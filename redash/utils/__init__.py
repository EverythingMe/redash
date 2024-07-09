import binascii
import codecs
import csv
import datetime
import decimal
import hashlib
import io
import json
import os
import random
import re
import sys
import uuid

import pystache
import pytz
import sqlparse
from flask import current_app
from funcy import select_values
from sqlalchemy.orm.query import Query

from redash import settings

from .human_time import parse_human_time

COMMENTS_REGEX = re.compile(r"/\*.*?\*/")
WRITER_ENCODING = os.environ.get("REDASH_CSV_WRITER_ENCODING", "utf-8")
WRITER_ERRORS = os.environ.get("REDASH_CSV_WRITER_ERRORS", "strict")


def utcnow():
    """Return datetime.now value with timezone specified.

    Without the timezone data, when the timestamp stored to the database it gets the current timezone of the server,
    which leads to errors in calculations.
    """
    return datetime.datetime.now(pytz.utc)


def dt_from_timestamp(timestamp, tz_aware=True):
    timestamp = datetime.datetime.utcfromtimestamp(float(timestamp))

    if tz_aware:
        timestamp = timestamp.replace(tzinfo=pytz.utc)

    return timestamp


def slugify(s):
    return re.sub(r"[^a-z0-9_\-]+", "-", s.lower())


def gen_query_hash(sql, parameters={}, auto_limit=False):
    """Return hash of the given query after stripping all comments, line breaks
    and multiple spaces.

    The following queries will get different ids:
        1. SELECT 1 FROM table WHERE column='Value';
        2. SELECT 1 FROM table where column='value';
    """
    sql = COMMENTS_REGEX.sub("", sql)
    sql = "".join(sql.split())

    query_parameters = {"parameters": parameters, "auto_limit": auto_limit}
    sql += "\n" + json.dumps(query_parameters, sort_keys=True, separators=(",", ":"))

    return hashlib.md5(sql.encode("utf-8")).hexdigest()


def generate_token(length):
    chars = "abcdefghijklmnopqrstuvwxyz" "ABCDEFGHIJKLMNOPQRSTUVWXYZ" "0123456789"

    rand = random.SystemRandom()
    return "".join(rand.choice(chars) for x in range(length))


class JSONEncoder(json.JSONEncoder):
    """Adapter for `json.dumps`."""

    def __init__(self, **kwargs):
        from redash.query_runner import query_runners

        self.encoders = [r.custom_json_encoder for r in query_runners.values() if hasattr(r, "custom_json_encoder")]
        super().__init__(**kwargs)

    def default(self, o):
        for encoder in self.encoders:
            result = encoder(self, o)
            if result:
                return result
        if isinstance(o, Query):
            result = list(o)
        elif isinstance(o, decimal.Decimal):
            result = float(o)
        elif isinstance(o, (datetime.timedelta, uuid.UUID)):
            result = str(o)
        # See "Date Time String Format" in the ECMA-262 specification.
        elif isinstance(o, datetime.datetime):
            result = o.isoformat()
            if o.microsecond:
                result = result[:23] + result[26:]
            if result.endswith("+00:00"):
                result = result[:-6] + "Z"
        elif isinstance(o, datetime.date):
            result = o.isoformat()
        elif isinstance(o, datetime.time):
            if o.utcoffset() is not None:
                raise ValueError("JSON can't represent timezone-aware times.")
            result = o.isoformat()
            if o.microsecond:
                result = result[:12]
        elif isinstance(o, memoryview):
            result = binascii.hexlify(o).decode()
        elif isinstance(o, bytes):
            result = binascii.hexlify(o).decode()
        else:
            result = super().default(o)
        return result


def json_loads(data, *args, **kwargs):
    """A custom JSON loading function which passes all parameters to the
    json.loads function."""
    return json.loads(data, *args, **kwargs)


def json_dumps(data, *args, **kwargs):
    """A custom JSON dumping function which passes all parameters to the
    json.dumps function."""
    kwargs.setdefault("cls", JSONEncoder)
    kwargs.setdefault("ensure_ascii", False)
    # Float value nan or inf in Python should be render to None or null in json.
    # Using allow_nan = True will make Python render nan as NaN, leading to parse error in front-end
    kwargs.setdefault("allow_nan", False)
    return json.dumps(data, *args, **kwargs)


def mustache_render(template, context=None, **kwargs):
    renderer = pystache.Renderer(escape=lambda u: u)
    return renderer.render(template, context, **kwargs)


def mustache_render_escape(template, context=None, **kwargs):
    renderer = pystache.Renderer()
    return renderer.render(template, context, **kwargs)


def build_url(request, host, path):
    parts = request.host.split(":")
    if len(parts) > 1:
        port = parts[1]
        if (port, request.scheme) not in (("80", "http"), ("443", "https")):
            host = "{}:{}".format(host, port)

    return "{}://{}{}".format(request.scheme, host, path)


class UnicodeWriter:
    """
    A CSV writer which will write rows to CSV file "f",
    which is encoded in the given encoding.
    """

    def __init__(self, f, dialect=csv.excel, encoding=WRITER_ENCODING, **kwds):
        # Redirect output to a queue
        self.queue = io.StringIO()
        self.writer = csv.writer(self.queue, dialect=dialect, **kwds)
        self.stream = f
        self.encoder = codecs.getincrementalencoder(encoding)()

    def _encode_utf8(self, val):
        if isinstance(val, str):
            return val.encode(WRITER_ENCODING, WRITER_ERRORS)

        return val

    def writerow(self, row):
        self.writer.writerow([self._encode_utf8(s) for s in row])
        # Fetch UTF-8 output from the queue ...
        data = self.queue.getvalue()
        data = data.decode(WRITER_ENCODING)
        # ... and reencode it into the target encoding
        data = self.encoder.encode(data)
        # write to the target stream
        self.stream.write(data)
        # empty queue
        self.queue.truncate(0)

    def writerows(self, rows):
        for row in rows:
            self.writerow(row)


def collect_parameters_from_request(args):
    parameters = {}

    for k, v in args.items():
        if k.startswith("p_"):
            parameters[k[2:]] = v

    return parameters


def base_url(org):
    if settings.MULTI_ORG:
        return "https://{}/{}".format(settings.HOST, org.slug)

    return settings.HOST


def filter_none(d):
    return select_values(lambda v: v is not None, d)


def to_filename(s):
    s = re.sub(r'[<>:"\\\/|?*]+', " ", s, flags=re.UNICODE)
    s = re.sub(r"\s+", "_", s, flags=re.UNICODE)
    return s.strip("_")


def deprecated():
    def wrapper(K):
        setattr(K, "deprecated", True)
        return K

    return wrapper


def render_template(path, context):
    """Render a template with context, without loading the entire app context.
    Using Flask's `render_template` function requires the entire app context to load, which in turn triggers any
    function decorated with the `context_processor` decorator, which is not explicitly required for rendering purposes.
    """
    return current_app.jinja_env.get_template(path).render(**context)
