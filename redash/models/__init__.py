import calendar
import datetime
import logging
import numbers
import time

import pytz
from sqlalchemy import UniqueConstraint, and_, cast, distinct, func, or_
from sqlalchemy.dialects.postgresql import ARRAY, DOUBLE_PRECISION, JSONB
from sqlalchemy.event import listens_for
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import (
    backref,
    contains_eager,
    joinedload,
    load_only,
    subqueryload,
)
from sqlalchemy.orm.exc import NoResultFound  # noqa: F401
from sqlalchemy_utils import generic_relationship
from sqlalchemy_utils.models import generic_repr
from sqlalchemy_utils.types import TSVectorType
from sqlalchemy_utils.types.encrypted.encrypted_type import FernetEngine

from redash import redis_connection, settings, utils
from redash.destinations import (
    get_configuration_schema_for_destination_type,
    get_destination,
)
from redash.metrics import database  # noqa: F401
from redash.models.base import (
    Column,
    GFKBase,
    SearchBaseQuery,
    db,
    gfk_type,
    key_type,
    primary_key,
)
from redash.models.changes import Change, ChangeTrackingMixin  # noqa
from redash.models.mixins import BelongsToOrgMixin, TimestampMixin
from redash.models.organizations import Organization
from redash.models.parameterized_query import (
    InvalidParameterError,
    ParameterizedQuery,
    QueryDetachedFromDataSourceError,
)
from redash.models.types import (
    Configuration,
    EncryptedConfiguration,
    JSONText,
    MutableDict,
    MutableList,
    json_cast_property,
)
from redash.models.users import (  # noqa
    AccessPermission,
    AnonymousUser,
    ApiUser,
    Group,
    User,
)
from redash.query_runner import (
    TYPE_BOOLEAN,
    TYPE_DATE,
    TYPE_DATETIME,
    BaseQueryRunner,
    get_configuration_schema_for_query_runner_type,
    get_query_runner,
    with_ssh_tunnel,
)
from redash.utils import (
    base_url,
    gen_query_hash,
    generate_token,
    json_dumps,
    json_loads,
    mustache_render,
    mustache_render_escape,
    sentry,
)
from redash.utils.configuration import ConfigurationContainer

logger = logging.getLogger(__name__)


class ScheduledQueriesExecutions:
    KEY_NAME = "sq:executed_at"

    def __init__(self):
        self.executions = {}

    def refresh(self):
        self.executions = redis_connection.hgetall(self.KEY_NAME)

    def update(self, query_id):
        redis_connection.hset(self.KEY_NAME, mapping={query_id: time.time()})

    def get(self, query_id):
        timestamp = self.executions.get(str(query_id))
        if timestamp:
            timestamp = utils.dt_from_timestamp(timestamp)

        return timestamp


scheduled_queries_executions = ScheduledQueriesExecutions()


@generic_repr("id", "name", "type", "org_id", "created_at")
class DataSource(BelongsToOrgMixin, db.Model):
    id = primary_key("DataSource")
    org_id = Column(key_type("Organization"), db.ForeignKey("organizations.id"))
    org = db.relationship(Organization, backref="data_sources")

    name = Column(db.String(255))
    type = Column(db.String(255))
    options = Column(
        "encrypted_options",
        ConfigurationContainer.as_mutable(
            EncryptedConfiguration(db.Text, settings.DATASOURCE_SECRET_KEY, FernetEngine)
        ),
    )
    queue_name = Column(db.String(255), default="queries")
    scheduled_queue_name = Column(db.String(255), default="scheduled_queries")
    created_at = Column(db.DateTime(True), default=db.func.now())

    data_source_groups = db.relationship("DataSourceGroup", back_populates="data_source", cascade="all")
    __tablename__ = "data_sources"
    __table_args__ = (
        db.Index("data_sources_org_id_name", "org_id", "name"),
        {"extend_existing": True},
    )

    def __eq__(self, other):
        return self.id == other.id

    def __hash__(self):
        return hash(self.id)

    def to_dict(self, all=False, with_permissions_for=None):
        d = {
            "id": self.id,
            "name": self.name,
            "type": self.type,
            "syntax": self.query_runner.syntax,
            "paused": self.paused,
            "pause_reason": self.pause_reason,
            "supports_auto_limit": self.query_runner.supports_auto_limit,
        }

        if all:
            schema = get_configuration_schema_for_query_runner_type(self.type)
            self.options.set_schema(schema)
            d["options"] = self.options.to_dict(mask_secrets=True)
            d["queue_name"] = self.queue_name
            d["scheduled_queue_name"] = self.scheduled_queue_name
            d["groups"] = self.groups

        if with_permissions_for is not None:
            d["view_only"] = (
                db.session.query(DataSourceGroup.view_only)
                .filter(
                    DataSourceGroup.group == with_permissions_for,
                    DataSourceGroup.data_source == self,
                )
                .one()[0]
            )

        return d

    def __str__(self):
        return str(self.name)

    @classmethod
    def create_with_group(cls, *args, **kwargs):
        data_source = cls(*args, **kwargs)
        data_source_group = DataSourceGroup(data_source=data_source, group=data_source.org.default_group)
        db.session.add_all([data_source, data_source_group])
        return data_source

    @classmethod
    def all(cls, org, group_ids=None):
        data_sources = cls.query.filter(cls.org == org).order_by(cls.id.asc())

        if group_ids:
            data_sources = data_sources.join(DataSourceGroup).filter(DataSourceGroup.group_id.in_(group_ids))

        return data_sources.distinct()

    @classmethod
    def get_by_id(cls, _id):
        return cls.query.filter(cls.id == _id).one()

    def delete(self):
        Query.query.filter(Query.data_source == self).update(dict(data_source_id=None, latest_query_data_id=None))
        QueryResult.query.filter(QueryResult.data_source == self).delete()
        res = db.session.delete(self)
        db.session.commit()

        redis_connection.delete(self._schema_key)

        return res

    def get_cached_schema(self):
        cache = redis_connection.get(self._schema_key)
        return json_loads(cache) if cache else None

    def get_schema(self, refresh=False):
        out_schema = None
        if not refresh:
            out_schema = self.get_cached_schema()

        if out_schema is None:
            query_runner = self.query_runner
            schema = query_runner.get_schema(get_stats=refresh)

            try:
                out_schema = self._sort_schema(schema)
            except Exception:
                logging.exception("Error sorting schema columns for data_source {}".format(self.id))
                out_schema = schema
            finally:
                ttl = int(datetime.timedelta(minutes=settings.SCHEMAS_REFRESH_SCHEDULE, days=7).total_seconds())
                redis_connection.set(self._schema_key, json_dumps(out_schema), ex=ttl)

        return out_schema

    def _sort_schema(self, schema):
        return [
            {"name": i["name"], "columns": sorted(i["columns"], key=lambda x: x["name"] if isinstance(x, dict) else x)}
            for i in sorted(schema, key=lambda x: x["name"])
        ]

    @property
    def _schema_key(self):
        return "data_source:schema:{}".format(self.id)

    @property
    def _pause_key(self):
        return "ds:{}:pause".format(self.id)

    @property
    def paused(self):
        return redis_connection.exists(self._pause_key)

    @property
    def pause_reason(self):
        return redis_connection.get(self._pause_key)

    def pause(self, reason=None):
        redis_connection.set(self._pause_key, reason or "")

    def resume(self):
        redis_connection.delete(self._pause_key)

    def add_group(self, group, view_only=False):
        dsg = DataSourceGroup(group=group, data_source=self, view_only=view_only)
        db.session.add(dsg)
        return dsg

    def remove_group(self, group):
        DataSourceGroup.query.filter(DataSourceGroup.group == group, DataSourceGroup.data_source == self).delete()
        db.session.commit()

    def update_group_permission(self, group, view_only):
        dsg = DataSourceGroup.query.filter(DataSourceGroup.group == group, DataSourceGroup.data_source == self).one()
        dsg.view_only = view_only
        db.session.add(dsg)
        return dsg

    @property
    def uses_ssh_tunnel(self):
        return self.options and "ssh_tunnel" in self.options

    @property
    def query_runner(self):
        query_runner = get_query_runner(self.type, self.options)

        if self.uses_ssh_tunnel:
            query_runner = with_ssh_tunnel(query_runner, self.options.get("ssh_tunnel"))

        return query_runner

    @classmethod
    def get_by_name(cls, name):
        return cls.query.filter(cls.name == name).one()

    # XXX examine call sites to see if a regular SQLA collection would work better
    @property
    def groups(self):
        groups = DataSourceGroup.query.filter(DataSourceGroup.data_source == self)
        return dict([(group.group_id, group.view_only) for group in groups])


