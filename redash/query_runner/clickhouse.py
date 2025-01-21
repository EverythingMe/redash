import logging
import re
from urllib.parse import urlparse
from uuid import uuid4

import requests

from redash.query_runner import (
    TYPE_DATE,
    TYPE_DATETIME,
    TYPE_FLOAT,
    TYPE_INTEGER,
    TYPE_STRING,
    BaseSQLQueryRunner,
    register,
    split_sql_statements,
)

logger = logging.getLogger(__name__)


def split_multi_query(query):
    return [st for st in split_sql_statements(query) if st != ""]


class ClickHouse(BaseSQLQueryRunner):
    noop_query = "SELECT 1"

    @classmethod
    def configuration_schema(cls):
        return {
            "type": "object",
            "properties": {
                "url": {"type": "string", "default": "http://127.0.0.1:8123"},
                "user": {"type": "string", "default": "default"},
                "password": {"type": "string"},
                "dbname": {"type": "string", "title": "Database Name"},
                "timeout": {
                    "type": "number",
                    "title": "Request Timeout",
                    "default": 30,
                },
                "verify": {
                    "type": "boolean",
                    "title": "Verify SSL certificate",
                    "default": True,
                },
            },
            "order": ["url", "user", "password", "dbname"],
            "required": ["dbname"],
            "extra_options": ["timeout", "verify"],
            "secret": ["password"],
        }

    @property
    def _url(self):
        return urlparse(self.configuration["url"])

    @_url.setter
    def _url(self, url):
        self.configuration["url"] = url.geturl()

    @property
    def host(self):
        return self._url.hostname

    @host.setter
    def host(self, host):
        self._url = self._url._replace(netloc="{}:{}".format(host, self._url.port))

    @property
    def port(self):
        return self._url.port

    @port.setter
    def port(self, port):
        self._url = self._url._replace(netloc="{}:{}".format(self._url.hostname, port))

    def _get_tables(self, schema):
        query = "SELECT database, table, name FROM system.columns WHERE database NOT IN ('system')"

        results, error = self.run_query(query, None)

        if error is not None:
            self._handle_run_query_error(error)

        for row in results["rows"]:
            table_name = "{}.{}".format(row["database"], row["table"])

            if table_name not in schema:
                schema[table_name] = {"name": table_name, "columns": []}

            schema[table_name]["columns"].append(row["name"])

        return list(schema.values())

    def _send_query(self, data, session_id=None, session_check=None):
        url = self.configuration.get("url", "http://127.0.0.1:8123")
        timeout = self.configuration.get("timeout", 30)

        params = {
            "database": self.configuration["dbname"],
            "default_format": "JSON",
        }

        headers = {
            "x-clickhouse-user": self.configuration.get("user", "default"),
            "x-clickhouse-key": self.configuration.get("password", ""),
        }

        if session_id:
            params["session_id"] = session_id
            params["session_check"] = "1" if session_check else "0"
            params["session_timeout"] = timeout

        try:
            verify = self.configuration.get("verify", True)
            r = requests.post(
                url,
                data=data.encode("utf-8", "ignore"),
                stream=False,
                timeout=timeout,
                params=params,
                verify=verify,
                headers=headers,
            )

            if not r.ok:
                raise Exception(r.text)

            # In certain situations the response body can be empty even if the query was successful, for example
            # when creating temporary tables.
            if not r.text:
                return {}

            response = r.json()
            if "exception" in response:
                raise Exception(response["exception"])

            return response
        except requests.RequestException as e:
            if e.response:
                details = "({}, Status Code: {})".format(e.__class__.__name__, e.response.status_code)
            else:
                details = "({})".format(e.__class__.__name__)
            raise Exception("Connection error to: {} {}.".format(url, details))

    @staticmethod
    def _define_column_type(column):
        c = column.lower()
        f = re.search(r"^nullable\((.*)\)$", c)
        if f is not None:
            c = f.group(1)
        if c.startswith("int") or c.startswith("uint"):
            return TYPE_INTEGER
        elif c.startswith("float"):
            return TYPE_FLOAT
        elif c == "datetime":
            return TYPE_DATETIME
        elif c == "date":
            return TYPE_DATE
        else:
            return TYPE_STRING

    def _clickhouse_query(self, query, session_id=None, session_check=None):
        logger.debug(f"{self.name()} is about to execute query: %s", query)

        query += "\nFORMAT JSON"

        response = self._send_query(query, session_id, session_check)

        columns = []
        columns_int64 = []  # db converts value to string if its type equals UInt64
        columns_totals = {}

        meta = response.get("meta", [])
        for r in meta:
            column_name = r["name"]
            column_type = self._define_column_type(r["type"])

            if r["type"] in ("Int64", "UInt64", "Nullable(Int64)", "Nullable(UInt64)"):
                columns_int64.append(column_name)
            else:
                columns_totals[column_name] = "Total" if column_type == TYPE_STRING else None

            columns.append({"name": column_name, "friendly_name": column_name, "type": column_type})

        rows = response.get("data", [])
        for row in rows:
            for column in columns_int64:
                try:
                    row[column] = int(row[column])
                except TypeError:
                    row[column] = None

        if "totals" in response:
            totals = response["totals"]
            for column, value in columns_totals.items():
                totals[column] = value
            rows.append(totals)

        return {"columns": columns, "rows": rows}

    def run_query(self, query, user):
        queries = split_multi_query(query)

        if not queries:
            data = None
            error = "Query is empty"
            return data, error

        try:
            # If just one query was given no session is needed
            if len(queries) == 1:
                data = self._clickhouse_query(queries[0])
            else:
                # If more than one query was given, a session is needed. Parameter session_check must be false
                # for the first query
                session_id = "redash_{}".format(uuid4().hex)

                data = self._clickhouse_query(queries[0], session_id, session_check=False)

                for query in queries[1:]:
                    data = self._clickhouse_query(query, session_id, session_check=True)

            error = None
        except Exception as e:
            data = None
            logging.exception(e)
            error = str(e)
        return data, error


register(ClickHouse)
