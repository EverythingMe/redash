import json
import logging
from collections import OrderedDict


from redash.query_runner import *
from redash.utils import json_dumps, json_loads


logger = logging.getLogger(__name__)

# TODO: make this more general and move into __init__.py
class ResultSet(object):
    def __init__(self):
        self.columns = OrderedDict()
        self.rows = []

    def add_row(self, row):
        for key in row.keys():
            self.add_column(key)

        self.rows.append(row)

    def add_column(self, column, column_type=TYPE_STRING):
        if column not in self.columns:
            self.columns[column] = {
                "name": column,
                "type": column_type,
                "friendly_name": column,
            }

    def to_json(self):
        return json_dumps({"rows": self.rows, "columns": list(self.columns.values())})

    def merge(self, set):
        self.rows = self.rows + set.rows


def pct_change(current, previous):
    diff = current - previous
    change = 0
    try:
        if diff > 0:
            change = (diff / current) * 100
        elif diff < 0:
            diff = previous - current
            change = -((diff / current) * 100)
    except ZeroDivisionError:
        return float("inf")
    return float("{:.2f}".format(change))


def parse_comparision(data):
    results = ResultSet()
    try:
        nested_data = data.get("data").get("actor").get("account").get("nrql").get("results")
    except (KeyError, ValueError) as err:
        logger.error("Error Raised: %s", err)
    else:
        data_dict = {}
        for rows in nested_data:
            try:
                data_dict[rows["comparison"]] = rows["count"]
            except (KeyError, IndexError) as err:
                logger.error(f"Error adding data to dictionary, err: {err}")
        if len(data_dict) >= 1:
            data_dict['change'] = pct_change(data_dict.get('current'), data_dict.get('previous'))
        results.add_row(data_dict)
    return results


def parse_count(data):
    results = ResultSet()
    try:
        nested_data = data.get("data").get("actor").get("account").get("nrql").get("results")[0]
    except (KeyError, ValueError) as err:
        logger.error("Error Raised: %s", err)
    else:
        key_name = list(nested_data.keys())[0]
        data_count = list(nested_data.values())[0]
        results.add_row({key_name: data_count})
    return results


class NewRelicGQL(BaseHTTPQueryRunner):
    should_annotate_query = False
    response_error = "NewRelic returned unexpected status code"

    @classmethod
    def configuration_schema(cls):
        return {
            "type": "object",
            "properties": {
                "nr_account_id": {"type": "string", "title": "NewRelic Account ID"},
                "url": {"type": "string", "title": "API URL"},
                "token": {"type": "string", "title": "Security Token"},
            },
            "required": ["nr_account_id", "url", "token"],
            "secret": ["token"],
            "order": [
                "nr_account_id",
                "url",
                "token",
            ]
        }

    @classmethod
    def name(cls):
        return "NewRelic (GraphQL)"

    def test_connection(self):
        nr_account_id = str("{}".format(self.configuration["nr_account_id"]))
        qraphql_test_query = '{actor {account(id: ' + nr_account_id +') {nrql(query: "SELECT 1") {results}}}}'
        testQuery = {"queryType": "count", "query": qraphql_test_query}
        try:
            response = self.run_query(query=json.dumps(testQuery), user="test")
        except Exception as err:
            logger.info(f"Raised Exception: {err}")
            response = None
        if response is None:
            raise Exception("Failed describing objects.")
        pass

    def run_query(self, query, user):
        nr_url = "{}".format(self.configuration["url"])
        nr_token = "{}".format(self.configuration["token"])
        nr_account_id = "{}".format(self.configuration["nr_account_id"])
        headers = {
            "Content-Type": "application/json",
            "API-Key": "{}".format(nr_token),
            }

        query = json_loads(query)
        query_type = query.pop("queryType", "count")
        nrql_query = query.pop("nrql", None)
        if not nrql_query or not nr_account_id:
            return None, None

        qraphql_query = '{actor {account(id: ' + nr_account_id +') {nrql(query: "' + nrql_query + '") {results}}}}'
        payload = {"query":qraphql_query}
        response, error = self.get_response(nr_url, http_method="post", data=json.dumps(payload), headers=headers)

        if error is not None:
            return None, error
        data = response.json()

        if query_type == "count":
            results = parse_count(data)

        if query_type == "comparison":
            results = parse_comparision(data)

        return results.to_json(), None


register(NewRelicGQL)
