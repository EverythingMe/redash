import importlib
import os
import ssl

from flask_talisman import talisman
from funcy import distinct, remove

from redash.settings.helpers import (
    add_decode_responses_to_redis_url,
    array_from_string,
    cast_int_or_default,
    fix_assets_path,
    int_or_none,
    parse_boolean,
    set_from_string,
)
from redash.settings.organization import DATE_FORMAT, TIME_FORMAT  # noqa

# _REDIS_URL is the unchanged REDIS_URL we get from env vars, to be used later with RQ
_REDIS_URL = os.environ.get("REDASH_REDIS_URL", os.environ.get("REDIS_URL", "redis://localhost:6379/0"))
# This is the one to use for Redash' own connection:
REDIS_URL = add_decode_responses_to_redis_url(_REDIS_URL)
PROXIES_COUNT = int(os.environ.get("REDASH_PROXIES_COUNT", "1"))

STATSD_HOST = os.environ.get("REDASH_STATSD_HOST", "127.0.0.1")
STATSD_PORT = int(os.environ.get("REDASH_STATSD_PORT", "8125"))
STATSD_PREFIX = os.environ.get("REDASH_STATSD_PREFIX", "redash")
STATSD_USE_TAGS = parse_boolean(os.environ.get("REDASH_STATSD_USE_TAGS", "false"))

# Connection settings for Redash's own database (where we store the queries, results, etc)
SQLALCHEMY_DATABASE_URI = os.environ.get(
    "REDASH_DATABASE_URL", os.environ.get("DATABASE_URL", "postgresql:///postgres")
)
SQLALCHEMY_MAX_OVERFLOW = int_or_none(os.environ.get("SQLALCHEMY_MAX_OVERFLOW"))
SQLALCHEMY_POOL_SIZE = int_or_none(os.environ.get("SQLALCHEMY_POOL_SIZE"))
SQLALCHEMY_DISABLE_POOL = parse_boolean(os.environ.get("SQLALCHEMY_DISABLE_POOL", "false"))
SQLALCHEMY_ENABLE_POOL_PRE_PING = parse_boolean(os.environ.get("SQLALCHEMY_ENABLE_POOL_PRE_PING", "false"))
SQLALCHEMY_TRACK_MODIFICATIONS = False
SQLALCHEMY_ECHO = False

RQ_REDIS_URL = os.environ.get("RQ_REDIS_URL", _REDIS_URL)

# The following enables periodic job (every 5 minutes) of removing unused query results.
QUERY_RESULTS_CLEANUP_ENABLED = parse_boolean(os.environ.get("REDASH_QUERY_RESULTS_CLEANUP_ENABLED", "true"))
QUERY_RESULTS_CLEANUP_COUNT = int(os.environ.get("REDASH_QUERY_RESULTS_CLEANUP_COUNT", "100"))
QUERY_RESULTS_CLEANUP_MAX_AGE = int(os.environ.get("REDASH_QUERY_RESULTS_CLEANUP_MAX_AGE", "7"))

QUERY_RESULTS_EXPIRED_TTL_ENABLED = parse_boolean(os.environ.get("REDASH_QUERY_RESULTS_EXPIRED_TTL_ENABLED", "false"))
# default set query results expired ttl 86400 seconds
QUERY_RESULTS_EXPIRED_TTL = int(os.environ.get("REDASH_QUERY_RESULTS_EXPIRED_TTL", "86400"))

SCHEMAS_REFRESH_SCHEDULE = int(os.environ.get("REDASH_SCHEMAS_REFRESH_SCHEDULE", 30))
SCHEMAS_REFRESH_TIMEOUT = int(os.environ.get("REDASH_SCHEMAS_REFRESH_TIMEOUT", 300))

AUTH_TYPE = os.environ.get("REDASH_AUTH_TYPE", "api_key")
INVITATION_TOKEN_MAX_AGE = int(os.environ.get("REDASH_INVITATION_TOKEN_MAX_AGE", 60 * 60 * 24 * 7))

# The secret key to use in the Flask app for various cryptographic features
SECRET_KEY = os.environ.get("REDASH_COOKIE_SECRET")

