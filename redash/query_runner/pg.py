import logging
import os
import select
from base64 import b64decode
from tempfile import NamedTemporaryFile
from uuid import uuid4

import psycopg2
from psycopg2.extras import Range

from redash.query_runner import (
    TYPE_BOOLEAN,
    TYPE_DATE,
    TYPE_DATETIME,
    TYPE_FLOAT,
    TYPE_INTEGER,
    TYPE_STRING,
    BaseSQLQueryRunner,
    InterruptException,
    JobTimeoutException,
    register,
)

logger = logging.getLogger(__name__)

try:
    import boto3

    IAM_ENABLED = True
except ImportError:
    IAM_ENABLED = False

types_map = {
    20: TYPE_INTEGER,
    21: TYPE_INTEGER,
    23: TYPE_INTEGER,
    700: TYPE_FLOAT,
    1700: TYPE_FLOAT,
    701: TYPE_FLOAT,
    16: TYPE_BOOLEAN,
    1082: TYPE_DATE,
    1182: TYPE_DATE,
    1114: TYPE_DATETIME,
    1184: TYPE_DATETIME,
    1115: TYPE_DATETIME,
    1185: TYPE_DATETIME,
    1014: TYPE_STRING,
    1015: TYPE_STRING,
    1008: TYPE_STRING,
    1009: TYPE_STRING,
    2951: TYPE_STRING,
    1043: TYPE_STRING,
    1002: TYPE_STRING,
    1003: TYPE_STRING,
}


def _wait(conn, timeout=None):
    while 1:
        try:
            state = conn.poll()
            if state == psycopg2.extensions.POLL_OK:
                break
            elif state == psycopg2.extensions.POLL_WRITE:
                select.select([], [conn.fileno()], [], timeout)
            elif state == psycopg2.extensions.POLL_READ:
                select.select([conn.fileno()], [], [], timeout)
            else:
                raise psycopg2.OperationalError("poll() returned %s" % state)
        except select.error:
            raise psycopg2.OperationalError("select.error received")


def full_table_name(schema, name):
    if "." in name:
        name = '"{}"'.format(name)

    return "{}.{}".format(schema, name)


def build_schema(query_result, schema):
    # By default we omit the public schema name from the table name. But there are
    # edge cases, where this might cause conflicts. For example:
    # * We have a schema named "main" with table "users".
    # * We have a table named "main.users" in the public schema.
    # (while this feels unlikely, this actually happened)
    # In this case if we omit the schema name for the public table, we will have
    # a conflict.
    table_names = set(
        map(
            lambda r: full_table_name(r["table_schema"], r["table_name"]),
            query_result["rows"],
        )
    )

    for row in query_result["rows"]:
        if row["table_schema"] != "public":
            table_name = full_table_name(row["table_schema"], row["table_name"])
        else:
            if row["table_name"] in table_names:
                table_name = full_table_name(row["table_schema"], row["table_name"])
            else:
                table_name = row["table_name"]

        if table_name not in schema:
            schema[table_name] = {"name": table_name, "columns": []}

        column = row["column_name"]
        if row.get("data_type") is not None:
            column = {"name": row["column_name"], "type": row["data_type"]}

        schema[table_name]["columns"].append(column)


def _create_cert_file(configuration, key, ssl_config):
    file_key = key + "File"
    if file_key in configuration:
        with NamedTemporaryFile(mode="w", delete=False) as cert_file:
            cert_bytes = b64decode(configuration[file_key])
            cert_file.write(cert_bytes.decode("utf-8"))

        ssl_config[key] = cert_file.name


def _cleanup_ssl_certs(ssl_config):
    for k, v in ssl_config.items():
        if k != "sslmode":
            os.remove(v)


def _get_ssl_config(configuration):
    ssl_config = {"sslmode": configuration.get("sslmode", "prefer")}

    _create_cert_file(configuration, "sslrootcert", ssl_config)
    _create_cert_file(configuration, "sslcert", ssl_config)
    _create_cert_file(configuration, "sslkey", ssl_config)

    return ssl_config