@generic_repr("id", "data_source_id", "group_id", "view_only")
class DataSourceGroup(db.Model):
    # XXX drop id, use datasource/group as PK
    id = primary_key("DataSourceGroup")
    data_source_id = Column(key_type("DataSource"), db.ForeignKey("data_sources.id"))
    data_source = db.relationship(DataSource, back_populates="data_source_groups")
    group_id = Column(key_type("Group"), db.ForeignKey("groups.id"))
    group = db.relationship(Group, back_populates="data_sources")
    view_only = Column(db.Boolean, default=False)

    __tablename__ = "data_source_groups"
    __table_args__ = ({"extend_existing": True},)


@generic_repr("id", "org_id", "data_source_id", "query_hash", "runtime", "retrieved_at")
class QueryResult(db.Model, BelongsToOrgMixin):
    id = primary_key("QueryResult")
    org_id = Column(key_type("Organization"), db.ForeignKey("organizations.id"))
    org = db.relationship(Organization)
    data_source_id = Column(key_type("DataSource"), db.ForeignKey("data_sources.id"))
    data_source = db.relationship(DataSource, backref=backref("query_results"))
    query_hash = Column(db.String(32), index=True)
    query_text = Column("query", db.Text)
    data = Column(JSONText, nullable=True)
    runtime = Column(DOUBLE_PRECISION)
    retrieved_at = Column(db.DateTime(True))

    __tablename__ = "query_results"

    def __str__(self):
        return "%d | %s | %s" % (self.id, self.query_hash, self.retrieved_at)

    def to_dict(self):
        return {
            "id": self.id,
            "query_hash": self.query_hash,
            "query": self.query_text,
            "data": self.data,
            "data_source_id": self.data_source_id,
            "runtime": self.runtime,
            "retrieved_at": self.retrieved_at,
        }

    @classmethod
    def unused(cls, days=7):
        age_threshold = datetime.datetime.now() - datetime.timedelta(days=days)
        return (cls.query.filter(Query.id.is_(None), cls.retrieved_at < age_threshold).outerjoin(Query)).options(
            load_only("id")
        )

    @classmethod
    def get_latest(cls, data_source, query, max_age=0):
        query_hash = gen_query_hash(query)

        if max_age == -1 and settings.QUERY_RESULTS_EXPIRED_TTL_ENABLED:
            max_age = settings.QUERY_RESULTS_EXPIRED_TTL

        if max_age == -1:
            query = cls.query.filter(cls.query_hash == query_hash, cls.data_source == data_source)
        else:
            query = cls.query.filter(
                cls.query_hash == query_hash,
                cls.data_source == data_source,
                (
                    db.func.timezone("utc", cls.retrieved_at) + datetime.timedelta(seconds=max_age)
                    >= db.func.timezone("utc", db.func.now())
                ),
            )

        return query.order_by(cls.retrieved_at.desc()).first()

    @classmethod
    def store_result(cls, org, data_source, query_hash, query, data, run_time, retrieved_at):
        query_result = cls(
            org_id=org,
            query_hash=query_hash,
            query_text=query,
            runtime=run_time,
            data_source=data_source,
            retrieved_at=retrieved_at,
            data=data,
        )

        db.session.add(query_result)
        logging.info("Inserted query (%s) data; id=%s", query_hash, query_result.id)

        return query_result

    @property
    def groups(self):
        return self.data_source.groups