if SECRET_KEY is None:
    raise Exception(
        "You must set the REDASH_COOKIE_SECRET environment variable. Visit http://redash.io/help/open-source/admin-guide/secrets for more information."
    )

# The secret key to use when encrypting data source options
DATASOURCE_SECRET_KEY = os.environ.get("REDASH_SECRET_KEY", SECRET_KEY)

# Whether and how to redirect non-HTTP requests to HTTPS. Disabled by default.
ENFORCE_HTTPS = parse_boolean(os.environ.get("REDASH_ENFORCE_HTTPS", "false"))
ENFORCE_HTTPS_PERMANENT = parse_boolean(os.environ.get("REDASH_ENFORCE_HTTPS_PERMANENT", "false"))
# Whether file downloads are enforced or not.
ENFORCE_FILE_SAVE = parse_boolean(os.environ.get("REDASH_ENFORCE_FILE_SAVE", "true"))

# Whether api calls using the json query runner will block private addresses
ENFORCE_PRIVATE_ADDRESS_BLOCK = parse_boolean(os.environ.get("REDASH_ENFORCE_PRIVATE_IP_BLOCK", "true"))

# Whether to use secure cookies by default.
COOKIES_SECURE = parse_boolean(os.environ.get("REDASH_COOKIES_SECURE", str(ENFORCE_HTTPS)))
# Whether the session cookie is set to secure.
SESSION_COOKIE_SECURE = parse_boolean(os.environ.get("REDASH_SESSION_COOKIE_SECURE") or str(COOKIES_SECURE))
# Whether the session cookie is set HttpOnly.
SESSION_COOKIE_HTTPONLY = parse_boolean(os.environ.get("REDASH_SESSION_COOKIE_HTTPONLY", "true"))
SESSION_EXPIRY_TIME = int(os.environ.get("REDASH_SESSION_EXPIRY_TIME", 60 * 60 * 6))

# Whether the session cookie is set to secure.
REMEMBER_COOKIE_SECURE = parse_boolean(os.environ.get("REDASH_REMEMBER_COOKIE_SECURE") or str(COOKIES_SECURE))
# Whether the remember cookie is set HttpOnly.
REMEMBER_COOKIE_HTTPONLY = parse_boolean(os.environ.get("REDASH_REMEMBER_COOKIE_HTTPONLY", "true"))
# The amount of time before the remember cookie expires.
REMEMBER_COOKIE_DURATION = int(os.environ.get("REDASH_REMEMBER_COOKIE_DURATION", 60 * 60 * 24 * 31))

# Doesn't set X-Frame-Options by default since it's highly dependent
# on the specific deployment.
# See https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/X-Frame-Options
# for more information.
FRAME_OPTIONS = os.environ.get("REDASH_FRAME_OPTIONS", "deny")
FRAME_OPTIONS_ALLOW_FROM = os.environ.get("REDASH_FRAME_OPTIONS_ALLOW_FROM", "")

# Whether and how to send Strict-Transport-Security response headers.
# See https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Strict-Transport-Security
# for more information.
HSTS_ENABLED = parse_boolean(os.environ.get("REDASH_HSTS_ENABLED") or str(ENFORCE_HTTPS))
HSTS_PRELOAD = parse_boolean(os.environ.get("REDASH_HSTS_PRELOAD", "false"))
HSTS_MAX_AGE = int(os.environ.get("REDASH_HSTS_MAX_AGE", talisman.ONE_YEAR_IN_SECS))
HSTS_INCLUDE_SUBDOMAINS = parse_boolean(os.environ.get("REDASH_HSTS_INCLUDE_SUBDOMAINS", "false"))

# Whether and how to send Content-Security-Policy response headers.
# See https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Content-Security-Policy
# for more information.
# Overriding this value via an environment variables requires setting it
# as a string in the general CSP format of a semicolon separated list of
# individual CSP directives, see https://github.com/GoogleCloudPlatform/flask-talisman#example-7
# for more information. E.g.:
CONTENT_SECURITY_POLICY = os.environ.get(
    "REDASH_CONTENT_SECURITY_POLICY",
    "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-eval'; font-src 'self' data:; img-src 'self' http: https: data: blob:; object-src 'none'; frame-ancestors 'none'; frame-src redash.io;",
)
CONTENT_SECURITY_POLICY_REPORT_URI = os.environ.get("REDASH_CONTENT_SECURITY_POLICY_REPORT_URI", "")
CONTENT_SECURITY_POLICY_REPORT_ONLY = parse_boolean(
    os.environ.get("REDASH_CONTENT_SECURITY_POLICY_REPORT_ONLY", "false")
)
CONTENT_SECURITY_POLICY_NONCE_IN = array_from_string(os.environ.get("REDASH_CONTENT_SECURITY_POLICY_NONCE_IN", ""))

