import datetime
import importlib.util
import logging

from redash.query_runner import (
    TYPE_BOOLEAN,
    TYPE_DATETIME,
    TYPE_FLOAT,
    TYPE_INTEGER,
    TYPE_STRING,
    BaseSQLQueryRunner,
    JobTimeoutException,
    register,
)

ignite_available = importlib.util.find_spec("pyignite") is not None
gridgain_available = importlib.util.find_spec("pygridgain") is not None


logger = logging.getLogger(__name__)

types_map = {
    "java.lang.String": TYPE_STRING,
    "java.lang.Float": TYPE_FLOAT,
    "java.lang.Double": TYPE_FLOAT,
    "java.sql.Date": TYPE_DATETIME,
    "java.sql.Timestamp": TYPE_DATETIME,
    "java.lang.Long": TYPE_INTEGER,
    "java.lang.Integer": TYPE_INTEGER,
    "java.lang.Short": TYPE_INTEGER,
    "java.lang.Boolean": TYPE_BOOLEAN,
    "java.lang.Decimal": TYPE_FLOAT,
}


class Ignite(BaseSQLQueryRunner):
    should_annotate_query = False
    noop_query = "SELECT 1"

    @classmethod
    def configuration_schema(cls):
        return {
            "type": "object",
            "properties": {
                "user": {"type": "string"},
                "password": {"type": "string"},
                "server": {"type": "string", "default": "127.0.0.1:10800"},
                "tls": {"type": "boolean", "default": False, "title": "Use SSL/TLS connection"},
                "schema": {"type": "string", "title": "Schema Name", "default": "PUBLIC"},
                "distributed_joins": {"type": "boolean", "title": "Allow distributed joins", "default": False},
                "enforce_join_order": {"type": "boolean", "title": "Enforce join order", "default": False},
                "lazy": {"type": "boolean", "title": "Lazy query execution", "default": True},
                "gridgain": {"type": "boolean", "title": "Use GridGain libraries", "default": gridgain_available},
            },
            "required": ["server"],
            "secret": ["password"],
        }

    @classmethod
    def name(cls):
        return "Apache Ignite"

    @classmethod
    def type(cls):
        return "ignite"

    @classmethod
    def enabled(cls):
        return ignite_available or gridgain_available

    def _get_tables(self, schema):
        query = """
        SELECT schema_name, table_name, column_name, type
        FROM SYS.TABLE_COLUMNS
        WHERE schema_name NOT IN ('SYS') and column_name not in ('_KEY','_VAL');
        """

        results, error = self.run_query(query, None)

        if error is not None:
            raise Exception("Failed getting schema.")

        for row in results["rows"]:
            if row["SCHEMA_NAME"] != self.configuration.get("schema", "PUBLIC"):
                table_name = "{}.{}".format(row["SCHEMA_NAME"], row["TABLE_NAME"])
            else:
                table_name = row["TABLE_NAME"]

            if table_name not in schema:
                schema[table_name] = {"name": table_name, "columns": []}

            col_type = TYPE_STRING
            if row["TYPE"] in types_map:
                col_type = types_map[row["TYPE"]]

            schema[table_name]["columns"].append({"name": row["COLUMN_NAME"], "type": col_type})

        return list(schema.values())

    def normalise_column(self, col):
        # if it's a datetime, just return the milliseconds
        if type(col) is tuple and len(col) == 2 and type(col[0]) is datetime.datetime and isinstance(col[1], int):
            return col[0]
        return col

    def normalise_row(self, row):
        return [self.normalise_column(col) for col in row]

    def server_to_connection(self, s):
        st = s.split(":")
        if len(st) == 1:
            server = s
            port = 10800
        elif len(st) == 2:
            server = st[0]
            port = int(st[1])
        else:
            server = "unknown"
            port = 10800
        return (server, port)

    def _parse_results(self, c):
        column_names = next(c)
        columns = [{"name": col, "friendly_name": col.lower()} for col in column_names]
        rows = [dict(zip(column_names, self.normalise_row(row))) for row in c]

        return (columns, rows)

    def run_query(self, query, user):
        connection = None

        try:
            server = self.configuration.get("server", "127.0.0.1:10800")
            user = self.configuration.get("user", None)
            password = self.configuration.get("password", None)
            tls = self.configuration.get("tls", False)
            distributed_joins = self.configuration.get("distributed_joins", False)
            enforce_join_order = self.configuration.get("enforce_join_order", False)
            lazy = self.configuration.get("lazy", True)
            gridgain = self.configuration.get("gridgain", False)

            if gridgain:
                from pygridgain import Client
            else:
                from pyignite import Client

            connection = Client(username=user, password=password, use_ssl=tls)
            connection.connect([self.server_to_connection(s) for s in server.split(",")])

            cursor = connection.sql(
                query,
                include_field_names=True,
                distributed_joins=distributed_joins,
                enforce_join_order=enforce_join_order,
                lazy=lazy,
            )
            logger.debug("Ignite running query: %s", query)

            result = self._parse_results(cursor)
            data = {"columns": result[0], "rows": result[1]}
            error = None

        except (KeyboardInterrupt, JobTimeoutException):
            connection.cancel()
            raise
        finally:
            if connection:
                connection.close()

        return data, error


register(Ignite)
