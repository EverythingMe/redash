import logging
import time

from rq.timeouts import JobTimeoutException

from redash import models, redis_connection, settings, statsd_client
from redash.models.parameterized_query import (
    InvalidParameterError,
    QueryDetachedFromDataSourceError,
)
from redash.monitor import rq_job_ids
from redash.tasks.failure_report import track_failure
from redash.utils import json_dumps, sentry
from redash.worker import get_job_logger, job

from .execution import enqueue_query

logger = get_job_logger(__name__)


def empty_schedules():
    logger.info("Deleting schedules of past scheduled queries...")

    queries = models.Query.past_scheduled_queries()
    for query in queries:
        query.schedule = None
    models.db.session.commit()

    logger.info("Deleted %d schedules.", len(queries))


def _should_refresh_query(query):
    if settings.FEATURE_DISABLE_REFRESH_QUERIES:
        logger.info("Disabled refresh queries.")
        return False
    if query.org.is_disabled:
        logger.debug("Skipping refresh of %s because org is disabled.", query.id)
        return False
    if query.data_source is None:
        logger.debug("Skipping refresh of %s because the datasource is none.", query.id)
        return False
    if query.data_source.paused:
        logger.debug(
            "Skipping refresh of %s because datasource - %s is paused (%s).",
            query.id,
            query.data_source.name,
            query.data_source.pause_reason,
        )
        return False
    return True


def _apply_default_parameters(query):
    parameters = {p["name"]: p.get("value") for p in query.parameters}
    if any(parameters):
        try:
            return query.parameterized.apply(parameters).query
        except InvalidParameterError as e:
            error = f"Skipping refresh of {query.id} because of invalid parameters: {str(e)}"
            track_failure(query, error)
            raise
        except QueryDetachedFromDataSourceError as e:
            error = (
                f"Skipping refresh of {query.id} because a related dropdown "
                f"query ({e.query_id}) is unattached to any datasource."
            )
            track_failure(query, error)
            raise
    else:
        return query.query_text


class RefreshQueriesError(Exception):
    pass


def _apply_auto_limit(query_text, query):
    should_apply_auto_limit = query.options.get("apply_auto_limit", False)
    return query.data_source.query_runner.apply_auto_limit(query_text, should_apply_auto_limit)


def refresh_queries():
    started_at = time.time()
    logger.info("Refreshing queries...")
    enqueued = []
    for query in models.Query.outdated_queries():
        if not _should_refresh_query(query):
            continue

        try:
            query_text = _apply_default_parameters(query)
            query_text = _apply_auto_limit(query_text, query)
            enqueue_query(
                query_text,
                query.data_source,
                query.user_id,
                scheduled_query=query,
                metadata={"query_id": query.id, "Username": query.user.get_actual_user()},
            )
            enqueued.append(query)
        except Exception as e:
            message = "Could not enqueue query %d due to %s" % (query.id, repr(e))
            logging.info(message)
            error = RefreshQueriesError(message).with_traceback(e.__traceback__)
            sentry.capture_exception(error)

    status = {
        "started_at": started_at,
        "outdated_queries_count": len(enqueued),
        "last_refresh_at": time.time(),
        "query_ids": json_dumps([q.id for q in enqueued]),
    }

    redis_connection.hset("redash:status", mapping=status)
    logger.info("Done refreshing queries: %s" % status)


def cleanup_query_results():
    """
    Job to cleanup unused query results -- such that no query links to them anymore, and older than
    settings.QUERY_RESULTS_CLEANUP_MAX_AGE (a week by default, so it's less likely to be open in someone's browser and be used).

    Each time the job deletes only settings.QUERY_RESULTS_CLEANUP_COUNT (100 by default) query results so it won't choke
    the database in case of many such results.
    """

    logger.info(
        "Running query results clean up (removing maximum of %d unused results, that are %d days old or more)",
        settings.QUERY_RESULTS_CLEANUP_COUNT,
        settings.QUERY_RESULTS_CLEANUP_MAX_AGE,
    )

    unused_query_results = models.QueryResult.unused(settings.QUERY_RESULTS_CLEANUP_MAX_AGE)
    deleted_count = models.QueryResult.query.filter(
        models.QueryResult.id.in_(unused_query_results.limit(settings.QUERY_RESULTS_CLEANUP_COUNT).subquery())
    ).delete(synchronize_session=False)
    models.db.session.commit()
    logger.info("Deleted %d unused query results.", deleted_count)


def remove_ghost_locks():
    """
    Removes query locks that reference a non existing RQ job.
    """
    keys = redis_connection.keys("query_hash_job:*")
    locks = {k: redis_connection.get(k) for k in keys}
    jobs = list(rq_job_ids())

    count = 0

    for lock, job_id in locks.items():
        if job_id not in jobs:
            redis_connection.delete(lock)
            count += 1

    logger.info("Locks found: {}, Locks removed: {}".format(len(locks), count))


@job("schemas")
def refresh_schema(data_source_id):
    ds = models.DataSource.get_by_id(data_source_id)
    logger.info("task=refresh_schema state=start ds_id=%s", ds.id)
    start_time = time.time()
    try:
        ds.get_schema(refresh=True)
        logger.info(
            "task=refresh_schema state=finished ds_id=%s runtime=%.2f",
            ds.id,
            time.time() - start_time,
        )
        statsd_client.incr("refresh_schema.success")
    except JobTimeoutException:
        logger.info(
            "task=refresh_schema state=timeout ds_id=%s runtime=%.2f",
            ds.id,
            time.time() - start_time,
        )
        statsd_client.incr("refresh_schema.timeout")
    except Exception:
        logger.warning("Failed refreshing schema for the data source: %s", ds.name, exc_info=1)
        statsd_client.incr("refresh_schema.error")
        logger.info(
            "task=refresh_schema state=failed ds_id=%s runtime=%.2f",
            ds.id,
            time.time() - start_time,
        )


def refresh_schemas():
    """
    Refreshes the data sources schemas.
    """
    blacklist = [int(ds_id) for ds_id in redis_connection.smembers("data_sources:schema:blacklist") if ds_id]
    global_start_time = time.time()

    logger.info("task=refresh_schemas state=start")

    for ds in models.DataSource.query:
        if ds.paused:
            logger.info(
                "task=refresh_schema state=skip ds_id=%s reason=paused(%s)",
                ds.id,
                ds.pause_reason,
            )
        elif ds.id in blacklist:
            logger.info("task=refresh_schema state=skip ds_id=%s reason=blacklist", ds.id)
        elif ds.org.is_disabled:
            logger.info("task=refresh_schema state=skip ds_id=%s reason=org_disabled", ds.id)
        else:
            refresh_schema.delay(ds.id)

    logger.info(
        "task=refresh_schemas state=finish total_runtime=%.2f",
        time.time() - global_start_time,
    )