def should_schedule_next(previous_iteration, now, interval, time=None, day_of_week=None, failures=0):
    # if time exists then interval > 23 hours (82800s)
    # if day_of_week exists then interval > 6 days (518400s)
    if time is None:
        ttl = int(interval)
        next_iteration = previous_iteration + datetime.timedelta(seconds=ttl)
    else:
        hour, minute = time.split(":")
        hour, minute = int(hour), int(minute)

        # The following logic is needed for cases like the following:
        # - The query scheduled to run at 23:59.
        # - The scheduler wakes up at 00:01.
        # - Using naive implementation of comparing timestamps, it will skip the execution.
        normalized_previous_iteration = previous_iteration.replace(hour=hour, minute=minute)

        if normalized_previous_iteration > previous_iteration:
            previous_iteration = normalized_previous_iteration - datetime.timedelta(days=1)

        days_delay = int(interval) / 60 / 60 / 24

        days_to_add = 0
        if day_of_week is not None:
            days_to_add = list(calendar.day_name).index(day_of_week) - normalized_previous_iteration.weekday()

        next_iteration = (
            previous_iteration + datetime.timedelta(days=days_delay) + datetime.timedelta(days=days_to_add)
        ).replace(hour=hour, minute=minute)
    if failures:
        try:
            next_iteration += datetime.timedelta(minutes=2**failures)
        except OverflowError:
            return False
    return now > next_iteration


