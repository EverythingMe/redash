import logging
import os

from redash.query_runner import (
    TYPE_BOOLEAN,
    TYPE_DATE,
    TYPE_DATETIME,
    TYPE_FLOAT,
    TYPE_INTEGER,
    TYPE_STRING,
    BaseQueryRunner,
    register,
)
from redash.settings import parse_boolean

logger = logging.getLogger(__name__)
ANNOTATE_QUERY = parse_boolean(os.environ.get("ATHENA_ANNOTATE_QUERY", "true"))
SHOW_EXTRA_SETTINGS = parse_boolean(os.environ.get("ATHENA_SHOW_EXTRA_SETTINGS", "true"))
ASSUME_ROLE = parse_boolean(os.environ.get("ATHENA_ASSUME_ROLE", "false"))
OPTIONAL_CREDENTIALS = parse_boolean(os.environ.get("ATHENA_OPTIONAL_CREDENTIALS", "true"))

try:
    import boto3
    import pyathena

    enabled = True
except ImportError:
    enabled = False


_TYPE_MAPPINGS = {
    "boolean": TYPE_BOOLEAN,
    "tinyint": TYPE_INTEGER,
    "smallint": TYPE_INTEGER,
    "integer": TYPE_INTEGER,
    "bigint": TYPE_INTEGER,
    "double": TYPE_FLOAT,
    "varchar": TYPE_STRING,
    "timestamp": TYPE_DATETIME,
    "date": TYPE_DATE,
    "varbinary": TYPE_STRING,
    "array": TYPE_STRING,
    "map": TYPE_STRING,
    "row": TYPE_STRING,
    "decimal": TYPE_FLOAT,
}


class SimpleFormatter:
    def format(self, operation, parameters=None):
        return operation


