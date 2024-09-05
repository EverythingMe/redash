import unicodedata
from urllib.parse import quote

import regex
from flask import make_response, request
from flask_login import current_user
from flask_restful import abort

from redash import models, settings
from redash.handlers.base import BaseResource, get_object_or_404, record_event
from redash.models.parameterized_query import (
    InvalidParameterError,
    ParameterizedQuery,
    QueryDetachedFromDataSourceError,
    dropdown_values,
)
from redash.permissions import (
    has_access,
    not_view_only,
    require_access,
    require_any_of_permission,
    require_permission,
    view_only,
)
from redash.serializers import (
    serialize_job,
    serialize_query_result,
    serialize_query_result_to_dsv,
    serialize_query_result_to_xlsx,
)
from redash.tasks import Job
from redash.tasks.queries import enqueue_query
from redash.utils import (
    collect_parameters_from_request,
    json_dumps,
    to_filename,
)


def error_response(message, http_status=400):
    return {"job": {"status": 4, "error": message}}, http_status


error_messages = {
    "unsafe_when_shared": error_response(
        "This query contains potentially unsafe parameters and cannot be executed on a shared dashboard or an embedded visualization.",
        403,
    ),
    "unsafe_on_view_only": error_response(
        "This query contains potentially unsafe parameters and cannot be executed with read-only access to this data source.",
        403,
    ),
    "no_permission": error_response("You do not have permission to run queries with this data source.", 403),
    "select_data_source": error_response("Please select data source to run this query.", 401),
    "no_data_source": error_response("Target data source not available.", 401),
}


def run_query(query, parameters, data_source, query_id, should_apply_auto_limit, max_age=0):
    if not data_source:
        return error_messages["no_data_source"]

    if data_source.paused:
        if data_source.pause_reason:
            message = "{} is paused ({}). Please try later.".format(data_source.name, data_source.pause_reason)
        else:
            message = "{} is paused. Please try later.".format(data_source.name)
        return error_response(message)

    try:
        query.apply(parameters)
    except (InvalidParameterError, QueryDetachedFromDataSourceError) as e:
        abort(400, message=str(e))

    query_text = data_source.query_runner.apply_auto_limit(query.text, should_apply_auto_limit)

    if query.missing_params:
        return error_response("Missing parameter value for: {}".format(", ".join(query.missing_params)))

    if max_age == 0:
        query_result = None
    else:
        query_result = models.QueryResult.get_latest(data_source, query_text, max_age)

    record_event(
        current_user.org,
        current_user,
        {
            "action": "execute_query",
            "cache": "hit" if query_result else "miss",
            "object_id": data_source.id,
            "object_type": "data_source",
            "query": query_text,
            "query_id": query_id,
            "parameters": parameters,
        },
    )

    if query_result:
        return {"query_result": serialize_query_result(query_result, current_user.is_api_user())}
    job = enqueue_query(
        query_text,
        data_source,
        current_user.id,
        current_user.is_api_user(),
        metadata={
            "Username": current_user.get_actual_user(),
            "query_id": query_id,
        },
    )
    return serialize_job(job)


def get_download_filename(query_result, query, filetype):
    retrieved_at = query_result.retrieved_at.strftime("%Y_%m_%d")
    if query:
        query_name = regex.sub(r"\p{C}", "", query.name)
        filename = to_filename(query_name) if query_name != "" else str(query.id)
    else:
        filename = str(query_result.id)
    return "{}_{}.{}".format(filename, retrieved_at, filetype)


def content_disposition_filenames(attachment_filename):
    if not isinstance(attachment_filename, str):
        attachment_filename = attachment_filename.decode("utf-8")

    try:
        attachment_filename = attachment_filename.encode("ascii")
    except UnicodeEncodeError:
        filenames = {
            "filename": unicodedata.normalize("NFKD", attachment_filename).encode("ascii", "ignore"),
            "filename*": "UTF-8''%s" % quote(attachment_filename, safe=b""),
        }
    else:
        filenames = {"filename": attachment_filename}

    return filenames


