import datetime
import decimal
import hashlib
import logging
import re
import sqlite3
from urllib.parse import parse_qs

from redash import models
from redash.permissions import has_access, view_only
from redash.query_runner import (
    TYPE_STRING,
    BaseQueryRunner,
    JobTimeoutException,
    guess_type,
    register,
)
from redash.utils import json_dumps

logger = logging.getLogger(__name__)


class PermissionError(Exception):
    pass


class CreateTableError(Exception):
    pass


def extract_query_params(query):
    return re.findall(r"(?:join|from)\s+param_query_(\d+)_{([^}]+)}", query, re.IGNORECASE)


def extract_query_ids(query):
    queries = re.findall(r"(?:join|from)\s+query_(\d+)", query, re.IGNORECASE)
    return [int(q) for q in queries]


def extract_cached_query_ids(query):
    queries = re.findall(r"(?:join|from)\s+cached_query_(\d+)", query, re.IGNORECASE)
    return [int(q) for q in queries]


def _load_query(user, query_id):
    query = models.Query.get_by_id(query_id)

    if user.org_id != query.org_id:
        raise PermissionError("Query id {} not found.".format(query.id))

    # TODO: this duplicates some of the logic we already have in the redash.handlers.query_results.
    # We should merge it so it's consistent.
    if not has_access(query.data_source, user, view_only):
        raise PermissionError("You do not have access to query id {}.".format(query.id))

    return query


def replace_query_parameters(query_text, params):
    qs = parse_qs(params)
    for key, value in qs.items():
        query_text = query_text.replace("{{{{{my_key}}}}}".format(my_key=key), value[0])
    return query_text


def get_query_results(user, query_id, bring_from_cache, params=None):
    query = _load_query(user, query_id)
    if bring_from_cache:
        if query.latest_query_data_id is not None:
            results = query.latest_query_data.data
        else:
            raise Exception("No cached result available for query {}.".format(query.id))
    else:
        query_text = query.query_text
        if params is not None:
            query_text = replace_query_parameters(query_text, params)

        results, error = query.data_source.query_runner.run_query(query_text, user)
        if error:
            raise Exception("Failed loading results for query id {}.".format(query.id))

    return results


def create_tables_from_query_ids(user, connection, query_ids, query_params, cached_query_ids=[]):
    for query_id in set(cached_query_ids):
        results = get_query_results(user, query_id, True)
        table_name = "cached_query_{query_id}".format(query_id=query_id)
        create_table(connection, table_name, results)

    for query in set(query_params):
        results = get_query_results(user, query[0], False, query[1])
        table_hash = hashlib.md5(
            "query_{query}_{hash}".format(query=query[0], hash=query[1]).encode(), usedforsecurity=False
        ).hexdigest()
        table_name = "query_{query_id}_{param_hash}".format(query_id=query[0], param_hash=table_hash)
        create_table(connection, table_name, results)

    for query_id in set(query_ids):
        results = get_query_results(user, query_id, False)
        table_name = "query_{query_id}".format(query_id=query_id)
        create_table(connection, table_name, results)


def fix_column_name(name):
    return '"{}"'.format(re.sub(r'[:."\s]', "_", name, flags=re.UNICODE))


def flatten(value):
    if isinstance(value, (list, dict)):
        return json_dumps(value)
    elif isinstance(value, decimal.Decimal):
        return float(value)
    elif isinstance(value, (datetime.date, datetime.time, datetime.datetime, datetime.timedelta)):
        return str(value)
    else:
        if logger.isEnabledFor(logging.DEBUG):
            if not isinstance(value, (type(None), str, float, int, bool)):
                logger.debug("flatten() found unhandled type: %s", str(type(value)))
        return value


def create_table(connection, table_name, query_results):
    try:
        columns = [column["name"] for column in query_results["columns"]]
        safe_columns = [fix_column_name(column) for column in columns]

        column_list = ", ".join(safe_columns)
        create_table = "CREATE TABLE {table_name} ({column_list})".format(
            table_name=table_name, column_list=column_list
        )
        logger.debug("CREATE TABLE query: %s", create_table)
        connection.execute(create_table)
    except sqlite3.OperationalError as exc:
        raise CreateTableError("Error creating table {}: {}".format(table_name, str(exc)))

    insert_template = "insert into {table_name} ({column_list}) values ({place_holders})".format(
        table_name=table_name,
        column_list=column_list,
        place_holders=",".join(["?"] * len(columns)),
    )
    logger.debug("INSERT template: %s", insert_template)

    for row in query_results["rows"]:
        values = [flatten(row.get(column)) for column in columns]
        connection.execute(insert_template, values)


def prepare_parameterized_query(query, query_params):
    for params in query_params:
        table_hash = hashlib.md5(
            "query_{query}_{hash}".format(query=params[0], hash=params[1]).encode(), usedforsecurity=False
        ).hexdigest()
        key = "param_query_{query_id}_{{{param_string}}}".format(query_id=params[0], param_string=params[1])
        value = "query_{query_id}_{param_hash}".format(query_id=params[0], param_hash=table_hash)
        query = query.replace(key, value)
    return query


class Results(BaseQueryRunner):
    should_annotate_query = False
    noop_query = "SELECT 1"

    @classmethod
    def configuration_schema(cls):
        return {"type": "object", "properties": {}}

    @classmethod
    def name(cls):
        return "Query Results"

    def run_query(self, query, user):
        connection = sqlite3.connect(":memory:")

        query_ids = extract_query_ids(query)

        query_params = extract_query_params(query)

        cached_query_ids = extract_cached_query_ids(query)
        create_tables_from_query_ids(user, connection, query_ids, query_params, cached_query_ids)

        cursor = connection.cursor()

        if query_params is not None:
            query = prepare_parameterized_query(query, query_params)

        try:
            cursor.execute(query)

            if cursor.description is not None:
                columns = self.fetch_columns([(i[0], None) for i in cursor.description])

                rows = []
                column_names = [c["name"] for c in columns]

                for i, row in enumerate(cursor):
                    for j, col in enumerate(row):
                        guess = guess_type(col)

                        if columns[j]["type"] is None:
                            columns[j]["type"] = guess
                        elif columns[j]["type"] != guess:
                            columns[j]["type"] = TYPE_STRING

                    rows.append(dict(zip(column_names, row)))

                data = {"columns": columns, "rows": rows}
                error = None
            else:
                error = "Query completed but it returned no data."
                data = None
        except (KeyboardInterrupt, JobTimeoutException):
            connection.cancel()
            raise
        finally:
            connection.close()
        return data, error


register(Results)