class Athena(BaseQueryRunner):
    noop_query = "SELECT 1"

    @classmethod
    def name(cls):
        return "Amazon Athena"

    @classmethod
    def configuration_schema(cls):
        schema = {
            "type": "object",
            "properties": {
                "region": {"type": "string", "title": "AWS Region"},
                "aws_access_key": {"type": "string", "title": "AWS Access Key"},
                "aws_secret_key": {"type": "string", "title": "AWS Secret Key"},
                "s3_staging_dir": {
                    "type": "string",
                    "title": "S3 Staging (Query Results) Bucket Path",
                },
                "schema": {
                    "type": "string",
                    "title": "Schema Name",
                    "default": "default",
                },
                "glue": {"type": "boolean", "title": "Use Glue Data Catalog"},
                "catalog_ids": {
                    "type": "string",
                    "title": "Enter Glue Data Catalog IDs, separated by commas (leave blank for default catalog)",
                },
                "work_group": {
                    "type": "string",
                    "title": "Athena Work Group",
                    "default": "primary",
                },
                "cost_per_tb": {
                    "type": "number",
                    "title": "Athena cost per Tb scanned (USD)",
                    "default": 5,
                },
            },
            "required": ["region", "s3_staging_dir"],
            "extra_options": ["glue", "catalog_ids", "cost_per_tb"],
            "order": [
                "region",
                "s3_staging_dir",
                "schema",
                "work_group",
                "cost_per_tb",
            ],
            "secret": ["aws_secret_key"],
        }

        if SHOW_EXTRA_SETTINGS:
            schema["properties"].update(
                {
                    "encryption_option": {
                        "type": "string",
                        "title": "Encryption Option",
                    },
                    "kms_key": {"type": "string", "title": "KMS Key"},
                }
            )
            schema["extra_options"].append("encryption_option")
            schema["extra_options"].append("kms_key")

        if ASSUME_ROLE:
            del schema["properties"]["aws_access_key"]
            del schema["properties"]["aws_secret_key"]
            schema["secret"] = []

            schema["order"].insert(1, "iam_role")
            schema["order"].insert(2, "external_id")
            schema["properties"].update(
                {
                    "iam_role": {"type": "string", "title": "IAM role to assume"},
                    "external_id": {
                        "type": "string",
                        "title": "External ID to be used while STS assume role",
                    },
                }
            )
        else:
            schema["order"].insert(1, "aws_access_key")
            schema["order"].insert(2, "aws_secret_key")

        if not OPTIONAL_CREDENTIALS and not ASSUME_ROLE:
            schema["required"] += ["aws_access_key", "aws_secret_key"]

        return schema

    @classmethod
    def enabled(cls):
        return enabled

    def annotate_query(self, query, metadata):
        if ANNOTATE_QUERY:
            return super(Athena, self).annotate_query(query, metadata)
        return query

    @classmethod
    def type(cls):
        return "athena"

    def _get_iam_credentials(self, user=None):
        if ASSUME_ROLE:
            role_session_name = "redash" if user is None else user.email
            sts = boto3.client("sts")
            creds = sts.assume_role(
                RoleArn=self.configuration.get("iam_role"),
                RoleSessionName=role_session_name,
                ExternalId=self.configuration.get("external_id"),
            )
            return {
                "aws_access_key_id": creds["Credentials"]["AccessKeyId"],
                "aws_secret_access_key": creds["Credentials"]["SecretAccessKey"],
                "aws_session_token": creds["Credentials"]["SessionToken"],
                "region_name": self.configuration["region"],
            }
        return {
            "aws_access_key_id": self.configuration.get("aws_access_key", None),
            "aws_secret_access_key": self.configuration.get("aws_secret_key", None),
            "region_name": self.configuration["region"],
        }

    def __get_schema_from_glue(self, catalog_id=""):
        client = boto3.client("glue", **self._get_iam_credentials())
        schema = {}

        database_paginator = client.get_paginator("get_databases")
        table_paginator = client.get_paginator("get_tables")

        databases_iterator = database_paginator.paginate(
            **({"CatalogId": catalog_id} if catalog_id != "" else {}),
        )

        for databases in databases_iterator:
            for database in databases["DatabaseList"]:
                iterator = table_paginator.paginate(
                    DatabaseName=database["Name"],
                    **({"CatalogId": catalog_id} if catalog_id != "" else {}),
                )
                for table in iterator.search("TableList[]"):
                    table_name = "%s.%s" % (database["Name"], table["Name"])
                    if "StorageDescriptor" not in table:
                        logger.warning("Glue table doesn't have StorageDescriptor: %s", table_name)
                        continue
                    if table_name not in schema:
                        schema[table_name] = {"name": table_name, "columns": []}

                    for column_data in table["StorageDescriptor"]["Columns"]:
                        column = {
                            "name": column_data["Name"],
                            "type": column_data["Type"] if "Type" in column_data else None,
                        }
                        schema[table_name]["columns"].append(column)
                    for partition in table.get("PartitionKeys", []):
                        partition_column = {
                            "name": partition["Name"],
                            "type": partition["Type"] if "Type" in partition else None,
                        }
                        schema[table_name]["columns"].append(partition_column)
        return list(schema.values())

    def get_schema(self, get_stats=False):
        if self.configuration.get("glue", False):
            catalog_ids = [id.strip() for id in self.configuration.get("catalog_ids", "").split(",")]
            return sum([self.__get_schema_from_glue(catalog_id) for catalog_id in catalog_ids], [])

        schema = {}
        query = """
        SELECT table_schema, table_name, column_name, data_type
        FROM information_schema.columns
        WHERE table_schema NOT IN ('information_schema')
        """

        results, error = self.run_query(query, None)
        if error is not None:
            self._handle_run_query_error(error)

        for row in results["rows"]:
            table_name = "{0}.{1}".format(row["table_schema"], row["table_name"])
            if table_name not in schema:
                schema[table_name] = {"name": table_name, "columns": []}
            schema[table_name]["columns"].append({"name": row["column_name"], "type": row["data_type"]})

        return list(schema.values())

    def run_query(self, query, user):
        cursor = pyathena.connect(
            s3_staging_dir=self.configuration["s3_staging_dir"],
            schema_name=self.configuration.get("schema", "default"),
            encryption_option=self.configuration.get("encryption_option", None),
            kms_key=self.configuration.get("kms_key", None),
            work_group=self.configuration.get("work_group", "primary"),
            formatter=SimpleFormatter(),
            **self._get_iam_credentials(user=user),
        ).cursor()

        try:
            cursor.execute(query)
            column_tuples = [(i[0], _TYPE_MAPPINGS.get(i[1], None)) for i in cursor.description]
            columns = self.fetch_columns(column_tuples)
            rows = [dict(zip(([c["name"] for c in columns]), r)) for i, r in enumerate(cursor.fetchall())]
            qbytes = None
            athena_query_id = None
            try:
                qbytes = cursor.data_scanned_in_bytes
            except AttributeError as e:
                logger.debug("Athena Upstream can't get data_scanned_in_bytes: %s", e)
            try:
                athena_query_id = cursor.query_id
            except AttributeError as e:
                logger.debug("Athena Upstream can't get query_id: %s", e)

            price = self.configuration.get("cost_per_tb", 5)
            data = {
                "columns": columns,
                "rows": rows,
                "metadata": {
                    "data_scanned": qbytes,
                    "athena_query_id": athena_query_id,
                    "query_cost": price * qbytes * 10e-12,
                },
            }

            error = None
        except Exception:
            if cursor.query_id:
                cursor.cancel()
            raise

        return data, error


register(Athena)
