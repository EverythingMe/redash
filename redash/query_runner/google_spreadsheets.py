import logging
import re
from base64 import b64decode

from dateutil import parser
from requests import Session
from xlsxwriter.utility import xl_col_to_name

from redash.query_runner import (
    TYPE_BOOLEAN,
    TYPE_DATETIME,
    TYPE_FLOAT,
    TYPE_INTEGER,
    TYPE_STRING,
    BaseQueryRunner,
    guess_type,
    register,
)
from redash.utils import json_loads

logger = logging.getLogger(__name__)

try:
    import google.auth
    import gspread
    from google.auth.exceptions import GoogleAuthError
    from google.oauth2.service_account import Credentials
    from gspread.exceptions import APIError
    from gspread.exceptions import WorksheetNotFound as GSWorksheetNotFound

    enabled = True
except ImportError:
    enabled = False


def _load_key(filename):
    with open(filename, "rb") as f:
        return json_loads(f.read())


def _get_columns_and_column_names(row):
    column_names = []
    columns = []
    duplicate_counter = 1

    for i, column_name in enumerate(row):
        if not column_name:
            column_name = "column_{}".format(xl_col_to_name(i))

        if column_name in column_names:
            column_name = "{}{}".format(column_name, duplicate_counter)
            duplicate_counter += 1

        column_names.append(column_name)
        columns.append({"name": column_name, "friendly_name": column_name, "type": TYPE_STRING})

    return columns, column_names


def _value_eval_list(row_values, col_types):
    value_list = []
    raw_values = zip(col_types, row_values)
    for typ, rval in raw_values:
        try:
            if rval is None or rval == "":
                val = None
            elif typ == TYPE_BOOLEAN:
                val = True if str(rval).lower() == "true" else False
            elif typ == TYPE_DATETIME:
                val = parser.parse(rval)
            elif typ == TYPE_FLOAT:
                val = float(rval)
            elif typ == TYPE_INTEGER:
                val = int(rval)
            else:
                # for TYPE_STRING and default
                val = str(rval)
            value_list.append(val)
        except (ValueError, OverflowError):
            value_list.append(rval)
    return value_list


HEADER_INDEX = 0


class WorksheetNotFoundError(Exception):
    def __init__(self, worksheet_num, worksheet_count):
        message = "Worksheet number {} not found. Spreadsheet has {} worksheets. Note that the worksheet count is zero based.".format(
            worksheet_num, worksheet_count
        )
        super(WorksheetNotFoundError, self).__init__(message)


class WorksheetNotFoundByTitleError(Exception):
    def __init__(self, worksheet_title):
        message = "Worksheet title '{}' not found.".format(worksheet_title)
        super(WorksheetNotFoundByTitleError, self).__init__(message)


def parse_query(query):
    values = query.split("|")
    key = values[0]  # key of the spreadsheet
    worksheet_pointer = ' 1'  # A default value for when a number of inputs is invalid
    if len(values) == 2:
        s = values[1].strip()
        if len(s) > 0:
            if re.match(r"^\"(.*?)\"$", s):
                # A string quoted by " means a title of worksheet
                worksheet_pointer = s[1:-1]
            else:
                if re.match(r"^\d+$", s.split()[0]):
                    # index is initialized with a space to differentiate from the title of worksheet
                    worksheet_pointer = ' '+s.split()[0]
                else:
                    worksheet_pointer = s.split()[0]
    return key, worksheet_pointer


def parse_worksheet(worksheet):
    if not worksheet:
        return {"columns": [], "rows": []}

    columns, column_names = _get_columns_and_column_names(worksheet[HEADER_INDEX])

    if len(worksheet) > 1:
        for j, value in enumerate(worksheet[HEADER_INDEX + 1]):
            columns[j]["type"] = guess_type(value)

    column_types = [c["type"] for c in columns]
    rows = [dict(zip(column_names, _value_eval_list(row, column_types))) for row in worksheet[HEADER_INDEX + 1 :]]
    data = {"columns": columns, "rows": rows}

    return data