# Whether and how to send Referrer-Policy response headers. Defaults to
# 'strict-origin-when-cross-origin'.
# See https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Referrer-Policy
# for more information.
REFERRER_POLICY = os.environ.get("REDASH_REFERRER_POLICY", "strict-origin-when-cross-origin")
# Whether and how to send Feature-Policy response headers. Defaults to
# an empty value.
# See https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Feature-Policy
# for more information.
FEATURE_POLICY = os.environ.get("REDASH_FEATURE_POLICY", "")

MULTI_ORG = parse_boolean(os.environ.get("REDASH_MULTI_ORG", "false"))

GOOGLE_CLIENT_ID = os.environ.get("REDASH_GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("REDASH_GOOGLE_CLIENT_SECRET", "")
GOOGLE_OAUTH_ENABLED = bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)

# If Redash is behind a proxy it might sometimes receive a X-Forwarded-Proto of HTTP
# even if your actual Redash URL scheme is HTTPS. This will cause Flask to build
# the SAML redirect URL incorrect thus failing auth. This is especially common if
# you're behind a SSL/TCP configured AWS ELB or similar.
# This setting will force the URL scheme.
SAML_SCHEME_OVERRIDE = os.environ.get("REDASH_SAML_SCHEME_OVERRIDE", "")

SAML_ENCRYPTION_PEM_PATH = os.environ.get("REDASH_SAML_ENCRYPTION_PEM_PATH", "")
SAML_ENCRYPTION_CERT_PATH = os.environ.get("REDASH_SAML_ENCRYPTION_CERT_PATH", "")
SAML_ENCRYPTION_ENABLED = SAML_ENCRYPTION_PEM_PATH != "" and SAML_ENCRYPTION_CERT_PATH != ""

# Enables the use of an externally-provided and trusted remote user via an HTTP
# header.  The "user" must be an email address.
#
# By default the trusted header is X-Forwarded-Remote-User.  You can change
# this by setting REDASH_REMOTE_USER_HEADER.
#
# Enabling this authentication method is *potentially dangerous*, and it is
# your responsibility to ensure that only a trusted frontend (usually on the
# same server) can talk to the redash backend server, otherwise people will be
# able to login as anyone they want by directly talking to the redash backend.
# You must *also* ensure that any special header in the original request is
# removed or always overwritten by your frontend, otherwise your frontend may
# pass it through to the backend unchanged.
#
# Note that redash will only check the remote user once, upon the first need
# for a login, and then set a cookie which keeps the user logged in.  Dropping
# the remote user header after subsequent requests won't automatically log the
# user out.  Doing so could be done with further work, but usually it's
# unnecessary.
#
# If you also set the organization setting auth_password_login_enabled to false,
# then your authentication will be seamless.  Otherwise a link will be presented
# on the login page to trigger remote user auth.
REMOTE_USER_LOGIN_ENABLED = parse_boolean(os.environ.get("REDASH_REMOTE_USER_LOGIN_ENABLED", "false"))
REMOTE_USER_HEADER = os.environ.get("REDASH_REMOTE_USER_HEADER", "X-Forwarded-Remote-User")

