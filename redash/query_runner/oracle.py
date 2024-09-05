import logging
import os

from redash.query_runner import (
    TYPE_DATETIME,
    TYPE_FLOAT,
    TYPE_INTEGER,
    TYPE_STRING,
    BaseSQLQueryRunner,
    JobTimeoutException,
    register,
)

try:
    import oracledb

    TYPES_MAP = {
        oracledb.DATETIME: TYPE_DATETIME,
        oracledb.CLOB: TYPE_STRING,
        oracledb.LOB: TYPE_STRING,
        oracledb.FIXED_CHAR: TYPE_STRING,
        oracledb.FIXED_NCHAR: TYPE_STRING,
        oracledb.INTERVAL: TYPE_DATETIME,
        oracledb.LONG_STRING: TYPE_STRING,
        oracledb.NATIVE_FLOAT: TYPE_FLOAT,
        oracledb.NCHAR: TYPE_STRING,
        oracledb.NUMBER: TYPE_FLOAT,
        oracledb.ROWID: TYPE_INTEGER,
        oracledb.STRING: TYPE_STRING,
        oracledb.TIMESTAMP: TYPE_DATETIME,
    }

    ENABLED = True
except ImportError:
    ENABLED = False

logger = logging.getLogger(__name__)


class Oracle(BaseSQLQueryRunner):
    should_annotate_query = False
    noop_query = "SELECT 1 FROM dual"
    limit_query = " FETCH NEXT 1000 ROWS ONLY"
    limit_keywords = ["ROW", "ROWS", "ONLY", "TIES"]

    @classmethod
    def get_col_type(cls, col_type, scale):
        if col_type == oracledb.NUMBER:
            if scale is None:
                return TYPE_INTEGER
            if scale > 0:
                return TYPE_FLOAT
            return TYPE_INTEGER
        return TYPES_MAP.get(col_type, None)

    @classmethod
    def enabled(cls):
        return ENABLED

    @classmethod
    def configuration_schema(cls):
        return {
            "type": "object",
            "properties": {
                "user": {"type": "string"},
                "password": {"type": "string"},
                "host": {
                    "type": "string",
                    "title": "Host: To use a DSN Service Name instead, use the text string `_useservicename` in the host name field.",
                },
                "port": {"type": "number"},
                "servicename": {"type": "string", "title": "DSN Service Name"},
                "encoding": {"type": "string"},
            },
            "required": ["servicename", "user", "password", "host", "port"],
            "extra_options": ["encoding"],
            "secret": ["password"],
        }

    @classmethod
    def type(cls):
        return "oracle"

    def _get_tables(self, schema):
        query = """
        SELECT
            all_tab_cols.OWNER,
            all_tab_cols.TABLE_NAME,
            all_tab_cols.COLUMN_NAME
        FROM all_tab_cols
        WHERE all_tab_cols.OWNER NOT IN('SYS','SYSTEM','ORDSYS','CTXSYS','WMSYS','MDSYS','ORDDATA','XDB','OUTLN','DMSYS','DSSYS','EXFSYS','LBACSYS','TSMSYS')
        """

        results, error = self.run_query(query, None)

        if error is not None:
            self._handle_run_query_error(error)

        for row in results["rows"]:
            if row["OWNER"] is not None:
                table_name = "{}.{}".format(row["OWNER"], row["TABLE_NAME"])
            else:
                table_name = row["TABLE_NAME"]

            if table_name not in schema:
                schema[table_name] = {"name": table_name, "columns": []}

            schema[table_name]["columns"].append(row["COLUMN_NAME"])

        return list(schema.values())

    @classmethod
    def _convert_number(cls, value):
        try:
            return int(value)
        except BaseException:
            return value

    @classmethod
    def output_handler(cls, cursor, name, default_type, length, precision, scale):
        if default_type in (oracledb.CLOB, oracledb.LOB):
            return cursor.var(oracledb.LONG_STRING, 80000, cursor.arraysize)

        if default_type in (oracledb.STRING, oracledb.FIXED_CHAR):
            return cursor.var(str, length, cursor.arraysize)

        if default_type == oracledb.NUMBER:
            if scale <= 0:
                return cursor.var(
                    oracledb.STRING,
                    255,
                    outconverter=Oracle._convert_number,
                    arraysize=cursor.arraysize,
                )

    def run_query(self, query, user):
        if self.configuration.get("encoding"):
            os.environ["NLS_LANG"] = self.configuration["encoding"]

        # To use a DSN Service Name instead, use the text string `_useservicename` in the host name field.
        if self.configuration["host"].lower() == "_useservicename":
            dsn = self.configuration["servicename"]
        else:
            dsn = oracledb.makedsn(
                self.configuration["host"],
                self.configuration["port"],
                service_name=self.configuration["servicename"],
            )

        connection = oracledb.connect(
            user=self.configuration["user"],
            password=self.configuration["password"],
            dsn=dsn,
        )
        connection.outputtypehandler = Oracle.output_handler

        cursor = connection.cursor()

        try:
            cursor.execute(query)
            rows_count = cursor.rowcount
            if cursor.description is not None:
                columns = self.fetch_columns([(i[0], Oracle.get_col_type(i[1], i[5])) for i in cursor.description])
                rows = [dict(zip((c["name"] for c in columns), row)) for row in cursor]
                data = {"columns": columns, "rows": rows}
                error = None
            else:
                columns = [{"name": "Row(s) Affected", "type": "TYPE_INTEGER"}]
                rows = [{"Row(s) Affected": rows_count}]
                data = {"columns": columns, "rows": rows}
                connection.commit()
        except oracledb.DatabaseError as err:
            (err_args,) = err.args
            line_number = query.count("\n", 0, err_args.offset) + 1
            column_number = err_args.offset - query.rfind("\n", 0, err_args.offset) - 1
            error = "Query failed at line {}, column {}: {}".format(str(line_number), str(column_number), str(err))
            data = None
        except (KeyboardInterrupt, JobTimeoutException):
            connection.cancel()
            raise
        finally:
            os.environ.pop("NLS_LANG", None)
            connection.close()

        return data, error


register(Oracle)