@gfk_type
@generic_repr(
    "id",
    "name",
    "query_hash",
    "version",
    "user_id",
    "org_id",
    "data_source_id",
    "query_hash",
    "last_modified_by_id",
    "is_archived",
    "is_draft",
    "schedule",
    "schedule_failures",
)
class Query(ChangeTrackingMixin, TimestampMixin, BelongsToOrgMixin, db.Model):
    id = primary_key("Query")
    version = Column(db.Integer, default=1)
    org_id = Column(key_type("Organization"), db.ForeignKey("organizations.id"))
    org = db.relationship(Organization, backref="queries")
    data_source_id = Column(key_type("DataSource"), db.ForeignKey("data_sources.id"), nullable=True)
    data_source = db.relationship(DataSource, backref="queries")
    latest_query_data_id = Column(key_type("QueryResult"), db.ForeignKey("query_results.id"), nullable=True)
    latest_query_data = db.relationship(QueryResult)
    name = Column(db.String(255))
    description = Column(db.String(4096), nullable=True)
    query_text = Column("query", db.Text)
    query_hash = Column(db.String(32))
    api_key = Column(db.String(40), default=lambda: generate_token(40))
    user_id = Column(key_type("User"), db.ForeignKey("users.id"))
    user = db.relationship(User, foreign_keys=[user_id])
    last_modified_by_id = Column(key_type("User"), db.ForeignKey("users.id"), nullable=True)
    last_modified_by = db.relationship(User, backref="modified_queries", foreign_keys=[last_modified_by_id])
    is_archived = Column(db.Boolean, default=False, index=True)
    is_draft = Column(db.Boolean, default=True, index=True)
    schedule = Column(MutableDict.as_mutable(JSONB), nullable=True)
    interval = json_cast_property(db.Integer, "schedule", "interval", default=0)
    schedule_failures = Column(db.Integer, default=0)
    visualizations = db.relationship("Visualization", cascade="all, delete-orphan")
    options = Column(MutableDict.as_mutable(JSONB), default={})
    search_vector = Column(
        TSVectorType(
            "id",
            "name",
            "description",
            "query",
            weights={"name": "A", "id": "B", "description": "C", "query": "D"},
        ),
        nullable=True,
    )
    tags = Column("tags", MutableList.as_mutable(ARRAY(db.Unicode)), nullable=True)

    query_class = SearchBaseQuery
    __tablename__ = "queries"
    __mapper_args__ = {"version_id_col": version, "version_id_generator": False}

    def __str__(self):
        return str(self.id)

    def archive(self, user=None):
        db.session.add(self)
        self.is_archived = True
        self.schedule = None

        for vis in self.visualizations:
            for w in vis.widgets:
                db.session.delete(w)

        for a in self.alerts:
            db.session.delete(a)

        if user:
            self.record_changes(user)

    def regenerate_api_key(self):
        self.api_key = generate_token(40)

    @classmethod
    def create(cls, **kwargs):
        query = cls(**kwargs)
        db.session.add(
            Visualization(
                query_rel=query,
                name="Table",
                description="",
                type="TABLE",
                options={},
            )
        )
        return query

    @classmethod
    def all_queries(cls, group_ids, user_id=None, include_drafts=False, include_archived=False):
        query_ids = (
            db.session.query(distinct(cls.id))
            .join(DataSourceGroup, Query.data_source_id == DataSourceGroup.data_source_id)
            .filter(Query.is_archived.is_(include_archived))
            .filter(DataSourceGroup.group_id.in_(group_ids))
        )
        queries = (
            cls.query.options(
                joinedload(Query.user),
                joinedload(Query.latest_query_data).load_only("runtime", "retrieved_at"),
            )
            .filter(cls.id.in_(query_ids))
            # Adding outer joins to be able to order by relationship
            .outerjoin(User, User.id == Query.user_id)
            .outerjoin(QueryResult, QueryResult.id == Query.latest_query_data_id)
            .options(contains_eager(Query.user), contains_eager(Query.latest_query_data))
        )

        if not include_drafts:
            queries = queries.filter(or_(Query.is_draft.is_(False), Query.user_id == user_id))
        return queries

    @classmethod
    def favorites(cls, user, base_query=None):
        if base_query is None:
            base_query = cls.all_queries(user.group_ids, user.id, include_drafts=True)
        return base_query.join(
            (
                Favorite,
                and_(Favorite.object_type == "Query", Favorite.object_id == Query.id),
            )
        ).filter(Favorite.user_id == user.id)

    @classmethod
    def all_tags(cls, user, include_drafts=False):
        queries = cls.all_queries(group_ids=user.group_ids, user_id=user.id, include_drafts=include_drafts)

        tag_column = func.unnest(cls.tags).label("tag")
        usage_count = func.count(1).label("usage_count")

        query = (
            db.session.query(tag_column, usage_count)
            .group_by(tag_column)
            .filter(Query.id.in_(queries.options(load_only("id"))))
            .order_by(usage_count.desc())
        )
        return query

    @classmethod
    def by_user(cls, user):
        return cls.all_queries(user.group_ids, user.id).filter(Query.user == user)

    @classmethod
    def by_api_key(cls, api_key):
        return cls.query.filter(cls.api_key == api_key).one()

    @classmethod
    def past_scheduled_queries(cls):
        now = utils.utcnow()
        queries = Query.query.filter(func.jsonb_typeof(Query.schedule) != "null").order_by(Query.id)
        return [
            query
            for query in queries
            if "until" in query.schedule
            and query.schedule["until"] is not None
            and pytz.utc.localize(datetime.datetime.strptime(query.schedule["until"], "%Y-%m-%d")) <= now
        ]

    @classmethod
    def outdated_queries(cls):
        queries = (
            Query.query.options(joinedload(Query.latest_query_data).load_only("retrieved_at"))
            .filter(func.jsonb_typeof(Query.schedule) != "null")
            .order_by(Query.id)
            .all()
        )

        now = utils.utcnow()
        outdated_queries = {}
        scheduled_queries_executions.refresh()

        for query in queries:
            try:
                if query.schedule.get("disabled"):
                    continue

                if query.schedule["until"]:
                    schedule_until = pytz.utc.localize(datetime.datetime.strptime(query.schedule["until"], "%Y-%m-%d"))

                    if schedule_until <= now:
                        continue

                retrieved_at = scheduled_queries_executions.get(query.id) or (
                    query.latest_query_data and query.latest_query_data.retrieved_at
                )

                if should_schedule_next(
                    retrieved_at or now,
                    now,
                    query.schedule["interval"],
                    query.schedule["time"],
                    query.schedule["day_of_week"],
                    query.schedule_failures,
                ):
                    key = "{}:{}".format(query.query_hash, query.data_source_id)
                    outdated_queries[key] = query
            except Exception as e:
                query.schedule["disabled"] = True
                db.session.commit()

                message = (
                    "Could not determine if query %d is outdated due to %s. The schedule for this query has been disabled."
                    % (query.id, repr(e))
                )
                logging.info(message)
                sentry.capture_exception(type(e)(message).with_traceback(e.__traceback__))

        return list(outdated_queries.values())

    @classmethod
    def search(
        cls,
        term,
        group_ids,
        user_id=None,
        include_drafts=False,
        limit=None,
        include_archived=False,
        multi_byte_search=False,
    ):
        all_queries = cls.all_queries(
            group_ids,
            user_id=user_id,
            include_drafts=include_drafts,
            include_archived=include_archived,
        )

        if multi_byte_search:
            # Since tsvector doesn't work well with CJK languages, use `ilike` too
            pattern = "%{}%".format(term)
            return (
                all_queries.filter(or_(cls.name.ilike(pattern), cls.description.ilike(pattern)))
                .order_by(Query.id)
                .limit(limit)
            )

        # sort the result using the weight as defined in the search vector column
        return all_queries.search(term, sort=True).limit(limit)

    @classmethod
    def search_by_user(cls, term, user, limit=None, multi_byte_search=False):
        if multi_byte_search:
            # Since tsvector doesn't work well with CJK languages, use `ilike` too
            pattern = "%{}%".format(term)
            return (
                cls.by_user(user)
                .filter(or_(cls.name.ilike(pattern), cls.description.ilike(pattern)))
                .order_by(Query.id)
                .limit(limit)
            )

        return cls.by_user(user).search(term, sort=True).limit(limit)

    @classmethod
    def recent(cls, group_ids, user_id=None, limit=20):
        query = (
            cls.query.filter(Event.created_at > (db.func.current_date() - 7))
            .join(Event, Query.id == Event.object_id.cast(db.Integer))
            .join(DataSourceGroup, Query.data_source_id == DataSourceGroup.data_source_id)
            .filter(
                Event.action.in_(["edit", "execute", "edit_name", "edit_description", "view_source"]),
                Event.object_id is not None,
                Event.object_type == "query",
                DataSourceGroup.group_id.in_(group_ids),
                or_(Query.is_draft.is_(False), Query.user_id is user_id),
                Query.is_archived.is_(False),
            )
            .group_by(Event.object_id, Query.id)
            .order_by(db.desc(db.func.count(0)))
        )

        if user_id:
            query = query.filter(Event.user_id == user_id)

        query = query.limit(limit)

        return query

    @classmethod
    def get_by_id(cls, _id):
        return cls.query.filter(cls.id == _id).one()

    @classmethod
    def all_groups_for_query_ids(cls, query_ids):
        query = """SELECT group_id, view_only
                   FROM queries
                   JOIN data_source_groups ON queries.data_source_id = data_source_groups.data_source_id
                   WHERE queries.id in :ids"""

        return db.session.execute(query, {"ids": tuple(query_ids)}).fetchall()

    @classmethod
    def update_latest_result(cls, query_result):
        # TODO: Investigate how big an impact this select-before-update makes.
        queries = Query.query.filter(
            Query.query_hash == query_result.query_hash,
            Query.data_source == query_result.data_source,
            Query.is_archived.is_(False),
        )

        for q in queries:
            q.latest_query_data = query_result
            # don't auto-update the updated_at timestamp
            q.skip_updated_at = True
            db.session.add(q)

        query_ids = [q.id for q in queries]
        logging.info(
            "Updated %s queries with result (%s).",
            len(query_ids),
            query_result.query_hash,
        )

        return query_ids

    def fork(self, user):
        forked_list = [
            "org",
            "data_source",
            "latest_query_data",
            "description",
            "query_text",
            "query_hash",
            "options",
            "tags",
        ]
        kwargs = {a: getattr(self, a) for a in forked_list}

        # Query.create will add default TABLE visualization, so use constructor to create bare copy of query
        forked_query = Query(name="Copy of (#{}) {}".format(self.id, self.name), user=user, **kwargs)

        for v in sorted(self.visualizations, key=lambda v: v.id):
            forked_v = v.copy()
            forked_v["query_rel"] = forked_query
            fv = Visualization(**forked_v)  # it will magically add it to `forked_query.visualizations`
            db.session.add(fv)

        db.session.add(forked_query)
        return forked_query

    @property
    def runtime(self):
        return self.latest_query_data.runtime

    @property
    def retrieved_at(self):
        return self.latest_query_data.retrieved_at

    @property
    def groups(self):
        if self.data_source is None:
            return {}

        return self.data_source.groups

    @hybrid_property
    def lowercase_name(self):
        "Optional property useful for sorting purposes."
        return self.name.lower()

    @lowercase_name.expression
    def lowercase_name(cls):
        "The SQLAlchemy expression for the property above."
        return func.lower(cls.name)

    @property
    def parameters(self):
        return self.options.get("parameters", [])

    @property
    def parameterized(self):
        return ParameterizedQuery(self.query_text, self.parameters, self.org)

    @property
    def dashboard_api_keys(self):
        query = """SELECT api_keys.api_key
                   FROM api_keys
                   JOIN dashboards ON object_id = dashboards.id
                   JOIN widgets ON dashboards.id = widgets.dashboard_id
                   JOIN visualizations ON widgets.visualization_id = visualizations.id
                   WHERE object_type='dashboards'
                     AND active=true
                     AND visualizations.query_id = :id"""

        api_keys = db.session.execute(query, {"id": self.id}).fetchall()
        return [api_key[0] for api_key in api_keys]

    def update_query_hash(self):
        should_apply_auto_limit = self.options.get("apply_auto_limit", False) if self.options else False
        query_runner = self.data_source.query_runner if self.data_source else BaseQueryRunner({})
        query_text = self.query_text

        parameters_dict = {p["name"]: p.get("value") for p in self.parameters} if self.options else {}
        if any(parameters_dict):
            try:
                query_text = self.parameterized.apply(parameters_dict).query
            except InvalidParameterError as e:
                logging.info(f"Unable to update hash for query {self.id} because of invalid parameters: {str(e)}")
            except QueryDetachedFromDataSourceError as e:
                logging.info(
                    f"Unable to update hash for query {self.id} because of dropdown query {e.query_id} is unattached from datasource"
                )

        self.query_hash = query_runner.gen_query_hash(query_text, should_apply_auto_limit)


