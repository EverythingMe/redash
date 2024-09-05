import datetime
import logging
import re

from dateutil.parser import parse

from redash.query_runner import (
    TYPE_BOOLEAN,
    TYPE_DATETIME,
    TYPE_FLOAT,
    TYPE_INTEGER,
    TYPE_STRING,
    BaseQueryRunner,
    register,
)
from redash.utils import json_loads, parse_human_time

logger = logging.getLogger(__name__)

try:
    import pymongo
    from bson.decimal128 import Decimal128
    from bson.json_util import JSONOptions
    from bson.json_util import object_hook as bson_object_hook
    from bson.objectid import ObjectId
    from bson.son import SON
    from bson.timestamp import Timestamp

    enabled = True

except ImportError:
    enabled = False


TYPES_MAP = {
    str: TYPE_STRING,
    bytes: TYPE_STRING,
    int: TYPE_INTEGER,
    float: TYPE_FLOAT,
    bool: TYPE_BOOLEAN,
    datetime.datetime: TYPE_DATETIME,
}


date_regex = re.compile(r'ISODate\("(.*)"\)', re.IGNORECASE)


def parse_oids(oids):
    if not isinstance(oids, list):
        raise Exception("$oids takes an array as input.")

    return [bson_object_hook({"$oid": oid}) for oid in oids]


def datetime_parser(dct):
    for k, v in dct.items():
        if isinstance(v, str):
            m = date_regex.findall(v)
            if len(m) > 0:
                dct[k] = parse(m[0], yearfirst=True)

    if "$humanTime" in dct:
        return parse_human_time(dct["$humanTime"])

    if "$oids" in dct:
        return parse_oids(dct["$oids"])

    opts = JSONOptions(tz_aware=True)
    return bson_object_hook(dct, json_options=opts)


def parse_query_json(query: str):
    query_data = json_loads(query, object_hook=datetime_parser)
    return query_data


def _get_column_by_name(columns, column_name):
    for c in columns:
        if "name" in c and c["name"] == column_name:
            return c

    return None


def _parse_dict(dic: dict, flatten: bool = False) -> dict:
    res = {}

    def _flatten(x, name=""):
        if isinstance(x, dict):
            for k, v in x.items():
                _flatten(v, "{}.{}".format(name, k))
        elif isinstance(x, list):
            for idx, item in enumerate(x):
                _flatten(item, "{}.{}".format(name, idx))
        else:
            res[name[1:]] = x

    if flatten:
        _flatten(dic)
    else:
        for key, value in dic.items():
            if isinstance(value, dict):
                for tmp_key, tmp_value in _parse_dict(value).items():
                    new_key = "{}.{}".format(key, tmp_key)
                    res[new_key] = tmp_value
            else:
                res[key] = value
    return res


def parse_results(results: list, flatten: bool = False) -> list:
    rows = []
    columns = []

    for row in results:
        parsed_row = {}

        parsed_row = _parse_dict(row, flatten)
        for column_name, value in parsed_row.items():
            if _get_column_by_name(columns, column_name) is None:
                columns.append(
                    {
                        "name": column_name,
                        "friendly_name": column_name,
                        "type": TYPES_MAP.get(type(value), TYPE_STRING),
                    }
                )

        rows.append(parsed_row)

    return rows, columns


def _sorted_fields(fields):
    ord = {}
    for k, v in fields.items():
        if isinstance(v, int):
            ord[k] = v
        else:
            ord[k] = len(fields)

    return sorted(ord, key=ord.get)