# If the organization setting auth_password_login_enabled is not false, then users will still be
# able to login through Redash instead of the LDAP server
LDAP_LOGIN_ENABLED = parse_boolean(os.environ.get("REDASH_LDAP_LOGIN_ENABLED", "false"))
# Bind LDAP using SSL. Default is False
LDAP_SSL = parse_boolean(os.environ.get("REDASH_LDAP_USE_SSL", "false"))
# Choose authentication method(SIMPLE, ANONYMOUS or NTLM). Default is SIMPLE
LDAP_AUTH_METHOD = os.environ.get("REDASH_LDAP_AUTH_METHOD", "SIMPLE")
# The LDAP directory address (ex. ldap://10.0.10.1:389)
LDAP_HOST_URL = os.environ.get("REDASH_LDAP_URL", None)
# The DN & password used to connect to LDAP to determine the identity of the user being authenticated.
# For AD this should be "org\\user".
LDAP_BIND_DN = os.environ.get("REDASH_LDAP_BIND_DN", None)
LDAP_BIND_DN_PASSWORD = os.environ.get("REDASH_LDAP_BIND_DN_PASSWORD", "")
# AD/LDAP email and display name keys
LDAP_DISPLAY_NAME_KEY = os.environ.get("REDASH_LDAP_DISPLAY_NAME_KEY", "displayName")
LDAP_EMAIL_KEY = os.environ.get("REDASH_LDAP_EMAIL_KEY", "mail")
# Prompt that should be shown above username/email field.
LDAP_CUSTOM_USERNAME_PROMPT = os.environ.get("REDASH_LDAP_CUSTOM_USERNAME_PROMPT", "LDAP/AD/SSO username:")
# LDAP Search DN TEMPLATE (for AD this should be "(sAMAccountName=%(username)s)"")
LDAP_SEARCH_TEMPLATE = os.environ.get("REDASH_LDAP_SEARCH_TEMPLATE", "(cn=%(username)s)")
# The schema to bind to (ex. cn=users,dc=ORG,dc=local)
LDAP_SEARCH_DN = os.environ.get("REDASH_LDAP_SEARCH_DN", os.environ.get("REDASH_SEARCH_DN"))

STATIC_ASSETS_PATH = fix_assets_path(os.environ.get("REDASH_STATIC_ASSETS_PATH", "../client/dist/"))
FLASK_TEMPLATE_PATH = fix_assets_path(os.environ.get("REDASH_FLASK_TEMPLATE_PATH", STATIC_ASSETS_PATH))
# Time limit (in seconds) for scheduled queries. Set this to -1 to execute without a time limit.
SCHEDULED_QUERY_TIME_LIMIT = int(os.environ.get("REDASH_SCHEDULED_QUERY_TIME_LIMIT", -1))

# Time limit (in seconds) for adhoc queries. Set this to -1 to execute without a time limit.
ADHOC_QUERY_TIME_LIMIT = int(os.environ.get("REDASH_ADHOC_QUERY_TIME_LIMIT", -1))

JOB_EXPIRY_TIME = int(os.environ.get("REDASH_JOB_EXPIRY_TIME", 3600 * 12))
JOB_DEFAULT_FAILURE_TTL = int(os.environ.get("REDASH_JOB_DEFAULT_FAILURE_TTL", 7 * 24 * 60 * 60))

LOG_LEVEL = os.environ.get("REDASH_LOG_LEVEL", "INFO")
LOG_STDOUT = parse_boolean(os.environ.get("REDASH_LOG_STDOUT", "false"))
LOG_PREFIX = os.environ.get("REDASH_LOG_PREFIX", "")
LOG_FORMAT = os.environ.get(
    "REDASH_LOG_FORMAT",
    LOG_PREFIX + "[%(asctime)s][PID:%(process)d][%(levelname)s][%(name)s] %(message)s",
)
RQ_WORKER_JOB_LOG_FORMAT = os.environ.get(
    "REDASH_RQ_WORKER_JOB_LOG_FORMAT",
    (
        LOG_PREFIX + "[%(asctime)s][PID:%(process)d][%(levelname)s][%(name)s] "
        "job.func_name=%(job_func_name)s "
        "job.id=%(job_id)s %(message)s"
    ),
)

# Mail settings:
MAIL_SERVER = os.environ.get("REDASH_MAIL_SERVER", "localhost")
MAIL_PORT = int(os.environ.get("REDASH_MAIL_PORT", 25))
MAIL_USE_TLS = parse_boolean(os.environ.get("REDASH_MAIL_USE_TLS", "false"))
MAIL_USE_SSL = parse_boolean(os.environ.get("REDASH_MAIL_USE_SSL", "false"))
MAIL_USERNAME = os.environ.get("REDASH_MAIL_USERNAME", None)
MAIL_PASSWORD = os.environ.get("REDASH_MAIL_PASSWORD", None)
MAIL_DEFAULT_SENDER = os.environ.get("REDASH_MAIL_DEFAULT_SENDER", None)
MAIL_MAX_EMAILS = os.environ.get("REDASH_MAIL_MAX_EMAILS", None)
MAIL_ASCII_ATTACHMENTS = parse_boolean(os.environ.get("REDASH_MAIL_ASCII_ATTACHMENTS", "false"))


