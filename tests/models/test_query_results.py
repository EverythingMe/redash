import datetime

from redash import models
from redash.utils import utcnow
from tests import BaseTestCase


class QueryResultTest(BaseTestCase):
    def test_get_latest_returns_none_if_not_found(self):
        found_query_result = models.QueryResult.get_latest(self.factory.data_source, "SELECT 1", 60)
        self.assertIsNone(found_query_result)

    def test_get_latest_returns_when_found(self):
        qr = self.factory.create_query_result()
        found_query_result = models.QueryResult.get_latest(qr.data_source, qr.query_hash, 60)

        self.assertEqual(qr, found_query_result)

    def test_get_latest_doesnt_return_query_from_different_data_source(self):
        qr = self.factory.create_query_result()
        data_source = self.factory.create_data_source()
        found_query_result = models.QueryResult.get_latest(data_source, qr.query_hash, 60)

        self.assertIsNone(found_query_result)

    def test_get_latest_doesnt_return_if_ttl_expired(self):
        yesterday = utcnow() - datetime.timedelta(days=1)
        qr = self.factory.create_query_result(retrieved_at=yesterday)

        found_query_result = models.QueryResult.get_latest(qr.data_source, qr.query_hash, max_age=60)

        self.assertIsNone(found_query_result)

    def test_get_latest_returns_if_ttl_not_expired(self):
        yesterday = utcnow() - datetime.timedelta(seconds=30)
        qr = self.factory.create_query_result(retrieved_at=yesterday)

        found_query_result = models.QueryResult.get_latest(qr.data_source, qr.query_hash, max_age=120)

        self.assertEqual(found_query_result, qr)

    def test_get_latest_returns_the_most_recent_result(self):
        yesterday = utcnow() - datetime.timedelta(seconds=30)
        self.factory.create_query_result(retrieved_at=yesterday)
        qr = self.factory.create_query_result()

        found_query_result = models.QueryResult.get_latest(qr.data_source, qr.query_hash, 60)

        self.assertEqual(found_query_result.id, qr.id)

    def test_get_latest_returns_the_last_cached_result_for_negative_ttl(self):
        yesterday = utcnow() + datetime.timedelta(days=-100)
        self.factory.create_query_result(retrieved_at=yesterday)

        yesterday = utcnow() + datetime.timedelta(days=-1)
        qr = self.factory.create_query_result(retrieved_at=yesterday)
        found_query_result = models.QueryResult.get_latest(qr.data_source, qr.query_hash, -1)

        self.assertEqual(found_query_result.id, qr.id)

    def test_store_result_does_not_modify_query_update_at(self):
        original_updated_at = utcnow() - datetime.timedelta(hours=1)
        query = self.factory.create_query(updated_at=original_updated_at)

        models.QueryResult.store_result(
            query.org_id,
            query.data_source,
            query.query_hash,
            query.query_text,
            {},
            0,
            utcnow(),
        )

        self.assertEqual(original_updated_at, query.updated_at)
