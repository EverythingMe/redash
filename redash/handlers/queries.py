import sqlparse
from flask import jsonify, request, url_for
from flask_login import login_required
from flask_restful import abort
from funcy import partial
from sqlalchemy.orm.exc import StaleDataError

from redash import models, settings
from redash.authentication.org_resolving import current_org
from redash.handlers.base import (
    BaseResource,
    filter_by_tags,
    get_object_or_404,
    org_scoped_rule,
    paginate,
    routes,
)
from redash.handlers.base import order_results as _order_results
from redash.handlers.query_results import run_query
from redash.models.parameterized_query import ParameterizedQuery
from redash.permissions import (
    can_modify,
    not_view_only,
    require_access,
    require_admin_or_owner,
    require_object_modify_permission,
    require_permission,
    view_only,
)
from redash.serializers import QuerySerializer
from redash.utils import collect_parameters_from_request

# Ordering map for relationships
order_map = {
    "name": "lowercase_name",
    "-name": "-lowercase_name",
    "created_at": "created_at",
    "-created_at": "-created_at",
    "schedule": "interval",
    "-schedule": "-interval",
    "runtime": "query_results-runtime",
    "-runtime": "-query_results-runtime",
    "executed_at": "query_results-retrieved_at",
    "-executed_at": "-query_results-retrieved_at",
    "created_by": "users-name",
    "-created_by": "-users-name",
}

order_results = partial(_order_results, default_order="-created_at", allowed_orders=order_map)


@routes.route(org_scoped_rule("/api/queries/format"), methods=["POST"])
@login_required
def format_sql_query(org_slug=None):
    """
    Formats an SQL query using the Python ``sqlparse`` formatter.

    :<json string query: The SQL text to format
    :>json string query: Formatted SQL text
    """
    arguments = request.get_json(force=True)
    query = arguments.get("query", "")

    return jsonify({"query": sqlparse.format(query, **settings.SQLPARSE_FORMAT_OPTIONS)})


class QuerySearchResource(BaseResource):
    @require_permission("view_query")
    def get(self):
        """
        Search query text, names, and descriptions.

        :qparam string q: Search term
        :qparam number include_drafts: Whether to include draft in results

        Responds with a list of :ref:`query <query-response-label>` objects.
        """
        term = request.args.get("q", "")
        if not term:
            return []

        include_drafts = request.args.get("include_drafts") is not None

        self.record_event({"action": "search", "object_type": "query", "term": term})

        # this redirects to the new query list API that is aware of search
        new_location = url_for(
            "queries",
            q=term,
            org_slug=current_org.slug,
            drafts="true" if include_drafts else "false",
        )
        return {}, 301, {"Location": new_location}


class QueryRecentResource(BaseResource):
    @require_permission("view_query")
    def get(self):
        """
        Retrieve up to 10 queries recently modified by the user.

        Responds with a list of :ref:`query <query-response-label>` objects.
        """

        results = models.Query.by_user(self.current_user).order_by(models.Query.updated_at.desc()).limit(10)
        return QuerySerializer(results, with_last_modified_by=False, with_user=False).serialize()


class BaseQueryListResource(BaseResource):
    def get_queries(self, search_term):
        if search_term:
            results = models.Query.search(
                search_term,
                self.current_user.group_ids,
                self.current_user.id,
                include_drafts=True,
                multi_byte_search=current_org.get_setting("multi_byte_search_enabled"),
            )
        else:
            results = models.Query.all_queries(self.current_user.group_ids, self.current_user.id, include_drafts=True)
        return filter_by_tags(results, models.Query.tags)

    @require_permission("view_query")
    def get(self):
        """
        Retrieve a list of queries.

        :qparam number page_size: Number of queries to return per page
        :qparam number page: Page number to retrieve
        :qparam number order: Name of column to order by
        :qparam number q: Full text search term

        Responds with an array of :ref:`query <query-response-label>` objects.
        """
        # See if we want to do full-text search or just regular queries
        search_term = request.args.get("q", "")

        queries = self.get_queries(search_term)

        results = filter_by_tags(queries, models.Query.tags)

        # order results according to passed order parameter,
        # special-casing search queries where the database
        # provides an order by search rank
        ordered_results = order_results(results, fallback=not bool(search_term))

        page = request.args.get("page", 1, type=int)
        page_size = request.args.get("page_size", 25, type=int)

        response = paginate(
            ordered_results,
            page=page,
            page_size=page_size,
            serializer=QuerySerializer,
            with_stats=True,
            with_last_modified_by=False,
        )

        if search_term:
            self.record_event({"action": "search", "object_type": "query", "term": search_term})
        else:
            self.record_event({"action": "list", "object_type": "query"})

        return response


