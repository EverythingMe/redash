from mock import Mock, patch
from rq import Connection
from rq.exceptions import NoSuchJobError

from redash import models, rq_redis_connection
from redash.query_runner.pg import PostgreSQL
from redash.tasks import Job
from redash.tasks.queries.execution import (
    QueryExecutionError,
    enqueue_query,
    execute_query,
)
from tests import BaseTestCase


def fetch_job(*args, **kwargs):
    if any(args):
        job_id = args[0] if isinstance(args[0], str) else args[0].id
    else:
        job_id = create_job().id

    result = Mock()
    result.id = job_id
    result.is_cancelled = False

    return result


def create_job(*args, **kwargs):
    return Job(connection=rq_redis_connection)


@patch("redash.tasks.queries.execution.Job.fetch", side_effect=fetch_job)
@patch("redash.tasks.queries.execution.Queue.enqueue", side_effect=create_job)
class TestEnqueueTask(BaseTestCase):
    def test_multiple_enqueue_of_same_query(self, enqueue, _):
        query = self.factory.create_query()

        with Connection(rq_redis_connection):
            enqueue_query(
                query.query_text,
                query.query_hash,
                query.data_source,
                query.user_id,
                False,
                query,
                {"Username": "Arik", "query_id": query.id},
            )
            enqueue_query(
                query.query_text,
                query.query_hash,
                query.data_source,
                query.user_id,
                False,
                query,
                {"Username": "Arik", "query_id": query.id},
            )
            enqueue_query(
                query.query_text,
                query.query_hash,
                query.data_source,
                query.user_id,
                False,
                query,
                {"Username": "Arik", "query_id": query.id},
            )

        self.assertEqual(1, enqueue.call_count)

    def test_multiple_enqueue_of_expired_job(self, enqueue, fetch_job):
        query = self.factory.create_query()

        with Connection(rq_redis_connection):
            enqueue_query(
                query.query_text,
                query.query_hash,
                query.data_source,
                query.user_id,
                False,
                query,
                {"Username": "Arik", "query_id": query.id},
            )

            # "expire" the previous job
            fetch_job.side_effect = NoSuchJobError

            enqueue_query(
                query.query_text,
                query.query_hash,
                query.data_source,
                query.user_id,
                False,
                query,
                {"Username": "Arik", "query_id": query.id},
            )

        self.assertEqual(2, enqueue.call_count)

    def test_reenqueue_during_job_cancellation(self, enqueue, my_fetch_job):
        query = self.factory.create_query()

        with Connection(rq_redis_connection):
            enqueue_query(
                query.query_text,
                query.query_hash,
                query.data_source,
                query.user_id,
                False,
                query,
                {"Username": "Arik", "query_id": query.id},
            )

            # "cancel" the previous job
            def cancel_job(*args, **kwargs):
                job = fetch_job(*args, **kwargs)
                job.is_cancelled = True
                return job

            my_fetch_job.side_effect = cancel_job

            enqueue_query(
                query.query_text,
                query.query_hash,
                query.data_source,
                query.user_id,
                False,
                query,
                {"Username": "Arik", "query_id": query.id},
            )

        self.assertEqual(2, enqueue.call_count)

    @patch("redash.settings.dynamic_settings.query_time_limit", return_value=60)
    def test_limits_query_time(self, _, enqueue, __):
        query = self.factory.create_query()

        with Connection(rq_redis_connection):
            enqueue_query(
                query.query_text,
                query.query_hash,
                query.data_source,
                query.user_id,
                False,
                query,
                {"Username": "Arik", "query_id": query.id},
            )

        _, kwargs = enqueue.call_args
        self.assertEqual(60, kwargs.get("job_timeout"))

    def test_multiple_enqueue_of_different_query(self, enqueue, _):
        query1 = self.factory.create_query(query_text="SELECT 1")
        query2 = self.factory.create_query(query_text="SELECT 2")
        query3 = self.factory.create_query(query_text="SELECT 3")

        with Connection(rq_redis_connection):
            enqueue_query(
                query1.query_text,
                query1.query_hash,
                query1.data_source,
                query1.user_id,
                False,
                None,
                {"Username": "Arik", "query_id": query1.id},
            )
            enqueue_query(
                query2.query_text,
                query2.query_hash,
                query2.data_source,
                query2.user_id,
                False,
                None,
                {"Username": "Arik", "query_id": query2.id},
            )
            enqueue_query(
                query3.query_text,
                query3.query_hash,
                query3.data_source,
                query3.user_id,
                False,
                None,
                {"Username": "Arik", "query_id": query3.id},
            )

        self.assertEqual(3, enqueue.call_count)


