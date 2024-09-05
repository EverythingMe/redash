import datetime
import logging
from urllib.parse import urljoin

import yaml
from funcy import compact, project

from redash.query_runner import (
    TYPE_BOOLEAN,
    TYPE_DATETIME,
    TYPE_FLOAT,
    TYPE_INTEGER,
    TYPE_STRING,
    BaseHTTPQueryRunner,
    register,
)


class QueryParseError(Exception):
    pass


def parse_query(query):
    # TODO: copy paste from Metrica query runner, we should extract this into a utility
    query = query.strip()
    if query == "":
        raise QueryParseError("Query is empty.")
    try:
        params = yaml.safe_load(query)
        return params
    except ValueError as e:
        logging.exception(e)
        error = str(e)
        raise QueryParseError(error)


TYPES_MAP = {
    str: TYPE_STRING,
    bytes: TYPE_STRING,
    int: TYPE_INTEGER,
    float: TYPE_FLOAT,
    bool: TYPE_BOOLEAN,
    datetime.datetime: TYPE_DATETIME,
}


def _get_column_by_name(columns, column_name):
    for c in columns:
        if "name" in c and c["name"] == column_name:
            return c

    return None


def _get_type(value):
    return TYPES_MAP.get(type(value), TYPE_STRING)


def add_column(columns, column_name, column_type):
    if _get_column_by_name(columns, column_name) is None:
        columns.append({"name": column_name, "friendly_name": column_name, "type": column_type})


def _apply_path_search(response, path, default=None):
    if path is None:
        return response

    path_parts = path.split(".")
    path_parts.reverse()
    while len(path_parts) > 0:
        current_path = path_parts.pop()
        if current_path in response:
            response = response[current_path]
        elif default is not None:
            return default
        else:
            raise Exception("Couldn't find path {} in response.".format(path))

    return response


def _normalize_json(data, path):
    if not data:
        return None
    data = _apply_path_search(data, path)

    if isinstance(data, dict):
        data = [data]

    return data


def _sort_columns_with_fields(columns, fields):
    if fields:
        columns = compact([_get_column_by_name(columns, field) for field in fields])

    return columns


# TODO: merge the logic here with the one in MongoDB's queyr runner
def parse_json(data, fields):
    rows = []
    columns = []

    for row in data:
        parsed_row = {}

        for key in row:
            if isinstance(row[key], dict):
                for inner_key in row[key]:
                    column_name = "{}.{}".format(key, inner_key)
                    if fields and key not in fields and column_name not in fields:
                        continue

                    value = row[key][inner_key]
                    add_column(columns, column_name, _get_type(value))
                    parsed_row[column_name] = value
            else:
                if fields and key not in fields:
                    continue

                value = row[key]
                add_column(columns, key, _get_type(value))
                parsed_row[key] = row[key]

        rows.append(parsed_row)

    columns = _sort_columns_with_fields(columns, fields)

    return {"rows": rows, "columns": columns}


class JSON(BaseHTTPQueryRunner):
    requires_url = False
    base_url_title = "Base URL"

    @classmethod
    def configuration_schema(cls):
        return {
            "type": "object",
            "properties": {
                "base_url": {"type": "string", "title": cls.base_url_title},
                "username": {"type": "string", "title": cls.username_title},
                "password": {"type": "string", "title": cls.password_title},
            },
            "secret": ["password"],
            "order": ["base_url", "username", "password"],
        }

    def __init__(self, configuration):
        super(JSON, self).__init__(configuration)
        self.syntax = "yaml"

    def test_connection(self):
        pass

    def run_query(self, query, user):
        query = parse_query(query)

        data, error = self._run_json_query(query)
        if error is not None:
            return None, error

        if data:
            return data, None
        return None, "Got empty response from '{}'.".format(query["url"])

    def _run_json_query(self, query):
        if not isinstance(query, dict):
            raise QueryParseError("Query should be a YAML object describing the URL to query.")

        if "url" not in query:
            raise QueryParseError("Query must include 'url' option.")

        method = query.get("method", "get")
        request_options = project(query, ("params", "headers", "data", "auth", "json", "verify"))

        fields = query.get("fields")
        path = query.get("path")

        if "pagination" in query:
            pagination = RequestPagination.from_config(self.configuration, query["pagination"])
        else:
            pagination = None

        if isinstance(request_options.get("auth", None), list):
            request_options["auth"] = tuple(request_options["auth"])
        elif self.configuration.get("username") or self.configuration.get("password"):
            request_options["auth"] = (self.configuration.get("username"), self.configuration.get("password"))

        if method not in ("get", "post"):
            raise QueryParseError("Only GET or POST methods are allowed.")

        if fields and not isinstance(fields, list):
            raise QueryParseError("'fields' needs to be a list.")

        results, error = self._get_all_results(query["url"], method, path, pagination, **request_options)
        return parse_json(results, fields), error

    def _get_all_results(self, url, method, result_path, pagination, **request_options):
        """Get all results from a paginated endpoint."""
        base_url = self.configuration.get("base_url")
        url = urljoin(base_url, url)

        results = []
        has_more = True
        while has_more:
            response, error = self._get_json_response(url, method, **request_options)
            has_more = False

            result = _normalize_json(response, result_path)
            if result:
                results.extend(result)
                if pagination:
                    has_more, url, request_options = pagination.next(url, request_options, response)

        return results, error

    def _get_json_response(self, url, method, **request_options):
        response, error = self.get_response(url, http_method=method, **request_options)
        result = response.json() if error is None else {}
        return result, error


class RequestPagination:
    def next(self, url, request_options, response):
        """Checks the response for another page.

        Returns:
            has_more, next_url, next_request_options
        """
        return False, None, request_options

    @staticmethod
    def from_config(configuration, pagination):
        if not isinstance(pagination, dict) or not isinstance(pagination.get("type"), str):
            raise QueryParseError("'pagination' should be an object with a `type` property")

        if pagination["type"] == "url":
            return UrlPagination(pagination)
        if pagination["type"] == "token":
            return TokenPagination(pagination)

        raise QueryParseError("Unknown 'pagination.type' {}".format(pagination["type"]))


class UrlPagination(RequestPagination):
    def __init__(self, pagination):
        self.path = pagination.get("path", "_links.next.href")
        if not isinstance(self.path, str):
            raise QueryParseError("'pagination.path' should be a string")

    def next(self, url, request_options, response):
        next_url = _apply_path_search(response, self.path, "")
        if not next_url:
            return False, None, request_options

        next_url = urljoin(url, next_url)
        return True, next_url, request_options


class TokenPagination(RequestPagination):
    def __init__(self, pagination):
        self.fields = pagination.get("fields", ["next_page_token", "page_token"])
        if not isinstance(self.fields, list) or len(self.fields) != 2:
            raise QueryParseError("'pagination.fields' should be a list of 2 field names")

    def next(self, url, request_options, response):
        next_token = _apply_path_search(response, self.fields[0], "")
        if not next_token:
            return False, None, request_options

        params = request_options.get("params", {})

        # prevent infinite loop that can happen if self.fields[1] is wrong
        if next_token == params.get(self.fields[1]):
            raise Exception("{} did not change; possible misconfiguration".format(self.fields[0]))

        params[self.fields[1]] = next_token
        request_options["params"] = params
        return True, url, request_options


register(JSON)