def require_access_to_dropdown_queries(user, query_def):
    parameters = query_def.get("options", {}).get("parameters", [])
    dropdown_query_ids = set([str(p["queryId"]) for p in parameters if p["type"] == "query"])

    if dropdown_query_ids:
        groups = models.Query.all_groups_for_query_ids(dropdown_query_ids)

        if len(groups) < len(dropdown_query_ids):
            abort(
                400,
                message="You are trying to associate a dropdown query that does not have a matching group. "
                "Please verify the dropdown query id you are trying to associate with this query.",
            )

        require_access(dict(groups), user, view_only)


class QueryListResource(BaseQueryListResource):
    @require_permission("create_query")
    def post(self):
        """
        Create a new query.

        :<json number data_source_id: The ID of the data source this query will run on
        :<json string query: Query text
        :<json string name:
        :<json string description:
        :<json string schedule: Schedule interval, in seconds, for repeated execution of this query
        :<json object options: Query options

        .. _query-response-label:

        :>json number id: Query ID
        :>json number latest_query_data_id: ID for latest output data from this query
        :>json string name:
        :>json string description:
        :>json string query: Query text
        :>json string query_hash: Hash of query text
        :>json string schedule: Schedule interval, in seconds, for repeated execution of this query
        :>json string api_key: Key for public access to this query's results.
        :>json boolean is_archived: Whether this query is displayed in indexes and search results or not.
        :>json boolean is_draft: Whether this query is a draft or not
        :>json string updated_at: Time of last modification, in ISO format
        :>json string created_at: Time of creation, in ISO format
        :>json number data_source_id: ID of the data source this query will run on
        :>json object options: Query options
        :>json number version: Revision version (for update conflict avoidance)
        :>json number user_id: ID of query creator
        :>json number last_modified_by_id: ID of user who last modified this query
        :>json string retrieved_at: Time when query results were last retrieved, in ISO format (may be null)
        :>json number runtime: Runtime of last query execution, in seconds (may be null)
        """
        query_def = request.get_json(force=True)
        data_source = models.DataSource.get_by_id_and_org(query_def.pop("data_source_id"), self.current_org)
        require_access(data_source, self.current_user, not_view_only)
        require_access_to_dropdown_queries(self.current_user, query_def)

        for field in [
            "id",
            "created_at",
            "api_key",
            "visualizations",
            "latest_query_data",
            "last_modified_by",
        ]:
            query_def.pop(field, None)

        query_def["query_text"] = query_def.pop("query")
        query_def["user"] = self.current_user
        query_def["data_source"] = data_source
        query_def["org"] = self.current_org
        query_def["is_draft"] = True
        query = models.Query.create(**query_def)
        models.db.session.add(query)
        models.db.session.commit()

        self.record_event({"action": "create", "object_id": query.id, "object_type": "query"})

        return QuerySerializer(query, with_visualizations=True).serialize()


class QueryArchiveResource(BaseQueryListResource):
    def get_queries(self, search_term):
        if search_term:
            return models.Query.search(
                search_term,
                self.current_user.group_ids,
                self.current_user.id,
                include_drafts=False,
                include_archived=True,
                multi_byte_search=current_org.get_setting("multi_byte_search_enabled"),
            )
        else:
            return models.Query.all_queries(
                self.current_user.group_ids,
                self.current_user.id,
                include_drafts=False,
                include_archived=True,
            )


