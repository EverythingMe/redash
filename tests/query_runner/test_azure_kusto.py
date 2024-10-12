from unittest import TestCase
from unittest.mock import patch

from redash.query_runner.azure_kusto import AzureKusto


class TestAzureKusto(TestCase):
    def setUp(self):
        self.configuration = {
            "cluster": "https://example.kusto.windows.net",
            "database": "sample_db",
            "azure_ad_client_id": "client_id",
            "azure_ad_client_secret": "client_secret",
            "azure_ad_tenant_id": "tenant_id",
        }
        self.kusto = AzureKusto(self.configuration)

    @patch.object(AzureKusto, "run_query")
    def test_get_schema(self, mock_run_query):
        mock_response = {
            "rows": [
                {
                    "DatabaseSchema": '{"Databases": {"sample_db": {"Tables": {"Table1": {"Name": "Table1", "OrderedColumns": [{"Name": "Column1"}, {"Name": "Column2"}]}}, "MaterializedViews": {}}}}'
                }
            ]
        }
        mock_run_query.return_value = (mock_response, None)

        expected_schema = [{"name": "Table1", "columns": ["Column1", "Column2"]}]

        schema = self.kusto.get_schema()
        self.assertEqual(schema, expected_schema)