class PostgreSQL(BaseSQLQueryRunner):
    noop_query = "SELECT 1"

    @classmethod
    def configuration_schema(cls):
        return {
            "type": "object",
            "properties": {
                "user": {"type": "string"},
                "password": {"type": "string"},
                "host": {"type": "string", "default": "127.0.0.1"},
                "port": {"type": "number", "default": 5432},
                "dbname": {"type": "string", "title": "Database Name"},
                "sslmode": {
                    "type": "string",
                    "title": "SSL Mode",
                    "default": "prefer",
                    "extendedEnum": [
                        {"value": "disable", "name": "Disable"},
                        {"value": "allow", "name": "Allow"},
                        {"value": "prefer", "name": "Prefer"},
                        {"value": "require", "name": "Require"},
                        {"value": "verify-ca", "name": "Verify CA"},
                        {"value": "verify-full", "name": "Verify Full"},
                    ],
                },
                "sslrootcertFile": {"type": "string", "title": "SSL Root Certificate"},
                "sslcertFile": {"type": "string", "title": "SSL Client Certificate"},
                "sslkeyFile": {"type": "string", "title": "SSL Client Key"},
            },
            "order": ["host", "port", "user", "password"],
            "required": ["dbname"],
            "secret": ["password", "sslrootcertFile", "sslcertFile", "sslkeyFile"],
            "extra_options": [
                "sslmode",
                "sslrootcertFile",
                "sslcertFile",
                "sslkeyFile",
            ],
        }

    @classmethod
    def type(cls):
        return "pg"

    @classmethod
    def custom_json_encoder(cls, dec, o):
        if isinstance(o, Range):
            # From: https://github.com/psycopg/psycopg2/pull/779
            if o._bounds is None:
                return ""

            items = [o._bounds[0], str(o._lower), ", ", str(o._upper), o._bounds[1]]

            return "".join(items)
        return None

    def _get_definitions(self, schema, query):
        results, error = self.run_query(query, None)

        if error is not None:
            self._handle_run_query_error(error)

        build_schema(results, schema)

    def _get_tables(self, schema):
        """
        relkind constants per https://www.postgresql.org/docs/10/static/catalog-pg-class.html
        r = regular table
        v = view
        m = materialized view
        f = foreign table
        p = partitioned table (new in 10)
        ---
        i = index
        S = sequence
        t = TOAST table
        c = composite type
        """

        query = """
        SELECT s.nspname as table_schema,
               c.relname as table_name,
               a.attname as column_name,
               null as data_type
        FROM pg_class c
        JOIN pg_namespace s
        ON c.relnamespace = s.oid
        AND s.nspname NOT IN ('pg_catalog', 'information_schema')
        JOIN pg_attribute a
        ON a.attrelid = c.oid
        AND a.attnum > 0
        AND NOT a.attisdropped
        WHERE c.relkind IN ('m', 'f', 'p')
        AND has_table_privilege(s.nspname || '.' || c.relname, 'select')
        AND has_schema_privilege(s.nspname, 'usage')

        UNION

        SELECT table_schema,
               table_name,
               column_name,
               data_type
        FROM information_schema.columns
        WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
        """

        self._get_definitions(schema, query)

        return list(schema.values())

    def _get_connection(self):
        self.ssl_config = _get_ssl_config(self.configuration)
        connection = psycopg2.connect(
            user=self.configuration.get("user"),
            password=self.configuration.get("password"),
            host=self.configuration.get("host"),
            port=self.configuration.get("port"),
            dbname=self.configuration.get("dbname"),
            async_=True,
            **self.ssl_config,
        )

        return connection

    def run_query(self, query, user):
        connection = self._get_connection()
        _wait(connection, timeout=10)

        cursor = connection.cursor()

        try:
            cursor.execute(query)
            _wait(connection)

            if cursor.description is not None:
                columns = self.fetch_columns([(i[0], types_map.get(i[1], None)) for i in cursor.description])
                rows = [dict(zip((column["name"] for column in columns), row)) for row in cursor]

                data = {"columns": columns, "rows": rows}
                error = None
            else:
                error = "Query completed but it returned no data."
                data = None
        except (select.error, OSError):
            error = "Query interrupted. Please retry."
            data = None
        except psycopg2.DatabaseError as e:
            error = str(e)
            data = None
        except (KeyboardInterrupt, InterruptException, JobTimeoutException):
            connection.cancel()
            raise
        finally:
            connection.close()
            _cleanup_ssl_certs(self.ssl_config)

        return data, error