class QueryResultListResource(BaseResource):
    @require_permission("execute_query")
    def post(self):
        """
        Execute a query (or retrieve recent results).

        :qparam string query: The query text to execute
        :qparam number query_id: The query object to update with the result (optional)
        :qparam number max_age: If query results less than `max_age` seconds old are available,
                                return them, otherwise execute the query; if omitted or -1, returns
                                any cached result, or executes if not available. Set to zero to
                                always execute.
        :qparam number data_source_id: ID of data source to query
        :qparam object parameters: A set of parameter values to apply to the query.
        """
        params = request.get_json(force=True)

        query = params["query"]
        max_age = params.get("max_age", -1)
        # max_age might have the value of None, in which case calling int(None) will fail
        if max_age is None:
            max_age = -1
        max_age = int(max_age)
        query_id = params.get("query_id", "adhoc")
        parameters = params.get("parameters", collect_parameters_from_request(request.args))

        parameterized_query = ParameterizedQuery(query, org=self.current_org)
        should_apply_auto_limit = params.get("apply_auto_limit", False)

        data_source_id = params.get("data_source_id")
        if data_source_id:
            data_source = models.DataSource.get_by_id_and_org(params.get("data_source_id"), self.current_org)
        else:
            return error_messages["select_data_source"]

        if not has_access(data_source, self.current_user, not_view_only):
            return error_messages["no_permission"]

        return run_query(
            parameterized_query,
            parameters,
            data_source,
            query_id,
            should_apply_auto_limit,
            max_age,
        )


ONE_YEAR = 60 * 60 * 24 * 365.25


class QueryResultDropdownResource(BaseResource):
    def get(self, query_id):
        query = get_object_or_404(models.Query.get_by_id_and_org, query_id, self.current_org)
        require_access(query.data_source, current_user, view_only)
        try:
            return dropdown_values(query_id, self.current_org)
        except QueryDetachedFromDataSourceError as e:
            abort(400, message=str(e))


class QueryDropdownsResource(BaseResource):
    def get(self, query_id, dropdown_query_id):
        query = get_object_or_404(models.Query.get_by_id_and_org, query_id, self.current_org)
        require_access(query, current_user, view_only)

        related_queries_ids = [p["queryId"] for p in query.parameters if p["type"] == "query"]
        if int(dropdown_query_id) not in related_queries_ids:
            dropdown_query = get_object_or_404(models.Query.get_by_id_and_org, dropdown_query_id, self.current_org)
            require_access(dropdown_query.data_source, current_user, view_only)

        return dropdown_values(dropdown_query_id, self.current_org)