def parse_spreadsheet(spreadsheet, worksheet_pointer):
    worksheet = None
    if worksheet_pointer[0] == ' ':  # An index of worksheet
        worksheet = spreadsheet.get_worksheet_by_index(int(worksheet_pointer))
        if worksheet is None:
            worksheet_count = len(spreadsheet.worksheets())
            raise WorksheetNotFoundError(int(worksheet_pointer), worksheet_count)
    elif worksheet_pointer[0] == ' ':  # A title of worksheet
        worksheet = spreadsheet.get_worksheet_by_title(worksheet_pointer)
        if worksheet is None:
            raise WorksheetNotFoundByTitleError(worksheet_pointer)

    worksheet_values = worksheet.get_all_values()

    return parse_worksheet(worksheet_values)


def is_url_key(key):
    return key.startswith("https://")


def parse_api_error(error):
    error_data = error.response.json()

    if "error" in error_data and "message" in error_data["error"]:
        message = error_data["error"]["message"]
    else:
        message = str(error)

    return message


class SpreadsheetWrapper:
    def __init__(self, spreadsheet):
        self.spreadsheet = spreadsheet

    def worksheets(self):
        return self.spreadsheet.worksheets()

    def get_worksheet_by_index(self, index):
        return self.spreadsheet.get_worksheet(index)

    def get_worksheet_by_title(self, title):
        try:
            return self.spreadsheet.worksheet(title)
        except GSWorksheetNotFound:
            return None


class TimeoutSession(Session):
    def request(self, *args, **kwargs):
        kwargs.setdefault("timeout", 300)
        return super(TimeoutSession, self).request(*args, **kwargs)


class GoogleSpreadsheet(BaseQueryRunner):
    should_annotate_query = False

    def __init__(self, configuration):
        super(GoogleSpreadsheet, self).__init__(configuration)
        self.syntax = "custom"

    @classmethod
    def name(cls):
        return "Google Sheets"

    @classmethod
    def type(cls):
        return "google_spreadsheets"

    @classmethod
    def enabled(cls):
        return enabled

    @classmethod
    def configuration_schema(cls):
        return {
            "type": "object",
            "properties": {"jsonKeyFile": {"type": "string", "title": "JSON Key File (ADC is used if omitted)"}},
            "required": [],
            "secret": ["jsonKeyFile"],
        }

    def _get_spreadsheet_service(self):
        scopes = ["https://spreadsheets.google.com/feeds"]

        try:
            key = json_loads(b64decode(self.configuration["jsonKeyFile"]))
            creds = Credentials.from_service_account_info(key, scopes=scopes)
        except KeyError:
            creds = google.auth.default(scopes=scopes)[0]

        timeout_session = Session()
        timeout_session.requests_session = TimeoutSession()
        spreadsheetservice = gspread.Client(auth=creds, session=timeout_session)
        spreadsheetservice.login()
        return spreadsheetservice

    def test_connection(self):
        test_spreadsheet_key = "1S0mld7LMbUad8LYlo13Os9f7eNjw57MqVC0YiCd1Jis"
        try:
            service = self._get_spreadsheet_service()
            service.open_by_key(test_spreadsheet_key).worksheets()
        except APIError as e:
            logger.exception(e)
            message = parse_api_error(e)
            raise Exception(message)
        except GoogleAuthError as e:
            logger.exception(e)
            raise Exception(str(e))

    def run_query(self, query, user):
        logger.debug("Spreadsheet is about to execute query: %s", query)
        key, worksheet_pointer = parse_query(query)

        try:
            spreadsheet_service = self._get_spreadsheet_service()

            if is_url_key(key):
                spreadsheet = spreadsheet_service.open_by_url(key)
            else:
                spreadsheet = spreadsheet_service.open_by_key(key)

            data = parse_spreadsheet(SpreadsheetWrapper(spreadsheet), worksheet_pointer)

            return data, None
        except gspread.SpreadsheetNotFound:
            return (
                None,
                "Spreadsheet ({}) not found. Make sure you used correct id.".format(key),
            )
        except APIError as e:
            return None, parse_api_error(e)


register(GoogleSpreadsheet)
