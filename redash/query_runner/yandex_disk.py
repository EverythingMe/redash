import logging
from importlib.util import find_spec

import requests
import yaml

from redash.query_runner import BaseSQLQueryRunner, register
from redash.utils.pandas import pandas_installed

openpyxl_installed = find_spec("openpyxl")

if pandas_installed and openpyxl_installed:
    import openpyxl  # noqa: F401
    import pandas as pd

    from redash.utils.pandas import pandas_to_result

    enabled = True

    EXTENSIONS_READERS = {
        "csv": pd.read_csv,
        "tsv": pd.read_table,
        "xls": pd.read_excel,
        "xlsx": pd.read_excel,
    }
else:
    enabled = False

logger = logging.getLogger(__name__)


class YandexDisk(BaseSQLQueryRunner):
    should_annotate_query = False

    @classmethod
    def type(cls):
        return "yandex_disk"

    @classmethod
    def name(cls):
        return "Yandex Disk"

    @classmethod
    def configuration_schema(cls):
        return {
            "type": "object",
            "properties": {
                "token": {"type": "string", "title": "OAuth Token"},
            },
            "secret": ["token"],
            "required": ["token"],
        }

    def __init__(self, configuration):
        super(YandexDisk, self).__init__(configuration)
        self.syntax = "yaml"
        self.base_url = "https://cloud-api.yandex.net/v1/disk"
        self.list_path = "counters"

    def _get_tables(self, schema):
        offset = 0
        limit = 100

        while True:
            tmp_response = self._send_query(
                "resources/public", media_type="spreadsheet,text", limit=limit, offset=offset
            )

            tmp_items = tmp_response["items"]

            for file_info in tmp_items:
                file_name = file_info["name"]
                file_path = file_info["path"].replace("disk:", "")

                file_extension = file_name.split(".")[-1].lower()
                if file_extension not in EXTENSIONS_READERS:
                    continue

                schema[file_name] = {"name": file_name, "columns": [file_path]}

            if len(tmp_items) < limit:
                break

            offset += limit

        return list(schema.values())

    def test_connection(self):
        self._send_query()

    def _send_query(self, url_path="", **kwargs):
        token = kwargs.pop("oauth_token", self.configuration["token"])
        r = requests.get(
            f"{self.base_url}/{url_path}",
            headers={"Authorization": f"OAuth {token}"},
            params=kwargs,
        )

        response_data = r.json()

        if not r.ok:
            error_message = f"Code: {r.status_code}, message: {r.text}"
            raise Exception(error_message)
        return response_data

    def run_query(self, query, user):
        logger.debug("Yandex Disk is about to execute query: %s", query)
        data = None

        if not query:
            error = "Query is empty"
            return data, error

        try:
            params = yaml.safe_load(query)
        except (ValueError, AttributeError) as e:
            logger.exception(e)
            error = f"YAML read error: {str(e)}"
            return data, error

        if not isinstance(params, dict):
            error = "The query format must be JSON or YAML"
            return data, error

        if "path" not in params:
            error = "The query must contain path"
            return data, error

        file_extension = params["path"].split(".")[-1].lower()

        read_params = {}
        is_multiple_sheets = False

        if file_extension not in EXTENSIONS_READERS:
            error = f"Unsupported file extension: {file_extension}"
            return data, error
        if file_extension in ("xls", "xlsx"):
            read_params["sheet_name"] = params.get("sheet_name", 0)
            if read_params["sheet_name"] is None:
                is_multiple_sheets = True

        file_url = self._send_query("resources/download", path=params["path"])["href"]

        try:
            df = EXTENSIONS_READERS[file_extension](file_url, **read_params)
        except Exception as e:
            logger.exception(e)
            error = f"Read file error: {str(e)}"
            return data, error

        if is_multiple_sheets:
            new_df = []
            for sheet_name, sheet_df in df.items():
                sheet_df["sheet_name"] = sheet_name
                new_df.append(sheet_df)
            new_df = pd.concat(new_df, ignore_index=True)
            df = new_df.copy()

        data = pandas_to_result(df)
        error = None

        return data, error


register(YandexDisk)