def email_server_is_configured():
    return MAIL_DEFAULT_SENDER is not None


HOST = os.environ.get("REDASH_HOST", "")

SEND_FAILURE_EMAIL_INTERVAL = int(os.environ.get("REDASH_SEND_FAILURE_EMAIL_INTERVAL", 60))
MAX_FAILURE_REPORTS_PER_QUERY = int(os.environ.get("REDASH_MAX_FAILURE_REPORTS_PER_QUERY", 100))

ALERTS_DEFAULT_MAIL_SUBJECT_TEMPLATE = os.environ.get(
    "REDASH_ALERTS_DEFAULT_MAIL_SUBJECT_TEMPLATE", "Alert: {alert_name} changed status to {state}"
)

REDASH_ALERTS_DEFAULT_MAIL_BODY_TEMPLATE_FILE = os.environ.get(
    "REDASH_ALERTS_DEFAULT_MAIL_BODY_TEMPLATE_FILE", fix_assets_path("templates/emails/alert.html")
)

# How many requests are allowed per IP to the login page before
# being throttled?
# See https://flask-limiter.readthedocs.io/en/stable/#rate-limit-string-notation

RATELIMIT_ENABLED = parse_boolean(os.environ.get("REDASH_RATELIMIT_ENABLED", "true"))
THROTTLE_LOGIN_PATTERN = os.environ.get("REDASH_THROTTLE_LOGIN_PATTERN", "50/hour")
LIMITER_STORAGE = os.environ.get("REDASH_LIMITER_STORAGE", REDIS_URL)
THROTTLE_PASS_RESET_PATTERN = os.environ.get("REDASH_THROTTLE_PASS_RESET_PATTERN", "10/hour")

# CORS settings for the Query Result API (and possibly future external APIs).
# In most cases all you need to do is set REDASH_CORS_ACCESS_CONTROL_ALLOW_ORIGIN
# to the calling domain (or domains in a comma separated list).
ACCESS_CONTROL_ALLOW_ORIGIN = set_from_string(os.environ.get("REDASH_CORS_ACCESS_CONTROL_ALLOW_ORIGIN", ""))
ACCESS_CONTROL_ALLOW_CREDENTIALS = parse_boolean(
    os.environ.get("REDASH_CORS_ACCESS_CONTROL_ALLOW_CREDENTIALS", "false")
)
ACCESS_CONTROL_REQUEST_METHOD = os.environ.get("REDASH_CORS_ACCESS_CONTROL_REQUEST_METHOD", "GET, POST, PUT")
ACCESS_CONTROL_ALLOW_HEADERS = os.environ.get("REDASH_CORS_ACCESS_CONTROL_ALLOW_HEADERS", "Content-Type")