class MongoDB(BaseQueryRunner):
    should_annotate_query = False

    @classmethod
    def configuration_schema(cls):
        return {
            "type": "object",
            "properties": {
                "connectionString": {"type": "string", "title": "Connection String"},
                "username": {"type": "string"},
                "password": {"type": "string"},
                "dbName": {"type": "string", "title": "Database Name"},
                "replicaSetName": {"type": "string", "title": "Replica Set Name"},
                "readPreference": {
                    "type": "string",
                    "extendedEnum": [
                        {"value": "primaryPreferred", "name": "Primary Preferred"},
                        {"value": "primary", "name": "Primary"},
                        {"value": "secondary", "name": "Secondary"},
                        {"value": "secondaryPreferred", "name": "Secondary Preferred"},
                        {"value": "nearest", "name": "Nearest"},
                    ],
                    "title": "Replica Set Read Preference",
                },
                "flatten": {
                    "type": "string",
                    "extendedEnum": [
                        {"value": "False", "name": "False"},
                        {"value": "True", "name": "True"},
                    ],
                    "title": "Flatten Results",
                },
            },
            "secret": ["password"],
            "required": ["connectionString", "dbName"],
        }

    @classmethod
    def enabled(cls):
        return enabled

    def __init__(self, configuration):
        super(MongoDB, self).__init__(configuration)

        self.syntax = "json"

        self.db_name = self.configuration["dbName"]

        self.is_replica_set = (
            True if "replicaSetName" in self.configuration and self.configuration["replicaSetName"] else False
        )

        self.flatten = self.configuration.get("flatten", "False").upper() in ["TRUE", "YES", "ON", "1", "Y", "T"]
        logger.debug("flatten: {}".format(self.flatten))

    @classmethod
    def custom_json_encoder(cls, dec, o):
        if isinstance(o, ObjectId):
            return str(o)
        if isinstance(o, Timestamp):
            return dec.default(o.as_datetime())
        if isinstance(o, Decimal128):
            return o.to_decimal()
        return None

    def _get_db(self):
        kwargs = {}
        if self.is_replica_set:
            kwargs["replicaSet"] = self.configuration["replicaSetName"]
            readPreference = self.configuration.get("readPreference")
            if readPreference:
                kwargs["readPreference"] = readPreference

        if "username" in self.configuration:
            kwargs["username"] = self.configuration["username"]

        if "password" in self.configuration:
            kwargs["password"] = self.configuration["password"]

        db_connection = pymongo.MongoClient(self.configuration["connectionString"], **kwargs)

        return db_connection[self.db_name]

    def test_connection(self):
        db = self._get_db()
        if not db.command("connectionStatus")["ok"]:
            raise Exception("MongoDB connection error")

        return db

    def _merge_property_names(self, columns, document):
        for property in document:
            if property not in columns:
                columns.append(property)

    def _is_collection_a_view(self, db, collection_name):
        if "viewOn" in db[collection_name].options():
            return True
        return False

    def _get_collection_fields(self, db, collection_name):
        # Since MongoDB is a document based database and each document doesn't have
        # to have the same fields as another documet in the collection its a bit hard to
        # show these attributes as fields in the schema.
        #
        # For now, the logic is to take the first and last documents (last is determined
        # by the Natural Order (http://www.mongodb.org/display/DOCS/Sorting+and+Natural+Order)
        # as we don't know the correct order. In most single server installations it would be
        # fine. In replicaset when reading from non master it might not return the really last
        # document written.
        collection_is_a_view = self._is_collection_a_view(db, collection_name)
        documents_sample = []
        try:
            if collection_is_a_view:
                for d in db[collection_name].find().limit(2):
                    documents_sample.append(d)
            else:
                for d in db[collection_name].find().sort([("$natural", 1)]).limit(1):
                    documents_sample.append(d)

                for d in db[collection_name].find().sort([("$natural", -1)]).limit(1):
                    documents_sample.append(d)
        except Exception as ex:
            template = "An exception of type {0} occurred. Arguments:\n{1!r}"
            message = template.format(type(ex).__name__, ex.args)
            logger.error(message)
            return []
        columns = []
        for d in documents_sample:
            self._merge_property_names(columns, d)
        return columns

    def get_schema(self, get_stats=False):
        schema = {}
        db = self._get_db()
        for collection_name in db.list_collection_names():
            if collection_name.startswith("system."):
                continue
            columns = self._get_collection_fields(db, collection_name)
            if columns:
                schema[collection_name] = {
                    "name": collection_name,
                    "columns": sorted(columns),
                }

        return list(schema.values())

    def run_query(self, query, user):  # noqa: C901
        db = self._get_db()

        logger.debug("mongodb connection string: %s", self.configuration["connectionString"])
        logger.debug("mongodb got query: %s", query)

        try:
            query_data = parse_query_json(query)
        except ValueError as error:
            return None, f"Invalid JSON format. {error.__str__()}"

        if "collection" not in query_data:
            return None, "'collection' must have a value to run a query"

        collection = query_data["collection"]

        q = query_data.get("query", None)
        f = None

        aggregate = query_data.get("aggregate", None)
        if aggregate:
            for step in aggregate:
                if "$sort" in step:
                    sort_list = []
                    for sort_item in step["$sort"]:
                        if isinstance(sort_item, dict):
                            sort_list.append((sort_item["name"], sort_item.get("direction", 1)))
                        elif isinstance(sort_item, list):
                            sort_list.append(tuple(sort_item))
                    step["$sort"] = SON(sort_list)

        if "fields" in query_data:
            f = query_data["fields"]

        s = None
        if "sort" in query_data and query_data["sort"]:
            s = []
            for field_data in query_data["sort"]:
                if isinstance(field_data, dict):
                    s.append((field_data["name"], field_data.get("direction", 1)))
                elif isinstance(field_data, list):
                    s.append(tuple(field_data))

        columns = []
        rows = []

        cursor = None
        if q or (not q and not aggregate):
            if "count" in query_data:
                options = {opt: query_data[opt] for opt in ("skip", "limit") if opt in query_data}
                cursor = db[collection].count_documents(q, **options)
            else:
                if s:
                    cursor = db[collection].find(q, f).sort(s)
                else:
                    cursor = db[collection].find(q, f)

                if "skip" in query_data:
                    cursor = cursor.skip(query_data["skip"])

                if "limit" in query_data:
                    cursor = cursor.limit(query_data["limit"])

        elif aggregate:
            allow_disk_use = query_data.get("allowDiskUse", False)
            r = db[collection].aggregate(aggregate, allowDiskUse=allow_disk_use)

            # Backwards compatibility with older pymongo versions.
            #
            # Older pymongo version would return a dictionary from an aggregate command.
            # The dict would contain a "result" key which would hold the cursor.
            # Newer ones return pymongo.command_cursor.CommandCursor.
            if isinstance(r, dict):
                cursor = r["result"]
            else:
                cursor = r

        if "count" in query_data:
            columns.append({"name": "count", "friendly_name": "count", "type": TYPE_INTEGER})
            rows.append({"count": cursor})
        else:
            rows, columns = parse_results(cursor, flatten=self.flatten)

        if f:
            ordered_columns = []
            for k in _sorted_fields(f):
                column = _get_column_by_name(columns, k)
                if column:
                    ordered_columns.append(column)

            columns = ordered_columns
            logger.debug("columns: {}".format(columns))

        if query_data.get("sortColumns"):
            reverse = query_data["sortColumns"] == "desc"
            columns = sorted(columns, key=lambda col: col["name"], reverse=reverse)

        data = {"columns": columns, "rows": rows}
        error = None

        return data, error


register(MongoDB)