class Redshift(PostgreSQL):
    @classmethod
    def type(cls):
        return "redshift"

    @classmethod
    def name(cls):
        return "Redshift"

    def _get_connection(self):
        self.ssl_config = {}

        sslrootcert_path = os.path.join(os.path.dirname(__file__), "./files/redshift-ca-bundle.crt")

        connection = psycopg2.connect(
            user=self.configuration.get("user"),
            password=self.configuration.get("password"),
            host=self.configuration.get("host"),
            port=self.configuration.get("port"),
            dbname=self.configuration.get("dbname"),
            sslmode=self.configuration.get("sslmode", "prefer"),
            sslrootcert=sslrootcert_path,
            async_=True,
        )

        return connection

    @classmethod
    def configuration_schema(cls):
        return {
            "type": "object",
            "properties": {
                "user": {"type": "string"},
                "password": {"type": "string"},
                "host": {"type": "string"},
                "port": {"type": "number"},
                "dbname": {"type": "string", "title": "Database Name"},
                "sslmode": {"type": "string", "title": "SSL Mode", "default": "prefer"},
                "adhoc_query_group": {
                    "type": "string",
                    "title": "Query Group for Adhoc Queries",
                    "default": "default",
                },
                "scheduled_query_group": {
                    "type": "string",
                    "title": "Query Group for Scheduled Queries",
                    "default": "default",
                },
            },
            "order": [
                "host",
                "port",
                "user",
                "password",
                "dbname",
                "sslmode",
                "adhoc_query_group",
                "scheduled_query_group",
            ],
            "required": ["dbname", "user", "password", "host", "port"],
            "secret": ["password"],
        }

    def annotate_query(self, query, metadata):
        annotated = super(Redshift, self).annotate_query(query, metadata)

        if metadata.get("Scheduled", False):
            query_group = self.configuration.get("scheduled_query_group")
        else:
            query_group = self.configuration.get("adhoc_query_group")

        if query_group:
            set_query_group = "set query_group to {};".format(query_group)
            annotated = "{}\n{}".format(set_query_group, annotated)

        return annotated

    def _get_tables(self, schema):
        # Use svv_columns to include internal & external (Spectrum) tables and views data for Redshift
        # https://docs.aws.amazon.com/redshift/latest/dg/r_SVV_COLUMNS.html
        # Use HAS_SCHEMA_PRIVILEGE(), SVV_EXTERNAL_SCHEMAS and HAS_TABLE_PRIVILEGE() to filter
        # out tables the current user cannot access.
        # https://docs.aws.amazon.com/redshift/latest/dg/r_HAS_SCHEMA_PRIVILEGE.html
        # https://docs.aws.amazon.com/redshift/latest/dg/r_SVV_EXTERNAL_SCHEMAS.html
        # https://docs.aws.amazon.com/redshift/latest/dg/r_HAS_TABLE_PRIVILEGE.html
        query = """
        WITH tables AS (
            SELECT DISTINCT table_name,
                            table_schema,
                            column_name,
                            data_type,
                            ordinal_position AS pos
            FROM svv_columns
            WHERE table_schema NOT IN ('pg_internal','pg_catalog','information_schema')
            AND table_schema NOT LIKE 'pg_temp_%'
        )
        SELECT table_name, table_schema, column_name, data_type
        FROM tables
        WHERE
            HAS_SCHEMA_PRIVILEGE(table_schema, 'USAGE') AND
            (
                table_schema IN (SELECT schemaname FROM SVV_EXTERNAL_SCHEMAS) OR
                HAS_TABLE_PRIVILEGE('"' || table_schema || '"."' || table_name || '"', 'SELECT')
            )
        ORDER BY table_name, pos
        """

        self._get_definitions(schema, query)

        return list(schema.values())


