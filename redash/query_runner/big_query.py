import datetime
import logging
import socket
import time
from base64 import b64decode

from redash import settings
from redash.query_runner import (
    TYPE_BOOLEAN,
    TYPE_DATETIME,
    TYPE_FLOAT,
    TYPE_INTEGER,
    TYPE_STRING,
    BaseQueryRunner,
    InterruptException,
    JobTimeoutException,
    register,
)
from redash.utils import json_loads

logger = logging.getLogger(__name__)

try:
    import apiclient.errors
    import google.auth
    from apiclient.discovery import build
    from apiclient.errors import HttpError  # noqa: F401
    from google.oauth2.service_account import Credentials

    enabled = True
except ImportError:
    enabled = False

types_map = {
    "INTEGER": TYPE_INTEGER,
    "FLOAT": TYPE_FLOAT,
    "BOOLEAN": TYPE_BOOLEAN,
    "STRING": TYPE_STRING,
    "TIMESTAMP": TYPE_DATETIME,
}


def transform_cell(field_type, cell_value):
    if cell_value is None:
        return None
    if field_type == "INTEGER":
        return int(cell_value)
    if field_type == "FLOAT":
        return float(cell_value)
    if field_type == "BOOLEAN":
        return cell_value.lower() == "true"
    if field_type == "TIMESTAMP":
        return datetime.datetime.fromtimestamp(float(cell_value))
    return cell_value


def transform_row(row, fields):
    row_data = {}

    for column_index, cell in enumerate(row["f"]):
        field = fields[column_index]
        if field.get("mode") == "REPEATED":
            cell_value = [transform_cell(field["type"], item["v"]) for item in cell["v"]]
        else:
            cell_value = transform_cell(field["type"], cell["v"])

        row_data[field["name"]] = cell_value

    return row_data


def _load_key(filename):
    f = open(filename, "rb")
    try:
        return f.read()
    finally:
        f.close()


def _get_query_results(jobs, project_id, location, job_id, start_index):
    query_reply = jobs.getQueryResults(
        projectId=project_id, location=location, jobId=job_id, startIndex=start_index
    ).execute()
    logging.debug("query_reply %s", query_reply)
    if not query_reply["jobComplete"]:
        time.sleep(10)
        return _get_query_results(jobs, project_id, location, job_id, start_index)

    return query_reply


def _get_total_bytes_processed_for_resp(bq_response):
    # BigQuery hides the total bytes processed for queries to tables with row-level access controls.
    # For these queries the "totalBytesProcessed" field may not be defined in the response.
    return int(bq_response.get("totalBytesProcessed", "0"))