@patch("redash.tasks.queries.execution.get_current_job", side_effect=fetch_job)
class QueryExecutorTests(BaseTestCase):
    def test_success(self, _):
        """
        ``execute_query`` invokes the query runner and stores a query result.
        """
        with patch.object(PostgreSQL, "run_query") as qr:
            query_result_data = {"columns": [], "rows": []}
            qr.return_value = (query_result_data, None)
            result_id = execute_query("SELECT 1, 2", self.factory.data_source.id, {})
            self.assertEqual(1, qr.call_count)
            result = models.QueryResult.query.get(result_id)
            self.assertEqual(result.data, query_result_data)

    def test_success_scheduled(self, _):
        """
        Scheduled queries remember their latest results.
        """
        q = self.factory.create_query(query_text="SELECT 1, 2", schedule={"interval": 300})
        with patch.object(PostgreSQL, "run_query") as qr:
            qr.return_value = (
                {
                    "columns": [
                        {"name": "_col0", "friendly_name": "_col0", "type": "integer"},
                        {"name": "_col1", "friendly_name": "_col1", "type": "integer"},
                    ],
                    "rows": [{"_col0": 1, "_col1": 2}],
                },
                None,
            )
            result_id = execute_query(
                "SELECT 1, 2",
                self.factory.data_source.id,
                {"query_id": q.id},
                scheduled_query_id=q.id,
            )
            q = models.Query.get_by_id(q.id)
            self.assertEqual(q.schedule_failures, 0)
            result = models.QueryResult.query.get(result_id)
            self.assertEqual(q.latest_query_data, result)

    def test_failure_scheduled(self, _):
        """
        Scheduled queries that fail have their failure recorded.
        """
        q = self.factory.create_query(query_text="SELECT 1, 2", schedule={"interval": 300})
        with patch.object(PostgreSQL, "run_query") as qr:
            qr.side_effect = ValueError("broken")

            result = execute_query(
                "SELECT 1, 2",
                self.factory.data_source.id,
                {"query_id": q.id},
                scheduled_query_id=q.id,
            )
            self.assertTrue(isinstance(result, QueryExecutionError))
            q = models.Query.get_by_id(q.id)
            self.assertEqual(q.schedule_failures, 1)

            result = execute_query(
                "SELECT 1, 2",
                self.factory.data_source.id,
                {"query_id": q.id},
                scheduled_query_id=q.id,
            )
            self.assertTrue(isinstance(result, QueryExecutionError))
            q = models.Query.get_by_id(q.id)
            self.assertEqual(q.schedule_failures, 2)

    def test_success_after_failure(self, _):
        """
        Query execution success resets the failure counter.
        """
        q = self.factory.create_query(query_text="SELECT 1, 2", schedule={"interval": 300})
        with patch.object(PostgreSQL, "run_query") as qr:
            qr.side_effect = ValueError("broken")
            result = execute_query(
                "SELECT 1, 2",
                self.factory.data_source.id,
                {"query_id": q.id},
                scheduled_query_id=q.id,
            )
            self.assertTrue(isinstance(result, QueryExecutionError))
            q = models.Query.get_by_id(q.id)
            self.assertEqual(q.schedule_failures, 1)

        with patch.object(PostgreSQL, "run_query") as qr:
            qr.return_value = (
                {
                    "columns": [
                        {"name": "_col0", "friendly_name": "_col0", "type": "integer"},
                        {"name": "_col1", "friendly_name": "_col1", "type": "integer"},
                    ],
                    "rows": [{"_col0": 1, "_col1": 2}],
                },
                None,
            )
            execute_query(
                "SELECT 1, 2",
                self.factory.data_source.id,
                {"query_id": q.id},
                scheduled_query_id=q.id,
            )
            q = models.Query.get_by_id(q.id)
            self.assertEqual(q.schedule_failures, 0)

    def test_adhoc_success_after_scheduled_failure(self, _):
        """
        Query execution success resets the failure counter, even if it runs as an adhoc query.
        """
        q = self.factory.create_query(query_text="SELECT 1, 2", schedule={"interval": 300})
        with patch.object(PostgreSQL, "run_query") as qr:
            qr.side_effect = ValueError("broken")
            result = execute_query(
                "SELECT 1, 2",
                self.factory.data_source.id,
                {"query_id": q.id},
                scheduled_query_id=q.id,
                user_id=self.factory.user.id,
            )
            self.assertTrue(isinstance(result, QueryExecutionError))
            q = models.Query.get_by_id(q.id)
            self.assertEqual(q.schedule_failures, 1)

        with patch.object(PostgreSQL, "run_query") as qr:
            qr.return_value = (
                {
                    "columns": [
                        {"name": "_col0", "friendly_name": "_col0", "type": "integer"},
                        {"name": "_col1", "friendly_name": "_col1", "type": "integer"},
                    ],
                    "rows": [{"_col0": 1, "_col1": 2}],
                },
                None,
            )
            execute_query(
                "SELECT 1, 2",
                self.factory.data_source.id,
                {"query_id": q.id},
                user_id=self.factory.user.id,
            )
            q = models.Query.get_by_id(q.id)
            self.assertEqual(q.schedule_failures, 0)