class MyQueriesResource(BaseResource):
    @require_permission("view_query")
    def get(self):
        """
        Retrieve a list of queries created by the current user.

        :qparam number page_size: Number of queries to return per page
        :qparam number page: Page number to retrieve
        :qparam number order: Name of column to order by
        :qparam number search: Full text search term

        Responds with an array of :ref:`query <query-response-label>` objects.
        """
        search_term = request.args.get("q", "")
        if search_term:
            results = models.Query.search_by_user(
                search_term,
                self.current_user,
                multi_byte_search=current_org.get_setting("multi_byte_search_enabled"),
            )
        else:
            results = models.Query.by_user(self.current_user)

        results = filter_by_tags(results, models.Query.tags)

        # order results according to passed order parameter,
        # special-casing search queries where the database
        # provides an order by search rank
        ordered_results = order_results(results, fallback=not bool(search_term))

        page = request.args.get("page", 1, type=int)
        page_size = request.args.get("page_size", 25, type=int)
        return paginate(
            ordered_results,
            page,
            page_size,
            QuerySerializer,
            with_stats=True,
            with_last_modified_by=False,
        )


class QueryResource(BaseResource):
    @require_permission("edit_query")
    def post(self, query_id):
        """
        Modify a query.

        :param query_id: ID of query to update
        :<json number data_source_id: The ID of the data source this query will run on
        :<json string query: Query text
        :<json string name:
        :<json string description:
        :<json string schedule: Schedule interval, in seconds, for repeated execution of this query
        :<json object options: Query options

        Responds with the updated :ref:`query <query-response-label>` object.
        """
        query = get_object_or_404(models.Query.get_by_id_and_org, query_id, self.current_org)
        query_def = request.get_json(force=True)
        previous_query_user = query.user

        require_object_modify_permission(query, self.current_user)
        require_access_to_dropdown_queries(self.current_user, query_def)

        for field in [
            "id",
            "created_at",
            "api_key",
            "visualizations",
            "latest_query_data",
            "last_modified_by",
            "org",
        ]:
            query_def.pop(field, None)

        if "query" in query_def:
            query_def["query_text"] = query_def.pop("query")

        if "tags" in query_def:
            query_def["tags"] = [tag for tag in query_def["tags"] if tag]

        if "data_source_id" in query_def:
            data_source = models.DataSource.get_by_id_and_org(query_def["data_source_id"], self.current_org)
            require_access(data_source, self.current_user, not_view_only)

        if "user" in query_def:
            require_admin_or_owner(self.current_user.id)
            new_user = models.User.get_by_id(query_def["user"]["id"])
            if "data_source_id" in query_def:
                data_source = models.DataSource.get_by_id_and_org(query_def["data_source_id"], self.current_org)
                require_access(data_source, new_user, not_view_only)
            else:
                data_source = query.data_source
                require_access(data_source, new_user, not_view_only)
            query_def["user"] = new_user

        query_def["last_modified_by"] = self.current_user
        query_def["changed_by"] = self.current_user
        # SQLAlchemy handles the case where a concurrent transaction beats us
        # to the update. But we still have to make sure that we're not starting
        # out behind.
        if "version" in query_def and query_def["version"] != query.version:
            abort(409)

        try:
            self.update_model(query, query_def)
            if "user" in query_def and query_def["user"] != previous_query_user:
                models.AccessPermission.revoke(query, query_def["user"], "modify")
                models.AccessPermission.grant(query, "modify", previous_query_user, self.current_user)
            models.db.session.commit()
        except StaleDataError:
            abort(409)

        return QuerySerializer(query, with_visualizations=True).serialize()

    @require_permission("view_query")
    def get(self, query_id):
        """
        Retrieve a query.

        :param query_id: ID of query to fetch

        Responds with the :ref:`query <query-response-label>` contents.
        """
        q = get_object_or_404(models.Query.get_by_id_and_org, query_id, self.current_org)
        require_access(q, self.current_user, view_only)

        result = QuerySerializer(q, with_visualizations=True).serialize()
        result["can_edit"] = can_modify(q, self.current_user)

        self.record_event({"action": "view", "object_id": query_id, "object_type": "query"})

        return result

    # TODO: move to resource of its own? (POST /queries/{id}/archive)
    def delete(self, query_id):
        """
        Archives a query.

        :param query_id: ID of query to archive
        """
        query = get_object_or_404(models.Query.get_by_id_and_org, query_id, self.current_org)
        require_admin_or_owner(query.user_id)
        query.archive(self.current_user)
        models.db.session.commit()