# Query Runners
default_query_runners = [
    "redash.query_runner.athena",
    "redash.query_runner.big_query",
    "redash.query_runner.google_spreadsheets",
    "redash.query_runner.graphite",
    "redash.query_runner.mongodb",
    "redash.query_runner.s3",
    "redash.query_runner.couchbase",
    "redash.query_runner.mysql",
    "redash.query_runner.pg",
    "redash.query_runner.url",
    "redash.query_runner.influx_db",
    "redash.query_runner.influx_db_v2",
    "redash.query_runner.elasticsearch",
    "redash.query_runner.elasticsearch2",
    "redash.query_runner.amazon_elasticsearch",
    "redash.query_runner.trino",
    "redash.query_runner.presto",
    "redash.query_runner.pinot",
    "redash.query_runner.databricks",
    "redash.query_runner.hive_ds",
    "redash.query_runner.impala_ds",
    "redash.query_runner.vertica",
    "redash.query_runner.clickhouse",
    "redash.query_runner.tinybird",
    "redash.query_runner.yandex_metrica",
    "redash.query_runner.yandex_disk",
    "redash.query_runner.rockset",
    "redash.query_runner.treasuredata",
    "redash.query_runner.sqlite",
    "redash.query_runner.mssql",
    "redash.query_runner.mssql_odbc",
    "redash.query_runner.memsql_ds",
    "redash.query_runner.jql",
    "redash.query_runner.google_analytics",
    "redash.query_runner.axibase_tsd",
    "redash.query_runner.salesforce",
    "redash.query_runner.query_results",
    "redash.query_runner.prometheus",
    "redash.query_runner.db2",
    "redash.query_runner.druid",
    "redash.query_runner.kylin",
    "redash.query_runner.drill",
    "redash.query_runner.uptycs",
    "redash.query_runner.snowflake",
    "redash.query_runner.phoenix",
    "redash.query_runner.json_ds",
    "redash.query_runner.cass",
    "redash.query_runner.dgraph",
    "redash.query_runner.azure_kusto",
    "redash.query_runner.exasol",
    "redash.query_runner.cloudwatch",
    "redash.query_runner.cloudwatch_insights",
    "redash.query_runner.corporate_memory",
    "redash.query_runner.sparql_endpoint",
    "redash.query_runner.excel",
    "redash.query_runner.csv",
    "redash.query_runner.databend",
    "redash.query_runner.nz",
    "redash.query_runner.arango",
    "redash.query_runner.google_analytics4",
    "redash.query_runner.google_search_console",
    "redash.query_runner.ignite",
    "redash.query_runner.oracle",
    "redash.query_runner.e6data",
    "redash.query_runner.risingwave",
]

enabled_query_runners = array_from_string(
    os.environ.get("REDASH_ENABLED_QUERY_RUNNERS", ",".join(default_query_runners))
)
additional_query_runners = array_from_string(os.environ.get("REDASH_ADDITIONAL_QUERY_RUNNERS", ""))
disabled_query_runners = array_from_string(os.environ.get("REDASH_DISABLED_QUERY_RUNNERS", ""))

QUERY_RUNNERS = remove(
    set(disabled_query_runners),
    distinct(enabled_query_runners + additional_query_runners),
)

dynamic_settings = importlib.import_module(
    os.environ.get("REDASH_DYNAMIC_SETTINGS_MODULE", "redash.settings.dynamic_settings")
)

# Destinations
default_destinations = [
    "redash.destinations.email",
    "redash.destinations.slack",
    "redash.destinations.webhook",
    "redash.destinations.discord",
    "redash.destinations.mattermost",
    "redash.destinations.chatwork",
    "redash.destinations.pagerduty",
    "redash.destinations.hangoutschat",
    "redash.destinations.microsoft_teams_webhook",
    "redash.destinations.asana",
    "redash.destinations.webex",
    "redash.destinations.datadog",
]

enabled_destinations = array_from_string(os.environ.get("REDASH_ENABLED_DESTINATIONS", ",".join(default_destinations)))
additional_destinations = array_from_string(os.environ.get("REDASH_ADDITIONAL_DESTINATIONS", ""))

DESTINATIONS = distinct(enabled_destinations + additional_destinations)

EVENT_REPORTING_WEBHOOKS = array_from_string(os.environ.get("REDASH_EVENT_REPORTING_WEBHOOKS", ""))

# Support for Sentry (https://getsentry.com/). Just set your Sentry DSN to enable it:
SENTRY_DSN = os.environ.get("REDASH_SENTRY_DSN", "")
SENTRY_ENVIRONMENT = os.environ.get("REDASH_SENTRY_ENVIRONMENT")

