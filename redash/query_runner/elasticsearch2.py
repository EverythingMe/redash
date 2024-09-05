import json
import logging
from typing import Optional, Tuple

from redash.query_runner import (
    TYPE_BOOLEAN,
    TYPE_DATE,
    TYPE_FLOAT,
    TYPE_INTEGER,
    TYPE_STRING,
    BaseHTTPQueryRunner,
    register,
)

logger = logging.getLogger(__name__)

ELASTICSEARCH_TYPES_MAPPING = {
    "integer": TYPE_INTEGER,
    "long": TYPE_INTEGER,
    "float": TYPE_FLOAT,
    "double": TYPE_FLOAT,
    "boolean": TYPE_BOOLEAN,
    "string": TYPE_STRING,
    "date": TYPE_DATE,
    "object": TYPE_STRING,
}


TYPES_MAP = {
    str: TYPE_STRING,
    int: TYPE_INTEGER,
    float: TYPE_FLOAT,
    bool: TYPE_BOOLEAN,
}


class ElasticSearch2(BaseHTTPQueryRunner):
    should_annotate_query = False

    @classmethod
    def name(cls):
        return "Elasticsearch"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.syntax = "json"

    def get_response(self, url, auth=None, http_method="get", **kwargs):
        url = "{}{}".format(self.configuration["server"], url)
        headers = kwargs.pop("headers", {})
        headers["Accept"] = "application/json"
        return super().get_response(url, auth, http_method, headers=headers, **kwargs)

    def test_connection(self):
        _, error = self.get_response("/_cluster/health")
        if error is not None:
            raise Exception(error)

    def run_query(self, query, user):
        query, url, result_fields = self._build_query(query)
        response, error = self.get_response(url, http_method="post", json=query)
        query_results = response.json()
        data = self._parse_results(result_fields, query_results)
        error = None
        return data, error

    def _build_query(self, query: str) -> Tuple[dict, str, Optional[list]]:
        query = json.loads(query)
        index_name = query.pop("index", "")
        result_fields = query.pop("result_fields", None)
        url = "/{}/_search".format(index_name)
        return query, url, result_fields

    @classmethod
    def _parse_mappings(cls, mappings_data: dict):
        mappings = {}

        def _parse_properties(prefix: str, properties: dict):
            for property_name, property_data in properties.items():
                if property_name not in mappings:
                    property_type = property_data.get("type", None)
                    nested_properties = property_data.get("properties", None)
                    if property_type:
                        mappings[index_name][prefix + property_name] = ELASTICSEARCH_TYPES_MAPPING.get(
                            property_type, TYPE_STRING
                        )
                    elif nested_properties:
                        new_prefix = prefix + property_name + "."
                        _parse_properties(new_prefix, nested_properties)

        for index_name in mappings_data:
            mappings[index_name] = {}
            index_mappings = mappings_data[index_name]
            try:
                for m in index_mappings.get("mappings", {}):
                    _parse_properties("", index_mappings["mappings"][m]["properties"])
            except KeyError:
                _parse_properties("", index_mappings["mappings"]["properties"])

        return mappings

    def get_mappings(self):
        response, error = self.get_response("/_mappings")
        return self._parse_mappings(response.json())

    def get_schema(self, *args, **kwargs):
        schema = {}
        for name, columns in self.get_mappings().items():
            schema[name] = {"name": name, "columns": list(columns.keys())}
        return list(schema.values())

    @classmethod
    def _parse_results(cls, result_fields, raw_result):  # noqa: C901
        result_columns = []
        result_rows = []
        result_columns_index = {c["name"]: c for c in result_columns}
        result_fields_index = {}

        def add_column_if_needed(column_name, value=None):
            if column_name not in result_columns_index:
                result_columns.append(
                    {
                        "name": column_name,
                        "friendly_name": column_name,
                        "type": TYPES_MAP.get(type(value), TYPE_STRING),
                    }
                )
                result_columns_index[column_name] = result_columns[-1]

        def get_row(rows, row):
            if row is None:
                row = {}
                rows.append(row)
            return row

        def collect_value(row, key, value):
            if result_fields and key not in result_fields_index:
                return

            add_column_if_needed(key, value)
            row[key] = value

        def parse_bucket_to_row(data, row, agg_key):
            sub_agg_key = ""
            for key, item in data.items():
                if key == "key_as_string":
                    continue
                if key == "key":
                    if "key_as_string" in data:
                        collect_value(row, agg_key, data["key_as_string"])
                    else:
                        collect_value(row, agg_key, data["key"])
                    continue

                if isinstance(item, (str, int, float)):
                    collect_value(row, agg_key + "." + key, item)
                elif isinstance(item, dict):
                    if "buckets" not in item:
                        for sub_key, sub_item in item.items():
                            collect_value(
                                row,
                                agg_key + "." + key + "." + sub_key,
                                sub_item,
                            )
                    else:
                        sub_agg_key = key

            return sub_agg_key

        def parse_buckets_list(rows, parent_key, data, row, depth):
            if len(rows) > 0 and depth == 0:
                row = rows.pop()

            for value in data:
                row = row.copy()
                sub_agg_key = parse_bucket_to_row(value, row, parent_key)

                if sub_agg_key == "":
                    rows.append(row)
                else:
                    depth += 1
                    parse_buckets_list(rows, sub_agg_key, value[sub_agg_key]["buckets"], row, depth)

        def collect_aggregations(rows, parent_key, data, row, depth):
            row = get_row(rows, row)
            parse_bucket_to_row(data, row, parent_key)

            if "buckets" in data:
                parse_buckets_list(rows, parent_key, data["buckets"], row, depth)

            return None

        def get_flatten_results(dd, separator=".", prefix=""):
            if isinstance(dd, dict):
                return {
                    prefix + separator + k if prefix else k: v
                    for kk, vv in dd.items()
                    for k, v in get_flatten_results(vv, separator, kk).items()
                }
            elif isinstance(dd, list) and len(dd) == 1:
                return {prefix: dd[0]}
            else:
                return {prefix: dd}

        if result_fields:
            for r in result_fields:
                result_fields_index[r] = None

        if "error" in raw_result:
            error = raw_result["error"]
            if len(error) > 10240:
                error = error[:10240] + "... continues"

            raise Exception(error)
        if "aggregations" in raw_result:
            for key, data in raw_result["aggregations"].items():
                collect_aggregations(result_rows, key, data, None, 0)
        if "hits" in raw_result and "hits" in raw_result["hits"]:
            for h in raw_result["hits"]["hits"]:
                row = {}

                fields_parameter_name = "_source" if "_source" in h else "fields"
                for column in h[fields_parameter_name]:
                    if result_fields and column not in result_fields_index:
                        continue

                    unested_results = get_flatten_results({column: h[fields_parameter_name][column]})

                    for column_name, value in unested_results.items():
                        add_column_if_needed(column_name, value=value)
                        row[column_name] = value

                result_rows.append(row)
        else:
            raise Exception("Redash failed to parse the results it got from Elasticsearch.")

        return {"columns": result_columns, "rows": result_rows}


