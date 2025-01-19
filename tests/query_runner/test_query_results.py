import datetime
import decimal
import logging
import sqlite3
from unittest import TestCase

import mock
import pytest

from redash.query_runner.query_results import (
    CreateTableError,
    PermissionError,
    _load_query,
    create_table,
    extract_cached_query_ids,
    extract_query_ids,
    extract_query_params,
    fix_column_name,
    flatten,
    get_query_results,
    prepare_parameterized_query,
    replace_query_parameters,
)
from tests import BaseTestCase


class TestExtractQueryIds(TestCase):
    def test_works_with_simple_query(self):
        query = "SELECT 1"
        self.assertEqual([], extract_query_ids(query))

    def test_finds_queries_to_load(self):
        query = "SELECT * FROM query_123"
        self.assertEqual([123], extract_query_ids(query))

    def test_finds_queries_in_joins(self):
        query = "SELECT * FROM query_123 JOIN query_4566"
        self.assertEqual([123, 4566], extract_query_ids(query))

    def test_finds_queries_with_whitespace_characters(self):
        query = "SELECT * FROM    query_123 a JOIN\tquery_4566 b ON a.id=b.parent_id JOIN\r\nquery_78 c ON b.id=c.parent_id"
        self.assertEqual([123, 4566, 78], extract_query_ids(query))


class TestCreateTable(TestCase):
    def test_creates_table_with_colons_in_column_name(self):
        connection = sqlite3.connect(":memory:")
        results = {
            "columns": [{"name": "ga:newUsers"}, {"name": "test2"}],
            "rows": [{"ga:newUsers": 123, "test2": 2}],
        }
        table_name = "query_123"
        create_table(connection, table_name, results)
        connection.execute("SELECT 1 FROM query_123")

    def test_creates_table_with_double_quotes_in_column_name(self):
        connection = sqlite3.connect(":memory:")
        results = {
            "columns": [{"name": "ga:newUsers"}, {"name": '"test2"'}],
            "rows": [{"ga:newUsers": 123, '"test2"': 2}],
        }
        table_name = "query_123"
        create_table(connection, table_name, results)
        connection.execute("SELECT 1 FROM query_123")

    def test_creates_table(self):
        connection = sqlite3.connect(":memory:")
        results = {"columns": [{"name": "test1"}, {"name": "test2"}], "rows": []}
        table_name = "query_123"
        create_table(connection, table_name, results)
        connection.execute("SELECT 1 FROM query_123")

    def test_creates_table_with_missing_columns(self):
        connection = sqlite3.connect(":memory:")
        results = {
            "columns": [{"name": "test1"}, {"name": "test2"}],
            "rows": [{"test1": 1, "test2": 2}, {"test1": 3}],
        }
        table_name = "query_123"
        create_table(connection, table_name, results)
        connection.execute("SELECT 1 FROM query_123")

    def test_creates_table_with_spaces_in_column_name(self):
        connection = sqlite3.connect(":memory:")
        results = {
            "columns": [{"name": "two words"}, {"name": "test2"}],
            "rows": [{"two words": 1, "test2": 2}, {"test1": 3}],
        }
        table_name = "query_123"
        create_table(connection, table_name, results)
        connection.execute("SELECT 1 FROM query_123")

    def test_creates_table_with_dashes_in_column_name(self):
        connection = sqlite3.connect(":memory:")
        results = {
            "columns": [{"name": "two-words"}, {"name": "test2"}],
            "rows": [{"two-words": 1, "test2": 2}],
        }
        table_name = "query_123"
        create_table(connection, table_name, results)
        connection.execute("SELECT 1 FROM query_123")
        connection.execute('SELECT "two-words" FROM query_123')

    def test_creates_table_with_non_ascii_in_column_name(self):
        connection = sqlite3.connect(":memory:")
        results = {
            "columns": [{"name": "\xe4"}, {"name": "test2"}],
            "rows": [{"\xe4": 1, "test2": 2}],
        }
        table_name = "query_123"
        create_table(connection, table_name, results)
        connection.execute("SELECT 1 FROM query_123")

    def test_creates_table_with_decimal_and_timedelta_in_column_value(self):
        connection = sqlite3.connect(":memory:")
        results = {
            "columns": [{"name": "test1"}, {"name": "test2"}, {"name": "test3"}],
            "rows": [{"test1": 1, "test2": decimal.Decimal(2), "test3": datetime.timedelta(seconds=3)}],
        }
        table_name = "query_123"
        create_table(connection, table_name, results)
        connection.execute("SELECT 1 FROM query_123")

    def test_shows_meaningful_error_on_failure_to_create_table(self):
        connection = sqlite3.connect(":memory:")
        results = {"columns": [], "rows": []}
        table_name = "query_123"
        with pytest.raises(CreateTableError):
            create_table(connection, table_name, results)

    def test_loads_results(self):
        connection = sqlite3.connect(":memory:")
        rows = [{"test1": 1, "test2": "test"}, {"test1": 2, "test2": "test2"}]
        results = {"columns": [{"name": "test1"}, {"name": "test2"}], "rows": rows}
        table_name = "query_123"
        create_table(connection, table_name, results)
        self.assertEqual(len(list(connection.execute("SELECT * FROM query_123"))), 2)

    def test_loads_list_and_dict_results(self):
        connection = sqlite3.connect(":memory:")
        rows = [{"test1": [1, 2, 3]}, {"test2": {"a": "b"}}]
        results = {"columns": [{"name": "test1"}, {"name": "test2"}], "rows": rows}
        table_name = "query_123"
        create_table(connection, table_name, results)
        self.assertEqual(len(list(connection.execute("SELECT * FROM query_123"))), 2)