@listens_for(Query, "before_insert")
@listens_for(Query, "before_update")
def receive_before_insert_update(mapper, connection, target):
    target.update_query_hash()


@listens_for(Query.user_id, "set")
def query_last_modified_by(target, val, oldval, initiator):
    target.last_modified_by_id = val


@generic_repr("id", "object_type", "object_id", "user_id", "org_id")
class Favorite(TimestampMixin, db.Model):
    id = primary_key("Favorite")
    org_id = Column(key_type("Organization"), db.ForeignKey("organizations.id"))

    object_type = Column(db.Unicode(255))
    object_id = Column(key_type("Favorite"))
    object = generic_relationship(object_type, object_id)

    user_id = Column(key_type("User"), db.ForeignKey("users.id"))
    user = db.relationship(User, backref="favorites")

    __tablename__ = "favorites"
    __table_args__ = (UniqueConstraint("object_type", "object_id", "user_id", name="unique_favorite"),)

    @classmethod
    def is_favorite(cls, user, object):
        return cls.query.filter(cls.object == object, cls.user_id == user).count() > 0

    @classmethod
    def are_favorites(cls, user, objects):
        objects = list(objects)
        if not objects:
            return []

        object_type = str(objects[0].__class__.__name__)
        return [
            fav.object_id
            for fav in cls.query.filter(
                cls.object_id.in_([o.id for o in objects]),
                cls.object_type == object_type,
                cls.user_id == user,
            )
        ]


OPERATORS = {
    ">": lambda v, t: v > t,
    ">=": lambda v, t: v >= t,
    "<": lambda v, t: v < t,
    "<=": lambda v, t: v <= t,
    "==": lambda v, t: v == t,
    "!=": lambda v, t: v != t,
    # backward compatibility
    "greater than": lambda v, t: v > t,
    "less than": lambda v, t: v < t,
    "equals": lambda v, t: v == t,
}


def next_state(op, value, threshold):
    if isinstance(value, bool):
        # If it's a boolean cast to string and lower case, because upper cased
        # boolean value is Python specific and most likely will be confusing to
        # users.
        value = str(value).lower()
    else:
        try:
            value = float(value)
            value_is_number = True
        except ValueError:
            value_is_number = isinstance(value, numbers.Number)

        if value_is_number:
            try:
                threshold = float(threshold)
            except ValueError:
                return Alert.UNKNOWN_STATE
        else:
            value = str(value)

    if op(value, threshold):
        new_state = Alert.TRIGGERED_STATE
    elif not value_is_number and op not in [OPERATORS.get("!="), OPERATORS.get("=="), OPERATORS.get("equals")]:
        new_state = Alert.UNKNOWN_STATE
    else:
        new_state = Alert.OK_STATE

    return new_state