class RedshiftIAM(Redshift):
    @classmethod
    def type(cls):
        return "redshift_iam"

    @classmethod
    def name(cls):
        return "Redshift (with IAM User/Role)"

    @classmethod
    def enabled(cls):
        return IAM_ENABLED

    def _login_method_selection(self):
        if self.configuration.get("rolename"):
            if not self.configuration.get("aws_access_key_id") or not self.configuration.get("aws_secret_access_key"):
                return "ASSUME_ROLE_NO_KEYS"
            return "ASSUME_ROLE_KEYS"
        elif self.configuration.get("aws_access_key_id") and self.configuration.get("aws_secret_access_key"):
            return "KEYS"
        elif not self.configuration.get("password"):
            return "ROLE"

    @classmethod
    def configuration_schema(cls):
        return {
            "type": "object",
            "properties": {
                "rolename": {"type": "string", "title": "IAM Role Name"},
                "aws_region": {"type": "string", "title": "AWS Region"},
                "aws_access_key_id": {"type": "string", "title": "AWS Access Key ID"},
                "aws_secret_access_key": {
                    "type": "string",
                    "title": "AWS Secret Access Key",
                },
                "clusterid": {"type": "string", "title": "Redshift Cluster ID"},
                "user": {"type": "string"},
                "host": {"type": "string"},
                "port": {"type": "number"},
                "dbname": {"type": "string", "title": "Database Name"},
                "sslmode": {"type": "string", "title": "SSL Mode", "default": "prefer"},
                "adhoc_query_group": {
                    "type": "string",
                    "title": "Query Group for Adhoc Queries",
                    "default": "default",
                },
                "scheduled_query_group": {
                    "type": "string",
                    "title": "Query Group for Scheduled Queries",
                    "default": "default",
                },
            },
            "order": [
                "rolename",
                "aws_region",
                "aws_access_key_id",
                "aws_secret_access_key",
                "clusterid",
                "host",
                "port",
                "user",
                "dbname",
                "sslmode",
                "adhoc_query_group",
                "scheduled_query_group",
            ],
            "required": ["dbname", "user", "host", "port", "aws_region"],
            "secret": ["aws_secret_access_key"],
        }

    def _get_connection(self):
        self.ssl_config = {}

        sslrootcert_path = os.path.join(os.path.dirname(__file__), "./files/redshift-ca-bundle.crt")

        login_method = self._login_method_selection()

        if login_method == "KEYS":
            client = boto3.client(
                "redshift",
                region_name=self.configuration.get("aws_region"),
                aws_access_key_id=self.configuration.get("aws_access_key_id"),
                aws_secret_access_key=self.configuration.get("aws_secret_access_key"),
            )
        elif login_method == "ROLE":
            client = boto3.client("redshift", region_name=self.configuration.get("aws_region"))
        else:
            if login_method == "ASSUME_ROLE_KEYS":
                assume_client = client = boto3.client(
                    "sts",
                    region_name=self.configuration.get("aws_region"),
                    aws_access_key_id=self.configuration.get("aws_access_key_id"),
                    aws_secret_access_key=self.configuration.get("aws_secret_access_key"),
                )
            else:
                assume_client = client = boto3.client("sts", region_name=self.configuration.get("aws_region"))
            role_session = f"redash_{uuid4().hex}"
            session_keys = assume_client.assume_role(
                RoleArn=self.configuration.get("rolename"), RoleSessionName=role_session
            )["Credentials"]
            client = boto3.client(
                "redshift",
                region_name=self.configuration.get("aws_region"),
                aws_access_key_id=session_keys["AccessKeyId"],
                aws_secret_access_key=session_keys["SecretAccessKey"],
                aws_session_token=session_keys["SessionToken"],
            )
        credentials = client.get_cluster_credentials(
            DbUser=self.configuration.get("user"),
            DbName=self.configuration.get("dbname"),
            ClusterIdentifier=self.configuration.get("clusterid"),
        )
        db_user = credentials["DbUser"]
        db_password = credentials["DbPassword"]
        connection = psycopg2.connect(
            user=db_user,
            password=db_password,
            host=self.configuration.get("host"),
            port=self.configuration.get("port"),
            dbname=self.configuration.get("dbname"),
            sslmode=self.configuration.get("sslmode", "prefer"),
            sslrootcert=sslrootcert_path,
            async_=True,
        )

        return connection


class CockroachDB(PostgreSQL):
    @classmethod
    def type(cls):
        return "cockroach"


register(PostgreSQL)
register(Redshift)
register(RedshiftIAM)
register(CockroachDB)