class QueryResultResource(BaseResource):
    @staticmethod
    def add_cors_headers(headers):
        if "Origin" in request.headers:
            origin = request.headers["Origin"]

            if set(["*", origin]) & settings.ACCESS_CONTROL_ALLOW_ORIGIN:
                headers["Access-Control-Allow-Origin"] = origin
                headers["Access-Control-Allow-Credentials"] = str(settings.ACCESS_CONTROL_ALLOW_CREDENTIALS).lower()

    @require_any_of_permission(("view_query", "execute_query"))
    def options(self, query_id=None, query_result_id=None, filetype="json"):
        headers = {}
        self.add_cors_headers(headers)

        if settings.ACCESS_CONTROL_REQUEST_METHOD:
            headers["Access-Control-Request-Method"] = settings.ACCESS_CONTROL_REQUEST_METHOD

        if settings.ACCESS_CONTROL_ALLOW_HEADERS:
            headers["Access-Control-Allow-Headers"] = settings.ACCESS_CONTROL_ALLOW_HEADERS

        return make_response("", 200, headers)

    @require_any_of_permission(("view_query", "execute_query"))
    def post(self, query_id):
        """
        Execute a saved query.

        :param number query_id: The ID of the query whose results should be fetched.
        :param object parameters: The parameter values to apply to the query.
        :qparam number max_age: If query results less than `max_age` seconds old are available,
                                return them, otherwise execute the query; if omitted or -1, returns
                                any cached result, or executes if not available. Set to zero to
                                always execute.
        """
        params = request.get_json(force=True, silent=True) or {}
        parameter_values = params.get("parameters", {})

        max_age = params.get("max_age", -1)
        # max_age might have the value of None, in which case calling int(None) will fail
        if max_age is None:
            max_age = -1
        max_age = int(max_age)

        query = get_object_or_404(models.Query.get_by_id_and_org, query_id, self.current_org)

        allow_executing_with_view_only_permissions = query.parameterized.is_safe
        if "apply_auto_limit" in params:
            should_apply_auto_limit = params.get("apply_auto_limit", False)
        else:
            should_apply_auto_limit = query.options.get("apply_auto_limit", False)

        if has_access(query, self.current_user, allow_executing_with_view_only_permissions):
            return run_query(
                query.parameterized,
                parameter_values,
                query.data_source,
                query_id,
                should_apply_auto_limit,
                max_age,
            )
        if not query.parameterized.is_safe:
            if current_user.is_api_user():
                return error_messages["unsafe_when_shared"]
            return error_messages["unsafe_on_view_only"]
        return error_messages["no_permission"]

    @require_any_of_permission(("view_query", "execute_query"))
    def get(self, query_id=None, query_result_id=None, filetype="json"):
        """
        Retrieve query results.

        :param number query_id: The ID of the query whose results should be fetched
        :param number query_result_id: the ID of the query result to fetch
        :param string filetype: Format to return. One of 'json', 'xlsx', or 'csv'. Defaults to 'json'.

        :<json number id: Query result ID
        :<json string query: Query that produced this result
        :<json string query_hash: Hash code for query text
        :<json object data: Query output
        :<json number data_source_id: ID of data source that produced this result
        :<json number runtime: Length of execution time in seconds
        :<json string retrieved_at: Query retrieval date/time, in ISO format
        """
        # TODO:
        # This method handles two cases: retrieving result by id & retrieving result by query id.
        # They need to be split, as they have different logic (for example, retrieving by query id
        # should check for query parameters and shouldn't cache the result).
        should_cache = query_result_id is not None

        query_result = None
        query = None

        if query_result_id:
            query_result = get_object_or_404(models.QueryResult.get_by_id_and_org, query_result_id, self.current_org)

        if query_id is not None:
            query = get_object_or_404(models.Query.get_by_id_and_org, query_id, self.current_org)

            if query_result is None and query is not None and query.latest_query_data_id is not None:
                query_result = get_object_or_404(
                    models.QueryResult.get_by_id_and_org,
                    query.latest_query_data_id,
                    self.current_org,
                )

            if query is not None and query_result is not None and self.current_user.is_api_user():
                if query.query_hash != query_result.query_hash:
                    abort(404, message="No cached result found for this query.")

        if query_result:
            require_access(query_result.data_source, self.current_user, view_only)

            if isinstance(self.current_user, models.ApiUser):
                event = {
                    "user_id": None,
                    "org_id": self.current_org.id,
                    "action": "api_get",
                    "api_key": self.current_user.name,
                    "file_type": filetype,
                    "user_agent": request.user_agent.string,
                    "ip": request.remote_addr,
                }

                if query_id:
                    event["object_type"] = "query"
                    event["object_id"] = query_id
                else:
                    event["object_type"] = "query_result"
                    event["object_id"] = query_result_id

                self.record_event(event)

            response_builders = {
                "json": self.make_json_response,
                "xlsx": self.make_excel_response,
                "csv": self.make_csv_response,
                "tsv": self.make_tsv_response,
            }
            response = response_builders[filetype](query_result)

            if len(settings.ACCESS_CONTROL_ALLOW_ORIGIN) > 0:
                self.add_cors_headers(response.headers)

            if should_cache:
                response.headers.add_header("Cache-Control", "private,max-age=%d" % ONE_YEAR)

            filename = get_download_filename(query_result, query, filetype)

            filenames = content_disposition_filenames(filename)
            response.headers.add("Content-Disposition", "attachment", **filenames)

            return response

        abort(404, message="No cached result found for this query.")

    @staticmethod
    def make_json_response(query_result):
        data = json_dumps({"query_result": query_result.to_dict()})
        headers = {"Content-Type": "application/json"}
        return make_response(data, 200, headers)

    @staticmethod
    def make_csv_response(query_result):
        headers = {"Content-Type": "text/csv; charset=UTF-8"}
        return make_response(serialize_query_result_to_dsv(query_result, ","), 200, headers)

    @staticmethod
    def make_tsv_response(query_result):
        headers = {"Content-Type": "text/tab-separated-values; charset=UTF-8"}
        return make_response(serialize_query_result_to_dsv(query_result, "\t"), 200, headers)

    @staticmethod
    def make_excel_response(query_result):
        headers = {"Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
        return make_response(serialize_query_result_to_xlsx(query_result), 200, headers)


class JobResource(BaseResource):
    def get(self, job_id, query_id=None):
        """
        Retrieve info about a running query job.
        """
        job = Job.fetch(job_id)
        return serialize_job(job)

    def delete(self, job_id):
        """
        Cancel a query job in progress.
        """
        job = Job.fetch(job_id)
        job.cancel()