@generic_repr("id", "name", "query_id", "user_id", "state", "last_triggered_at", "rearm")
class Alert(TimestampMixin, BelongsToOrgMixin, db.Model):
    UNKNOWN_STATE = "unknown"
    OK_STATE = "ok"
    TRIGGERED_STATE = "triggered"
    TEST_STATE = "test"

    id = primary_key("Alert")
    name = Column(db.String(255))
    query_id = Column(key_type("Query"), db.ForeignKey("queries.id"))
    query_rel = db.relationship(Query, backref=backref("alerts", cascade="all"))
    user_id = Column(key_type("User"), db.ForeignKey("users.id"))
    user = db.relationship(User, backref="alerts")
    options = Column(MutableDict.as_mutable(JSONB), nullable=True)
    state = Column(db.String(255), default=UNKNOWN_STATE)
    subscriptions = db.relationship("AlertSubscription", cascade="all, delete-orphan")
    last_triggered_at = Column(db.DateTime(True), nullable=True)
    rearm = Column(db.Integer, nullable=True)

    __tablename__ = "alerts"

    @classmethod
    def all(cls, group_ids):
        return (
            cls.query.options(joinedload(Alert.user), joinedload(Alert.query_rel))
            .join(Query)
            .join(DataSourceGroup, DataSourceGroup.data_source_id == Query.data_source_id)
            .filter(DataSourceGroup.group_id.in_(group_ids))
        )

    @classmethod
    def get_by_id_and_org(cls, object_id, org):
        return super(Alert, cls).get_by_id_and_org(object_id, org, Query)

    def evaluate(self):
        data = self.query_rel.latest_query_data.data

        if data["rows"] and self.options["column"] in data["rows"][0]:
            op = OPERATORS.get(self.options["op"], lambda v, t: False)

            if "selector" not in self.options:
                selector = "first"
            else:
                selector = self.options["selector"]

            try:
                if selector == "max":
                    max_val = float("-inf")
                    for i in range(len(data["rows"])):
                        max_val = max(max_val, float(data["rows"][i][self.options["column"]]))
                    value = max_val
                elif selector == "min":
                    min_val = float("inf")
                    for i in range(len(data["rows"])):
                        min_val = min(min_val, float(data["rows"][i][self.options["column"]]))
                    value = min_val
                else:
                    value = data["rows"][0][self.options["column"]]

            except ValueError:
                return self.UNKNOWN_STATE

            threshold = self.options["value"]

            new_state = next_state(op, value, threshold)
        else:
            new_state = self.UNKNOWN_STATE

        return new_state

    def subscribers(self):
        return User.query.join(AlertSubscription).filter(AlertSubscription.alert == self)

    def render_template(self, template):
        if template is None:
            return ""

        data = self.query_rel.latest_query_data.data
        host = base_url(self.query_rel.org)

        col_name = self.options["column"]
        if data["rows"] and col_name in data["rows"][0]:
            result_value = data["rows"][0][col_name]
        else:
            result_value = None

        result_table = []  # A two-dimensional array which can rendered as a table in Mustache
        for row in data["rows"]:
            result_table.append([row[col["name"]] for col in data["columns"]])
        context = {
            "ALERT_NAME": self.name,
            "ALERT_URL": "{host}/alerts/{alert_id}".format(host=host, alert_id=self.id),
            "ALERT_STATUS": self.state.upper(),
            "ALERT_SELECTOR": self.options["selector"],
            "ALERT_CONDITION": self.options["op"],
            "ALERT_THRESHOLD": self.options["value"],
            "QUERY_NAME": self.query_rel.name,
            "QUERY_URL": "{host}/queries/{query_id}".format(host=host, query_id=self.query_rel.id),
            "QUERY_RESULT_VALUE": result_value,
            "QUERY_RESULT_ROWS": data["rows"],
            "QUERY_RESULT_COLS": data["columns"],
            "QUERY_RESULT_TABLE": result_table,
        }
        return mustache_render_escape(template, context)

    @property
    def custom_body(self):
        template = self.options.get("custom_body", self.options.get("template"))
        return self.render_template(template)

    @property
    def custom_subject(self):
        template = self.options.get("custom_subject")
        return self.render_template(template)

    @property
    def groups(self):
        return self.query_rel.groups

    @property
    def muted(self):
        return self.options.get("muted", False)


def generate_slug(ctx):
    slug = utils.slugify(ctx.current_parameters["name"])
    tries = 1
    while Dashboard.query.filter(Dashboard.slug == slug).first() is not None:
        slug = utils.slugify(ctx.current_parameters["name"]) + "_" + str(tries)
        tries += 1
    return slug


@gfk_type
@generic_repr("id", "name", "slug", "user_id", "org_id", "version", "is_archived", "is_draft")
class Dashboard(ChangeTrackingMixin, TimestampMixin, BelongsToOrgMixin, db.Model):
    id = primary_key("Dashboard")
    version = Column(db.Integer)
    org_id = Column(key_type("Organization"), db.ForeignKey("organizations.id"))
    org = db.relationship(Organization, backref="dashboards")
    slug = Column(db.String(140), index=True, default=generate_slug)
    name = Column(db.String(100))
    user_id = Column(key_type("User"), db.ForeignKey("users.id"))
    user = db.relationship(User)
    # layout is no longer used, but kept so we know how to render old dashboards.
    layout = Column(MutableList.as_mutable(JSONB), default=[])
    dashboard_filters_enabled = Column(db.Boolean, default=False)
    is_archived = Column(db.Boolean, default=False, index=True)
    is_draft = Column(db.Boolean, default=True, index=True)
    widgets = db.relationship("Widget", backref="dashboard", lazy="dynamic")
    tags = Column("tags", MutableList.as_mutable(ARRAY(db.Unicode)), nullable=True)
    options = Column(MutableDict.as_mutable(JSONB), default={})

    __tablename__ = "dashboards"
    __mapper_args__ = {"version_id_col": version}

    def __str__(self):
        return "%s=%s" % (self.id, self.name)

    @property
    def name_as_slug(self):
        return utils.slugify(self.name)

    @classmethod
    def all(cls, org, group_ids, user_id):
        query = (
            Dashboard.query.options(joinedload(Dashboard.user).load_only("id", "name", "details", "email"))
            .distinct(cls.lowercase_name, Dashboard.created_at, Dashboard.slug)
            .outerjoin(Widget)
            .outerjoin(Visualization)
            .outerjoin(Query)
            .outerjoin(DataSourceGroup, Query.data_source_id == DataSourceGroup.data_source_id)
            .filter(
                Dashboard.is_archived.is_(False),
                (DataSourceGroup.group_id.in_(group_ids) | (Dashboard.user_id == user_id)),
                Dashboard.org == org,
            )
        )

        query = query.filter(or_(Dashboard.user_id == user_id, Dashboard.is_draft.is_(False)))

        return query

    @classmethod
    def search(cls, org, groups_ids, user_id, search_term):
        # TODO: switch to FTS
        return cls.all(org, groups_ids, user_id).filter(cls.name.ilike("%{}%".format(search_term)))

    @classmethod
    def search_by_user(cls, term, user, limit=None):
        return cls.by_user(user).filter(cls.name.ilike("%{}%".format(term))).limit(limit)

    @classmethod
    def all_tags(cls, org, user):
        dashboards = cls.all(org, user.group_ids, user.id)

        tag_column = func.unnest(cls.tags).label("tag")
        usage_count = func.count(1).label("usage_count")

        query = (
            db.session.query(tag_column, usage_count)
            .group_by(tag_column)
            .filter(Dashboard.id.in_(dashboards.options(load_only("id"))))
            .order_by(usage_count.desc())
        )
        return query

    @classmethod
    def favorites(cls, user, base_query=None):
        if base_query is None:
            base_query = cls.all(user.org, user.group_ids, user.id)
        return base_query.join(
            (
                Favorite,
                and_(
                    Favorite.object_type == "Dashboard",
                    Favorite.object_id == Dashboard.id,
                ),
            )
        ).filter(Favorite.user_id == user.id)

    @classmethod
    def by_user(cls, user):
        return cls.all(user.org, user.group_ids, user.id).filter(Dashboard.user == user)

    @classmethod
    def get_by_slug_and_org(cls, slug, org):
        return cls.query.filter(cls.slug == slug, cls.org == org).one()

    def fork(self, user):
        forked_list = ["org", "layout", "dashboard_filters_enabled", "tags"]

        kwargs = {a: getattr(self, a) for a in forked_list}
        forked_dashboard = Dashboard(name="Copy of (#{}) {}".format(self.id, self.name), user=user, **kwargs)

        for w in self.widgets:
            forked_w = w.copy(forked_dashboard.id)
            fw = Widget(**forked_w)
            db.session.add(fw)

        forked_dashboard.slug = forked_dashboard.id
        db.session.add(forked_dashboard)
        return forked_dashboard

    @hybrid_property
    def lowercase_name(self):
        "Optional property useful for sorting purposes."
        return self.name.lower()

    @lowercase_name.expression
    def lowercase_name(cls):
        "The SQLAlchemy expression for the property above."
        return func.lower(cls.name)