class OpenDistroSQLElasticSearch(ElasticSearch2):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.syntax = "sql"

    def _build_query(self, query: str) -> Tuple[dict, str, Optional[list]]:
        sql_query = {"query": query}
        sql_query_url = "/_opendistro/_sql"
        return sql_query, sql_query_url, None

    @classmethod
    def name(cls):
        return "Open Distro SQL Elasticsearch"

    @classmethod
    def type(cls):
        return "elasticsearch2_OpenDistroSQLElasticSearch"


class XPackSQLElasticSearch(ElasticSearch2):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.syntax = "sql"

    def _build_query(self, query: str) -> Tuple[dict, str, Optional[list]]:
        sql_query = {"query": query}
        sql_query_url = "/_xpack/sql"
        return sql_query, sql_query_url, None

    @classmethod
    def _parse_results(cls, result_fields, raw_result):
        error = raw_result.get("error")
        if error:
            raise Exception(error)

        rv = {
            "columns": [
                {
                    "name": c["name"],
                    "friendly_name": c["name"],
                    "type": ELASTICSEARCH_TYPES_MAPPING.get(c["type"], "string"),
                }
                for c in raw_result["columns"]
            ],
            "rows": [],
        }
        query_results_rows = raw_result["rows"]

        for query_results_row in query_results_rows:
            result_row = dict()
            for column, column_value in zip(rv["columns"], query_results_row):
                result_row[column["name"]] = column_value
            rv["rows"].append(result_row)

        return rv

    @classmethod
    def name(cls):
        return "X-Pack SQL Elasticsearch"

    @classmethod
    def type(cls):
        return "elasticsearch2_XPackSQLElasticSearch"


register(ElasticSearch2)
register(OpenDistroSQLElasticSearch)
register(XPackSQLElasticSearch)