# Client side toggles:
ALLOW_SCRIPTS_IN_USER_INPUT = parse_boolean(os.environ.get("REDASH_ALLOW_SCRIPTS_IN_USER_INPUT", "false"))
DASHBOARD_REFRESH_INTERVALS = list(
    map(
        int,
        array_from_string(os.environ.get("REDASH_DASHBOARD_REFRESH_INTERVALS", "60,300,600,1800,3600,43200,86400")),
    )
)
QUERY_REFRESH_INTERVALS = list(
    map(
        int,
        array_from_string(
            os.environ.get(
                "REDASH_QUERY_REFRESH_INTERVALS",
                "60, 300, 600, 900, 1800, 3600, 7200, 10800, 14400, 18000, 21600, 25200, 28800, 32400, 36000, 39600, 43200, 86400, 604800, 1209600, 2592000",
            )
        ),
    )
)
PAGE_SIZE = int(os.environ.get("REDASH_PAGE_SIZE", 20))
PAGE_SIZE_OPTIONS = list(
    map(
        int,
        array_from_string(os.environ.get("REDASH_PAGE_SIZE_OPTIONS", "5,10,20,50,100")),
    )
)
TABLE_CELL_MAX_JSON_SIZE = int(os.environ.get("REDASH_TABLE_CELL_MAX_JSON_SIZE", 50000))

# Features:
VERSION_CHECK = parse_boolean(os.environ.get("REDASH_VERSION_CHECK", "true"))
FEATURE_DISABLE_REFRESH_QUERIES = parse_boolean(os.environ.get("REDASH_FEATURE_DISABLE_REFRESH_QUERIES", "false"))
FEATURE_SHOW_QUERY_RESULTS_COUNT = parse_boolean(os.environ.get("REDASH_FEATURE_SHOW_QUERY_RESULTS_COUNT", "true"))
FEATURE_ALLOW_CUSTOM_JS_VISUALIZATIONS = parse_boolean(
    os.environ.get("REDASH_FEATURE_ALLOW_CUSTOM_JS_VISUALIZATIONS", "true")
)
FEATURE_AUTO_PUBLISH_NAMED_QUERIES = parse_boolean(os.environ.get("REDASH_FEATURE_AUTO_PUBLISH_NAMED_QUERIES", "true"))
FEATURE_EXTENDED_ALERT_OPTIONS = parse_boolean(os.environ.get("REDASH_FEATURE_EXTENDED_ALERT_OPTIONS", "false"))

# BigQuery
BIGQUERY_HTTP_TIMEOUT = int(os.environ.get("REDASH_BIGQUERY_HTTP_TIMEOUT", "600"))

# Allow Parameters in Embeds
# WARNING: Deprecated!
# See https://discuss.redash.io/t/support-for-parameters-in-embedded-visualizations/3337 for more details.
ALLOW_PARAMETERS_IN_EMBEDS = parse_boolean(os.environ.get("REDASH_ALLOW_PARAMETERS_IN_EMBEDS", "false"))

# Enhance schema fetching
SCHEMA_RUN_TABLE_SIZE_CALCULATIONS = parse_boolean(
    os.environ.get("REDASH_SCHEMA_RUN_TABLE_SIZE_CALCULATIONS", "false")
)

# kylin
KYLIN_OFFSET = int(os.environ.get("REDASH_KYLIN_OFFSET", 0))
KYLIN_LIMIT = int(os.environ.get("REDASH_KYLIN_LIMIT", 50000))
KYLIN_ACCEPT_PARTIAL = parse_boolean(os.environ.get("REDASH_KYLIN_ACCEPT_PARTIAL", "false"))

# sqlparse
SQLPARSE_FORMAT_OPTIONS = {
    "reindent": parse_boolean(os.environ.get("SQLPARSE_FORMAT_REINDENT", "true")),
    "keyword_case": os.environ.get("SQLPARSE_FORMAT_KEYWORD_CASE", "upper"),
}

# requests
REQUESTS_ALLOW_REDIRECTS = parse_boolean(os.environ.get("REDASH_REQUESTS_ALLOW_REDIRECTS", "false"))

# Enforces CSRF token validation on API requests.
# This is turned off by default to avoid breaking any existing deployments but it is highly recommended to turn this toggle on to prevent CSRF attacks.
ENFORCE_CSRF = parse_boolean(os.environ.get("REDASH_ENFORCE_CSRF", "false"))

# Databricks

CSRF_TIME_LIMIT = int(os.environ.get("REDASH_CSRF_TIME_LIMIT", 3600 * 6))

# Email blocked domains, use delimiter comma to separated multiple domains
BLOCKED_DOMAINS = set_from_string(os.environ.get("REDASH_BLOCKED_DOMAINS", "qq.com"))