@generic_repr("id", "name", "type", "query_id")
class Visualization(TimestampMixin, BelongsToOrgMixin, db.Model):
    id = primary_key("Visualization")
    type = Column(db.String(100))
    query_id = Column(key_type("Query"), db.ForeignKey("queries.id"))
    # query_rel and not query, because db.Model already has query defined.
    query_rel = db.relationship(Query, back_populates="visualizations")
    name = Column(db.String(255))
    description = Column(db.String(4096), nullable=True)
    options = Column(MutableDict.as_mutable(JSONB), nullable=True)

    __tablename__ = "visualizations"

    def __str__(self):
        return "%s %s" % (self.id, self.type)

    @classmethod
    def get_by_id_and_org(cls, object_id, org):
        return super(Visualization, cls).get_by_id_and_org(object_id, org, Query)

    def copy(self):
        return {
            "type": self.type,
            "name": self.name,
            "description": self.description,
            "options": self.options,
        }


@generic_repr("id", "visualization_id", "dashboard_id")
class Widget(TimestampMixin, BelongsToOrgMixin, db.Model):
    id = primary_key("Widget")
    visualization_id = Column(key_type("Visualization"), db.ForeignKey("visualizations.id"), nullable=True)
    visualization = db.relationship(Visualization, backref=backref("widgets", cascade="delete"))
    text = Column(db.Text, nullable=True)
    width = Column(db.Integer)
    options = Column(MutableDict.as_mutable(JSONB), default={})
    dashboard_id = Column(key_type("Dashboard"), db.ForeignKey("dashboards.id"), index=True)

    __tablename__ = "widgets"

    def __str__(self):
        return "%s" % self.id

    @classmethod
    def get_by_id_and_org(cls, object_id, org):
        return super(Widget, cls).get_by_id_and_org(object_id, org, Dashboard)

    def copy(self, dashboard_id):
        return {
            "options": self.options,
            "width": self.width,
            "text": self.text,
            "visualization_id": self.visualization_id,
            "dashboard_id": dashboard_id,
        }


