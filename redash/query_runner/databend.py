try:
    import re

    from databend_sqlalchemy import connector

    enabled = True
except ImportError:
    enabled = False

from redash.query_runner import (
    TYPE_DATE,
    TYPE_DATETIME,
    TYPE_FLOAT,
    TYPE_INTEGER,
    TYPE_STRING,
    BaseQueryRunner,
    register,
)


class Databend(BaseQueryRunner):
    noop_query = "SELECT 1"

    @classmethod
    def configuration_schema(cls):
        return {
            "type": "object",
            "properties": {
                "host": {"type": "string", "default": "localhost"},
                "port": {"type": "string", "default": "8000"},
                "username": {"type": "string"},
                "password": {"type": "string", "default": ""},
                "database": {"type": "string"},
                "secure": {"type": "boolean", "default": False},
            },
            "order": ["username", "password", "host", "port", "database"],
            "required": ["username", "database"],
            "secret": ["password"],
        }

    @classmethod
    def name(cls):
        return "Databend"

    @classmethod
    def type(cls):
        return "databend"

    @classmethod
    def enabled(cls):
        return enabled

    @staticmethod
    def _define_column_type(column_type):
        c = column_type.lower()
        f = re.search(r"^nullable\((.*)\)$", c)
        if f is not None:
            c = f.group(1)
        if c.startswith("int") or c.startswith("uint"):
            return TYPE_INTEGER
        if c.startswith("float"):
            return TYPE_FLOAT
        if c == "datetime":
            return TYPE_DATETIME
        if c == "date":
            return TYPE_DATE
        return TYPE_STRING

    def run_query(self, query, user):
        host = self.configuration.get("host") or "localhost"
        port = self.configuration.get("port") or "8000"
        username = self.configuration.get("username") or "root"
        password = self.configuration.get("password") or ""
        database = self.configuration.get("database") or "default"
        secure = self.configuration.get("secure") or False
        connection = connector.connect(f"databend://{username}:{password}@{host}:{port}/{database}?secure={secure}")
        cursor = connection.cursor()

        try:
            cursor.execute(query)
            columns = self.fetch_columns([(i[0], self._define_column_type(i[1])) for i in cursor.description])
            rows = [dict(zip((column["name"] for column in columns), row)) for row in cursor]

            data = {"columns": columns, "rows": rows}
            error = None
        finally:
            connection.close()

        return data, error

    def get_schema(self, get_stats=False):
        query = """
        SELECT TABLE_SCHEMA,
               TABLE_NAME,
               COLUMN_NAME
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA NOT IN ('information_schema', 'system')
        """

        results, error = self.run_query(query, None)

        if error is not None:
            self._handle_run_query_error(error)

        schema = {}

        for row in results["rows"]:
            table_name = "{}.{}".format(row["table_schema"], row["table_name"])

            if table_name not in schema:
                schema[table_name] = {"name": table_name, "columns": []}

            schema[table_name]["columns"].append(row["column_name"])

        return list(schema.values())

    def _get_tables(self):
        query = """
        SELECT TABLE_SCHEMA,
               TABLE_NAME,
               COLUMN_NAME
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA NOT IN ('information_schema', 'system')
        """

        results, error = self.run_query(query, None)

        if error is not None:
            self._handle_run_query_error(error)

        schema = {}

        for row in results["rows"]:
            table_name = "{}.{}".format(row["table_schema"], row["table_name"])

            if table_name not in schema:
                schema[table_name] = {"name": table_name, "columns": []}

            schema[table_name]["columns"].append(row["column_name"])

        return list(schema.values())


register(Databend)