class TestGetQuery(BaseTestCase):
    # test query from different account
    def test_raises_exception_for_query_from_different_account(self):
        query = self.factory.create_query()
        user = self.factory.create_user(org=self.factory.create_org())

        self.assertRaises(PermissionError, lambda: _load_query(user, query.id))

    def test_raises_exception_for_query_with_different_groups(self):
        ds = self.factory.create_data_source(group=self.factory.create_group())
        query = self.factory.create_query(data_source=ds)
        user = self.factory.create_user()

        self.assertRaises(PermissionError, lambda: _load_query(user, query.id))

    def test_returns_query(self):
        query = self.factory.create_query()
        user = self.factory.create_user()

        loaded = _load_query(user, query.id)
        self.assertEqual(query, loaded)

    def test_returns_query_when_user_has_view_only_access(self):
        ds = self.factory.create_data_source(group=self.factory.org.default_group, view_only=True)
        query = self.factory.create_query(data_source=ds)
        user = self.factory.create_user()

        loaded = _load_query(user, query.id)
        self.assertEqual(query, loaded)


class TestExtractCachedQueryIds(TestCase):
    def test_works_with_simple_query(self):
        query = "SELECT 1"
        self.assertEqual([], extract_cached_query_ids(query))

    def test_finds_queries_to_load(self):
        query = "SELECT * FROM cached_query_123"
        self.assertEqual([123], extract_cached_query_ids(query))

    def test_finds_queries_in_joins(self):
        query = "SELECT * FROM cached_query_123 JOIN cached_query_4566"
        self.assertEqual([123, 4566], extract_cached_query_ids(query))

    def test_finds_queries_with_whitespace_characters(self):
        query = "SELECT * FROM    cached_query_123 a JOIN\tcached_query_4566 b ON a.id=b.parent_id JOIN\r\ncached_query_78 c ON b.id=c.parent_id"
        self.assertEqual([123, 4566, 78], extract_cached_query_ids(query))