@generic_repr("id", "object_type", "object_id", "action", "user_id", "org_id", "created_at")
class Event(db.Model):
    id = primary_key("Event")
    org_id = Column(key_type("Organization"), db.ForeignKey("organizations.id"))
    org = db.relationship(Organization, back_populates="events")
    user_id = Column(key_type("User"), db.ForeignKey("users.id"), nullable=True)
    user = db.relationship(User, backref="events")
    action = Column(db.String(255))
    object_type = Column(db.String(255))
    object_id = Column(db.String(255), nullable=True)
    additional_properties = Column(MutableDict.as_mutable(JSONB), nullable=True, default={})
    created_at = Column(db.DateTime(True), default=db.func.now())

    __tablename__ = "events"

    def __str__(self):
        return "%s,%s,%s,%s" % (
            self.user_id,
            self.action,
            self.object_type,
            self.object_id,
        )

    def to_dict(self):
        return {
            "org_id": self.org_id,
            "user_id": self.user_id,
            "action": self.action,
            "object_type": self.object_type,
            "object_id": self.object_id,
            "additional_properties": self.additional_properties,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def record(cls, event):
        org_id = event.pop("org_id")
        user_id = event.pop("user_id", None)
        action = event.pop("action")
        object_type = event.pop("object_type")
        object_id = event.pop("object_id", None)

        created_at = datetime.datetime.utcfromtimestamp(event.pop("timestamp"))

        event = cls(
            org_id=org_id,
            user_id=user_id,
            action=action,
            object_type=object_type,
            object_id=object_id,
            additional_properties=event,
            created_at=created_at,
        )
        db.session.add(event)
        return event


@generic_repr("id", "created_by_id", "org_id", "active")
class ApiKey(TimestampMixin, GFKBase, db.Model):
    id = primary_key("ApiKey")
    org_id = Column(key_type("Organization"), db.ForeignKey("organizations.id"))
    org = db.relationship(Organization)
    api_key = Column(db.String(255), index=True, default=lambda: generate_token(40))
    active = Column(db.Boolean, default=True)
    # 'object' provided by GFKBase
    object_id = Column(key_type("ApiKey"))
    created_by_id = Column(key_type("User"), db.ForeignKey("users.id"), nullable=True)
    created_by = db.relationship(User)

    __tablename__ = "api_keys"
    __table_args__ = (db.Index("api_keys_object_type_object_id", "object_type", "object_id"),)

    @classmethod
    def get_by_api_key(cls, api_key):
        return cls.query.filter(cls.api_key == api_key, cls.active.is_(True)).one()

    @classmethod
    def get_by_object(cls, object):
        return cls.query.filter(
            cls.object_type == object.__class__.__tablename__,
            cls.object_id == object.id,
            cls.active.is_(True),
        ).first()

    @classmethod
    def create_for_object(cls, object, user):
        k = cls(org=user.org, object=object, created_by=user)
        db.session.add(k)
        return k


@generic_repr("id", "name", "type", "user_id", "org_id", "created_at")
class NotificationDestination(BelongsToOrgMixin, db.Model):
    id = primary_key("NotificationDestination")
    org_id = Column(key_type("Organization"), db.ForeignKey("organizations.id"))
    org = db.relationship(Organization, backref="notification_destinations")
    user_id = Column(key_type("User"), db.ForeignKey("users.id"))
    user = db.relationship(User, backref="notification_destinations")
    name = Column(db.String(255))
    type = Column(db.String(255))
    options = Column(
        "encrypted_options",
        ConfigurationContainer.as_mutable(
            EncryptedConfiguration(db.Text, settings.DATASOURCE_SECRET_KEY, FernetEngine)
        ),
    )
    created_at = Column(db.DateTime(True), default=db.func.now())

    __tablename__ = "notification_destinations"
    __table_args__ = (db.Index("notification_destinations_org_id_name", "org_id", "name", unique=True),)

    def __str__(self):
        return str(self.name)

    def to_dict(self, all=False):
        d = {
            "id": self.id,
            "name": self.name,
            "type": self.type,
            "icon": self.destination.icon(),
        }

        if all:
            schema = get_configuration_schema_for_destination_type(self.type)
            self.options.set_schema(schema)
            d["options"] = self.options.to_dict(mask_secrets=True)

        return d

    @property
    def destination(self):
        return get_destination(self.type, self.options)

    @classmethod
    def all(cls, org):
        notification_destinations = cls.query.filter(cls.org == org).order_by(cls.id.asc())

        return notification_destinations

    def notify(self, alert, query, user, new_state, app, host, metadata):
        schema = get_configuration_schema_for_destination_type(self.type)
        self.options.set_schema(schema)
        return self.destination.notify(alert, query, user, new_state, app, host, metadata, self.options)


@generic_repr("id", "user_id", "destination_id", "alert_id")
class AlertSubscription(TimestampMixin, db.Model):
    id = primary_key("AlertSubscription")
    user_id = Column(key_type("User"), db.ForeignKey("users.id"))
    user = db.relationship(User)
    destination_id = Column(
        key_type("NotificationDestination"), db.ForeignKey("notification_destinations.id"), nullable=True
    )
    destination = db.relationship(NotificationDestination)
    alert_id = Column(key_type("Alert"), db.ForeignKey("alerts.id"))
    alert = db.relationship(Alert, back_populates="subscriptions")

    __tablename__ = "alert_subscriptions"
    __table_args__ = (
        db.Index(
            "alert_subscriptions_destination_id_alert_id",
            "destination_id",
            "alert_id",
            unique=True,
        ),
    )

    def to_dict(self):
        d = {"id": self.id, "user": self.user.to_dict(), "alert_id": self.alert_id}

        if self.destination:
            d["destination"] = self.destination.to_dict()

        return d

    @classmethod
    def all(cls, alert_id):
        return AlertSubscription.query.join(User).filter(AlertSubscription.alert_id == alert_id)

    def notify(self, alert, query, user, new_state, app, host, metadata):
            if self.destination:
                return self.destination.notify(alert, query, user, new_state, app, host, metadata)
            # User email subscription, so create an email destination object
            config = {"addresses": self.user.email}
            schema = get_configuration_schema_for_destination_type("email")
            options = ConfigurationContainer(config, schema)
            destination = get_destination("email", options)
            return destination.notify(alert, query, user, new_state, app, host, metadata, options)


@generic_repr("id", "trigger", "user_id", "org_id")
class QuerySnippet(TimestampMixin, db.Model, BelongsToOrgMixin):
    id = primary_key("QuerySnippet")
    org_id = Column(key_type("Organization"), db.ForeignKey("organizations.id"))
    org = db.relationship(Organization, backref="query_snippets")
    trigger = Column(db.String(255), unique=True)
    description = Column(db.Text)
    user_id = Column(key_type("User"), db.ForeignKey("users.id"))
    user = db.relationship(User, backref="query_snippets")
    snippet = Column(db.Text)

    __tablename__ = "query_snippets"

    @classmethod
    def all(cls, org):
        return cls.query.filter(cls.org == org)

    def to_dict(self):
        d = {
            "id": self.id,
            "trigger": self.trigger,
            "description": self.description,
            "snippet": self.snippet,
            "user": self.user.to_dict(),
            "updated_at": self.updated_at,
            "created_at": self.created_at,
        }

        return d


def init_db():
    default_org = Organization(name="Default", slug="default", settings={})
    admin_group = Group(
        name="admin",
        permissions=Group.ADMIN_PERMISSIONS,
        org=default_org,
        type=Group.BUILTIN_GROUP,
    )
    default_group = Group(
        name="default",
        permissions=Group.DEFAULT_PERMISSIONS,
        org=default_org,
        type=Group.BUILTIN_GROUP,
    )

    db.session.add_all([default_org, admin_group, default_group])
    # XXX remove after fixing User.group_ids
    db.session.commit()
    return default_org, admin_group, default_group