class BigQuery(BaseQueryRunner):
    noop_query = "SELECT 1"

    def __init__(self, configuration):
        super().__init__(configuration)
        self.should_annotate_query = configuration.get("useQueryAnnotation", False)

    @classmethod
    def enabled(cls):
        return enabled

    @classmethod
    def configuration_schema(cls):
        return {
            "type": "object",
            "properties": {
                "projectId": {"type": "string", "title": "Project ID"},
                "jsonKeyFile": {"type": "string", "title": "JSON Key File (ADC is used if omitted)"},
                "totalMBytesProcessedLimit": {
                    "type": "number",
                    "title": "Scanned Data Limit (MB)",
                },
                "userDefinedFunctionResourceUri": {
                    "type": "string",
                    "title": "UDF Source URIs (i.e. gs://bucket/date_utils.js, gs://bucket/string_utils.js )",
                },
                "useStandardSql": {
                    "type": "boolean",
                    "title": "Use Standard SQL",
                    "default": True,
                },
                "location": {"type": "string", "title": "Processing Location"},
                "loadSchema": {"type": "boolean", "title": "Load Schema"},
                "maximumBillingTier": {
                    "type": "number",
                    "title": "Maximum Billing Tier",
                },
                "useQueryAnnotation": {
                    "type": "boolean",
                    "title": "Use Query Annotation",
                    "default": False,
                },
            },
            "required": ["projectId"],
            "order": [
                "projectId",
                "jsonKeyFile",
                "loadSchema",
                "useStandardSql",
                "location",
                "totalMBytesProcessedLimit",
                "maximumBillingTier",
                "userDefinedFunctionResourceUri",
                "useQueryAnnotation",
            ],
            "secret": ["jsonKeyFile"],
        }

    def _get_bigquery_service(self):
        socket.setdefaulttimeout(settings.BIGQUERY_HTTP_TIMEOUT)

        scopes = [
            "https://www.googleapis.com/auth/bigquery",
            "https://www.googleapis.com/auth/drive",
        ]

        try:
            key = json_loads(b64decode(self.configuration["jsonKeyFile"]))
            creds = Credentials.from_service_account_info(key, scopes=scopes)
        except KeyError:
            creds = google.auth.default(scopes=scopes)[0]

        return build("bigquery", "v2", credentials=creds, cache_discovery=False)

    def _get_project_id(self):
        return self.configuration["projectId"]

    def _get_location(self):
        return self.configuration.get("location")

    def _get_total_bytes_processed(self, jobs, query):
        job_data = {"query": query, "dryRun": True}

        if self._get_location():
            job_data["location"] = self._get_location()

        if self.configuration.get("useStandardSql", False):
            job_data["useLegacySql"] = False

        response = jobs.query(projectId=self._get_project_id(), body=job_data).execute()
        return _get_total_bytes_processed_for_resp(response)

    def _get_job_data(self, query):
        job_data = {"configuration": {"query": {"query": query}}}

        if self._get_location():
            job_data["jobReference"] = {"location": self._get_location()}

        if self.configuration.get("useStandardSql", False):
            job_data["configuration"]["query"]["useLegacySql"] = False

        if self.configuration.get("userDefinedFunctionResourceUri"):
            resource_uris = self.configuration["userDefinedFunctionResourceUri"].split(",")
            job_data["configuration"]["query"]["userDefinedFunctionResources"] = [
                {"resourceUri": resource_uri} for resource_uri in resource_uris
            ]

        if "maximumBillingTier" in self.configuration:
            job_data["configuration"]["query"]["maximumBillingTier"] = self.configuration["maximumBillingTier"]

        return job_data

    def _get_query_result(self, jobs, query):
        project_id = self._get_project_id()
        job_data = self._get_job_data(query)
        insert_response = jobs.insert(projectId=project_id, body=job_data).execute()
        self.current_job_id = insert_response["jobReference"]["jobId"]
        current_row = 0
        query_reply = _get_query_results(
            jobs,
            project_id=project_id,
            location=self._get_location(),
            job_id=self.current_job_id,
            start_index=current_row,
        )

        logger.debug("bigquery replied: %s", query_reply)

        rows = []

        while ("rows" in query_reply) and current_row < int(query_reply["totalRows"]):
            for row in query_reply["rows"]:
                rows.append(transform_row(row, query_reply["schema"]["fields"]))

            current_row += len(query_reply["rows"])

            query_result_request = {
                "projectId": project_id,
                "jobId": query_reply["jobReference"]["jobId"],
                "startIndex": current_row,
            }

            if self._get_location():
                query_result_request["location"] = self._get_location()

            query_reply = jobs.getQueryResults(**query_result_request).execute()

        columns = [
            {
                "name": f["name"],
                "friendly_name": f["name"],
                "type": "string" if f.get("mode") == "REPEATED" else types_map.get(f["type"], "string"),
            }
            for f in query_reply["schema"]["fields"]
        ]

        data = {
            "columns": columns,
            "rows": rows,
            "metadata": {"data_scanned": _get_total_bytes_processed_for_resp(query_reply)},
        }

        return data

    def _get_columns_schema(self, table_data):
        columns = []
        for column in table_data.get("schema", {}).get("fields", []):
            columns.extend(self._get_columns_schema_column(column))

        project_id = self._get_project_id()
        table_name = table_data["id"].replace("%s:" % project_id, "")
        return {"name": table_name, "columns": columns}

    def _get_columns_schema_column(self, column):
        columns = []
        if column["type"] == "RECORD":
            for field in column["fields"]:
                columns.append("{}.{}".format(column["name"], field["name"]))
        else:
            columns.append(column["name"])

        return columns

    def _get_project_datasets(self, project_id):
        result = []
        service = self._get_bigquery_service()

        datasets = service.datasets().list(projectId=project_id).execute()
        result.extend(datasets.get("datasets", []))
        nextPageToken = datasets.get("nextPageToken", None)

        while nextPageToken is not None:
            datasets = service.datasets().list(projectId=project_id, pageToken=nextPageToken).execute()
            result.extend(datasets.get("datasets", []))
            nextPageToken = datasets.get("nextPageToken", None)

        return result

    def get_schema(self, get_stats=False):
        if not self.configuration.get("loadSchema", False):
            return []

        project_id = self._get_project_id()
        datasets = self._get_project_datasets(project_id)

        query_base = """
        SELECT table_schema, table_name, field_path
        FROM `{dataset_id}`.INFORMATION_SCHEMA.COLUMN_FIELD_PATHS
        WHERE table_schema NOT IN ('information_schema')
        """

        schema = {}
        queries = []
        for dataset in datasets:
            dataset_id = dataset["datasetReference"]["datasetId"]
            query = query_base.format(dataset_id=dataset_id)
            queries.append(query)

        query = "\nUNION ALL\n".join(queries)
        results, error = self.run_query(query, None)
        if error is not None:
            self._handle_run_query_error(error)

        for row in results["rows"]:
            table_name = "{0}.{1}".format(row["table_schema"], row["table_name"])
            if table_name not in schema:
                schema[table_name] = {"name": table_name, "columns": []}
            schema[table_name]["columns"].append(row["field_path"])

        return list(schema.values())

    def run_query(self, query, user):
        logger.debug("BigQuery got query: %s", query)

        bigquery_service = self._get_bigquery_service()
        jobs = bigquery_service.jobs()

        try:
            if "totalMBytesProcessedLimit" in self.configuration:
                limitMB = self.configuration["totalMBytesProcessedLimit"]
                processedMB = self._get_total_bytes_processed(jobs, query) / 1000.0 / 1000.0
                if limitMB < processedMB:
                    return (
                        None,
                        "Larger than %d MBytes will be processed (%f MBytes)" % (limitMB, processedMB),
                    )

            data = self._get_query_result(jobs, query)
            error = None

        except apiclient.errors.HttpError as e:
            data = None
            if e.resp.status in [400, 404]:
                error = json_loads(e.content)["error"]["message"]
            else:
                error = e.content
        except (KeyboardInterrupt, InterruptException, JobTimeoutException):
            if self.current_job_id:
                self._get_bigquery_service().jobs().cancel(
                    projectId=self._get_project_id(),
                    jobId=self.current_job_id,
                    location=self._get_location(),
                ).execute()

            raise

        return data, error


register(BigQuery)
