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
from redash.utils import json_loads

try:
    from azure.kusto.data import (
        ClientRequestProperties,
        KustoClient,
        KustoConnectionStringBuilder,
    )
    from azure.kusto.data.exceptions import KustoServiceError

    enabled = True
except ImportError:
    enabled = False

TYPES_MAP = {
    "boolean": TYPE_BOOLEAN,
    "datetime": TYPE_DATETIME,
    "date": TYPE_DATE,
    "dynamic": TYPE_STRING,
    "guid": TYPE_STRING,
    "int": TYPE_INTEGER,
    "long": TYPE_INTEGER,
    "real": TYPE_FLOAT,
    "string": TYPE_STRING,
    "timespan": TYPE_STRING,
    "decimal": TYPE_FLOAT,
}


def _get_data_scanned(kusto_response):
    try:
        metadata_table = next(
            (table for table in kusto_response.tables if table.table_name == "QueryCompletionInformation"),
            None,
        )

        if metadata_table:
            resource_usage_json = next(
                (row["Payload"] for row in metadata_table.rows if row["EventTypeName"] == "QueryResourceConsumption"),
                "{}",
            )
            resource_usage = json_loads(resource_usage_json).get("resource_usage", {})

        data_scanned = (
            resource_usage["cache"]["shards"]["cold"]["hitbytes"]
            + resource_usage["cache"]["shards"]["cold"]["missbytes"]
            + resource_usage["cache"]["shards"]["hot"]["hitbytes"]
            + resource_usage["cache"]["shards"]["hot"]["missbytes"]
            + resource_usage["cache"]["shards"]["bypassbytes"]
        )

    except Exception:
        data_scanned = 0

    return int(data_scanned)


class AzureKusto(BaseQueryRunner):
    should_annotate_query = False
    noop_query = "let noop = datatable (Noop:string)[1]; noop"

    def __init__(self, configuration):
        super(AzureKusto, self).__init__(configuration)
        self.syntax = "custom"

    @classmethod
    def configuration_schema(cls):
        return {
            "type": "object",
            "properties": {
                "cluster": {"type": "string"},
                "azure_ad_client_id": {"type": "string", "title": "Azure AD Client ID"},
                "azure_ad_client_secret": {
                    "type": "string",
                    "title": "Azure AD Client Secret",
                },
                "azure_ad_tenant_id": {"type": "string", "title": "Azure AD Tenant Id"},
                "database": {"type": "string"},
                "msi": {"type": "boolean", "title": "Use Managed Service Identity"},
                "user_msi": {
                    "type": "string",
                    "title": "User-assigned managed identity client ID",
                },
            },
            "required": [
                "cluster",
                "database",
            ],
            "order": [
                "cluster",
                "azure_ad_client_id",
                "azure_ad_client_secret",
                "azure_ad_tenant_id",
                "database",
            ],
            "secret": ["azure_ad_client_secret"],
        }

    @classmethod
    def enabled(cls):
        return enabled

    @classmethod
    def type(cls):
        return "azure_kusto"

    @classmethod
    def name(cls):
        return "Azure Data Explorer (Kusto)"

    def run_query(self, query, user):
        cluster = self.configuration["cluster"]
        msi = self.configuration.get("msi", False)
        # Managed Service Identity(MSI)
        if msi:
            # If user-assigned managed identity is used, the client ID must be provided
            if self.configuration.get("user_msi"):
                kcsb = KustoConnectionStringBuilder.with_aad_managed_service_identity_authentication(
                    cluster,
                    client_id=self.configuration["user_msi"],
                )
            else:
                kcsb = KustoConnectionStringBuilder.with_aad_managed_service_identity_authentication(cluster)
        # Service Principal auth
        else:
            aad_app_id = self.configuration.get("azure_ad_client_id")
            app_key = self.configuration.get("azure_ad_client_secret")
            authority_id = self.configuration.get("azure_ad_tenant_id")

            if not (aad_app_id and app_key and authority_id):
                raise ValueError(
                    "Azure AD Client ID, Client Secret, and Tenant ID are required for Service Principal authentication."
                )

            kcsb = KustoConnectionStringBuilder.with_aad_application_key_authentication(
                connection_string=cluster,
                aad_app_id=aad_app_id,
                app_key=app_key,
                authority_id=authority_id,
            )

        client = KustoClient(kcsb)

        request_properties = ClientRequestProperties()
        request_properties.application = "redash"

        if user:
            request_properties.user = user.email
            request_properties.set_option("request_description", user.email)

        db = self.configuration["database"]
        try:
            response = client.execute(db, query, request_properties)

            result_cols = response.primary_results[0].columns
            result_rows = response.primary_results[0].rows

            columns = []
            rows = []
            for c in result_cols:
                columns.append(
                    {
                        "name": c.column_name,
                        "friendly_name": c.column_name,
                        "type": TYPES_MAP.get(c.column_type, None),
                    }
                )

            # rows must be [{'column1': value, 'column2': value}]
            for row in result_rows:
                rows.append(row.to_dict())

            error = None
            data = {
                "columns": columns,
                "rows": rows,
                "metadata": {"data_scanned": _get_data_scanned(response)},
            }

        except KustoServiceError as err:
            data = None
            error = str(err)

        return data, error

    def get_schema(self, get_stats=False):
        query = ".show database schema as json"

        results, error = self.run_query(query, None)

        if error is not None:
            self._handle_run_query_error(error)

        schema_as_json = json_loads(results["rows"][0]["DatabaseSchema"])
        tables_list = [
            *(schema_as_json["Databases"][self.configuration["database"]]["Tables"].values()),
            *(schema_as_json["Databases"][self.configuration["database"]]["MaterializedViews"].values()),
        ]

        schema = {}

        for table in tables_list:
            table_name = table["Name"]

            if table_name not in schema:
                schema[table_name] = {"name": table_name, "columns": []}

            for column in table["OrderedColumns"]:
                schema[table_name]["columns"].append(column["Name"])

        return list(schema.values())


register(AzureKusto)