class QueryRegenerateApiKeyResource(BaseResource):
    @require_permission("edit_query")
    def post(self, query_id):
        query = get_object_or_404(models.Query.get_by_id_and_org, query_id, self.current_org)
        require_admin_or_owner(query.user_id)
        query.regenerate_api_key()
        models.db.session.commit()

        self.record_event(
            {
                "action": "regnerate_api_key",
                "object_id": query_id,
                "object_type": "query",
            }
        )

        result = QuerySerializer(query).serialize()
        return result


class QueryForkResource(BaseResource):
    @require_permission("edit_query")
    def post(self, query_id):
        """
        Creates a new query, copying the query text from an existing one.

        :param query_id: ID of query to fork

        Responds with created :ref:`query <query-response-label>` object.
        """
        query = get_object_or_404(models.Query.get_by_id_and_org, query_id, self.current_org)
        require_access(query.data_source, self.current_user, not_view_only)
        forked_query = query.fork(self.current_user)
        models.db.session.commit()

        self.record_event({"action": "fork", "object_id": query_id, "object_type": "query"})

        return QuerySerializer(forked_query, with_visualizations=True).serialize()


class QueryRefreshResource(BaseResource):
    def post(self, query_id):
        """
        Execute a query, updating the query object with the results.

        :param query_id: ID of query to execute

        Responds with query task details.
        """
        # TODO: this should actually check for permissions, but because currently you can only
        # get here either with a user API key or a query one, we can just check whether it's
        # an api key (meaning this is a query API key, which only grants read access).
        if self.current_user.is_api_user():
            abort(403, message="Please use a user API key.")

        query = get_object_or_404(models.Query.get_by_id_and_org, query_id, self.current_org)
        require_access(query, self.current_user, not_view_only)

        parameter_values = collect_parameters_from_request(request.args)
        parameterized_query = ParameterizedQuery(query.query_text, org=self.current_org)
        should_apply_auto_limit = query.options.get("apply_auto_limit", False)
        return run_query(parameterized_query, parameter_values, query.data_source, query.id, should_apply_auto_limit)


class QueryTagsResource(BaseResource):
    def get(self):
        """
        Returns all query tags including those for drafts.
        """
        tags = models.Query.all_tags(self.current_user, include_drafts=True)
        return {"tags": [{"name": name, "count": count} for name, count in tags]}


class QueryFavoriteListResource(BaseResource):
    def get(self):
        search_term = request.args.get("q")

        if search_term:
            base_query = models.Query.search(
                search_term,
                self.current_user.group_ids,
                include_drafts=True,
                limit=None,
                multi_byte_search=current_org.get_setting("multi_byte_search_enabled"),
            )
            favorites = models.Query.favorites(self.current_user, base_query=base_query)
        else:
            favorites = models.Query.favorites(self.current_user)

        favorites = filter_by_tags(favorites, models.Query.tags)

        # order results according to passed order parameter,
        # special-casing search queries where the database
        # provides an order by search rank
        ordered_favorites = order_results(favorites, fallback=not bool(search_term))

        page = request.args.get("page", 1, type=int)
        page_size = request.args.get("page_size", 25, type=int)
        response = paginate(
            ordered_favorites,
            page,
            page_size,
            QuerySerializer,
            with_stats=True,
            with_last_modified_by=False,
        )

        self.record_event(
            {
                "action": "load_favorites",
                "object_type": "query",
                "params": {
                    "q": search_term,
                    "tags": request.args.getlist("tags"),
                    "page": page,
                },
            }
        )

        return response