class TestExtractParamQueryIds(TestCase):
    def test_works_with_simple_query(self):
        query = "SELECT 1"
        self.assertEqual([], extract_query_params(query))

    def test_ignores_non_param_queries(self):
        query = "SELECT * FROM query_123"
        self.assertEqual([], extract_query_params(query))

    def test_ignores_cached_queries_to_load(self):
        query = "SELECT * FROM cached_query_123"
        self.assertEqual([], extract_query_params(query))

    def test_finds_queries_to_load(self):
        query = "SELECT * FROM param_query_123_{token=test}"
        self.assertEqual([("123", "token=test")], extract_query_params(query))

    def test_finds_queries_in_joins(self):
        query = "SELECT * FROM param_query_123_{token1=test1} JOIN param_query_456_{token2=test2}"
        self.assertEqual([("123", "token1=test1"), ("456", "token2=test2")], extract_query_params(query))


class TestPrepareParameterizedQuery(TestCase):
    def test_param_query_replacement(self):
        result = prepare_parameterized_query("SELECT * FROM param_query_123_{token=test}", [("123", "token=test")])
        self.assertEqual("SELECT * FROM query_123_1c5f1acad40f99b968836273d74baa89", result)


class TestReplaceQueryParameters(TestCase):
    def test_replace_query_params(self):
        result = replace_query_parameters("SELECT '{{token1}}', '{{token2}}'", "token1=test1&token2=test2")
        self.assertEqual("SELECT 'test1', 'test2'", result)


class TestFixColumnName(TestCase):
    def test_fix_column_name(self):
        self.assertEqual('"a_b_c_d"', fix_column_name("a:b.c d"))


class TestGetQueryResult(BaseTestCase):
    def test_cached_query_result(self):
        query_result = self.factory.create_query_result()
        query = self.factory.create_query(latest_query_data=query_result)

        self.assertEqual(query_result.data, get_query_results(self.factory.user, query.id, True))

    def test_non_cached_query_result(self):
        query_result = self.factory.create_query_result()
        query = self.factory.create_query(latest_query_data=query_result)

        from redash.query_runner.pg import PostgreSQL

        with mock.patch.object(PostgreSQL, "run_query") as qr:
            query_result_data = {"columns": [], "rows": []}
            qr.return_value = (query_result_data, None)
            self.assertEqual(query_result_data, get_query_results(self.factory.user, query.id, False))


class TestFlatten(TestCase):
    def test_flatten_with_string(self):
        self.assertEqual(flatten("hello"), "hello")

    def test_flatten_with_integer(self):
        self.assertEqual(flatten(10), 10)

    def test_flatten_with_float(self):
        self.assertEqual(flatten(10.5), 10.5)

    def test_flatten_with_boolean(self):
        self.assertEqual(flatten(True), True)

    def test_flatten_with_decimal(self):
        self.assertEqual(flatten(decimal.Decimal("10.5")), 10.5)

    def test_flatten_with_date(self):
        date = datetime.date(2021, 1, 1)
        self.assertEqual(flatten(date), "2021-01-01")

    def test_flatten_with_time(self):
        time = datetime.time(12, 30)
        self.assertEqual(flatten(time), "12:30:00")

    def test_flatten_with_datetime(self):
        datetime_obj = datetime.datetime(2021, 1, 1, 12, 30)
        self.assertEqual(flatten(datetime_obj), "2021-01-01 12:30:00")

    def test_flatten_with_timedelta(self):
        timedelta_obj = datetime.timedelta(days=2)
        self.assertEqual(flatten(timedelta_obj), "2 days, 0:00:00")

    def test_flatten_with_list(self):
        self.assertEqual(flatten([1, 2, 3]), "[1, 2, 3]")

    def test_flatten_with_dictionary(self):
        self.assertEqual(flatten({"key": "value"}), '{"key": "value"}')

    def test_flatten_with_none(self):
        self.assertEqual(flatten(None), None)

    @pytest.fixture(autouse=True)
    def inject_fixtures(self, caplog):
        self._caplog = caplog

    def test_flatten_unhandled_type(self):
        class CustomType:
            pass

        instance = CustomType()
        with self._caplog.at_level(logging.DEBUG):
            result = flatten(instance)
            self.assertEqual(result, instance)
            self.assertIn("flatten() found unhandled type: %s" % str(type(instance)), self._caplog.records[0].message)
