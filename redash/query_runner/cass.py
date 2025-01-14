import logging
import os
import ssl
from base64 import b64decode
from tempfile import NamedTemporaryFile

from redash.query_runner import BaseQueryRunner, register

logger = logging.getLogger(__name__)

try:
    from cassandra.auth import PlainTextAuthProvider
    from cassandra.cluster import Cluster
    from cassandra.util import sortedset

    enabled = True
except ImportError:
    enabled = False


def generate_ssl_options_dict(protocol, cert_path=None):
    ssl_options = {"ssl_version": getattr(ssl, protocol)}
    if cert_path is not None:
        ssl_options["ca_certs"] = cert_path
        ssl_options["cert_reqs"] = ssl.CERT_REQUIRED
    return ssl_options


class Cassandra(BaseQueryRunner):
    noop_query = "SELECT dateof(now()) FROM system.local"

    @classmethod
    def enabled(cls):
        return enabled

    @classmethod
    def custom_json_encoder(cls, dec, o):
        if isinstance(o, sortedset):
            return list(o)
        return None

    @classmethod
    def configuration_schema(cls):
        return {
            "type": "object",
            "properties": {
                "host": {"type": "string"},
                "port": {"type": "number", "default": 9042},
                "keyspace": {"type": "string", "title": "Keyspace name"},
                "username": {"type": "string", "title": "Username"},
                "password": {"type": "string", "title": "Password"},
                "protocol": {
                    "type": "number",
                    "title": "Protocol Version",
                    "default": 3,
                },
                "timeout": {"type": "number", "title": "Timeout", "default": 10},
                "useSsl": {"type": "boolean", "title": "Use SSL", "default": False},
                "sslCertificateFile": {"type": "string", "title": "SSL Certificate File"},
                "sslClientCertificateFile": {"type": "string", "title": "SSL Client Certificate File"},
                "sslClientKeyFile": {"type": "string", "title": "SSL Client Private Key File"},
                "sslProtocol": {
                    "type": "string",
                    "title": "SSL Protocol",
                    "enum": [
                        "PROTOCOL_SSLv23",
                        "PROTOCOL_TLS",
                        "PROTOCOL_TLS_CLIENT",
                        "PROTOCOL_TLS_SERVER",
                        "PROTOCOL_TLSv1",
                        "PROTOCOL_TLSv1_1",
                        "PROTOCOL_TLSv1_2",
                    ],
                },
            },
            "required": ["keyspace", "host", "useSsl"],
            "secret": ["sslCertificateFile", "sslClientCertificateFile", "sslClientKeyFile"],
        }

    @classmethod
    def type(cls):
        return "Cassandra"

    def get_schema(self, get_stats=False):
        query = """
        select release_version from system.local;
        """
        results, error = self.run_query(query, None)
        release_version = results["rows"][0]["release_version"]

        query = """
        SELECT table_name, column_name
        FROM system_schema.columns
        WHERE keyspace_name ='{}';
        """.format(
            self.configuration["keyspace"]
        )

        if release_version.startswith("2"):
            query = """
                SELECT columnfamily_name AS table_name, column_name
                FROM system.schema_columns
                WHERE keyspace_name ='{}';
                """.format(
                self.configuration["keyspace"]
            )

        results, error = self.run_query(query, None)

        schema = {}
        for row in results["rows"]:
            table_name = row["table_name"]
            column_name = row["column_name"]
            if table_name not in schema:
                schema[table_name] = {"name": table_name, "columns": []}
            schema[table_name]["columns"].append(column_name)

        return list(schema.values())

    def run_query(self, query, user):
        connection = None
        cert_path = self._generate_cert_file()
        client_cert_path = self._generate_client_cert_file()
        client_key_path = self._generate_client_key_file()
        sslProtocol = self.configuration.get("sslProtocol", ssl.PROTOCOL_TLSv1_2)
        if self.configuration.get("username", "") and self.configuration.get("password", ""):
            auth_provider = PlainTextAuthProvider(
                username="{}".format(self.configuration.get("username", "")),
                password="{}".format(self.configuration.get("password", "")),
            )
            connection = Cluster(
                [self.configuration.get("host", "")],
                auth_provider=auth_provider,
                port=self.configuration.get("port", ""),
                protocol_version=self.configuration.get("protocol", 3),
                ssl_options=self._get_ssl_options(cert_path),
            )
        elif self.configuration.get("sslClientCertificateFile") and self.configuration.get("sslClientKeyFile"):
            sslProtocol = self.configuration.get("sslProtocol", ssl.PROTOCOL_TLSv1_2)
            cert_path = self._generate_cert_file()
            connection = Cluster(
                [self.configuration.get("host", "")],
                ssl_context=self._get_ssl_context(sslProtocol, client_cert_path, client_key_path, cert_path),
            )
        else:
            connection = Cluster(
                [self.configuration.get("host", "")],
                port=self.configuration.get("port", ""),
                protocol_version=self.configuration.get("protocol", 3),
                ssl_options=self._get_ssl_options(cert_path),
            )
        session = connection.connect()
        session.set_keyspace(self.configuration["keyspace"])
        session.default_timeout = self.configuration.get("timeout", 10)
        logger.debug("Cassandra running query: %s", query)
        result = session.execute(query)
        self._cleanup_cert_file(cert_path)

        column_names = result.column_names

        columns = self.fetch_columns([(c, "string") for c in column_names])

        rows = [dict(zip(column_names, row)) for row in result]

        data = {"columns": columns, "rows": rows}

        return data, None

    def _generate_cert_file(self):
        cert_encoded_bytes = self.configuration.get("sslCertificateFile", None)
        if cert_encoded_bytes:
            with NamedTemporaryFile(mode="w", delete=False) as cert_file:
                cert_bytes = b64decode(cert_encoded_bytes)
                cert_file.write(cert_bytes.decode("utf-8"))
            return cert_file.name
        return None

    def _generate_client_cert_file(self):
        cert_encoded_bytes = self.configuration.get("sslClientCertificateFile", None)
        if cert_encoded_bytes:
            with NamedTemporaryFile(mode="w", delete=False) as client_cert_file:
                cert_bytes = b64decode(cert_encoded_bytes)
                client_cert_file.write(cert_bytes.decode("utf-8"))
            return client_cert_file.name
        return None

    def _generate_client_key_file(self):
        cert_encoded_bytes = self.configuration.get("sslClientKeyFile", None)
        if cert_encoded_bytes:
            with NamedTemporaryFile(mode="w", delete=False) as client_key_file:
                cert_bytes = b64decode(cert_encoded_bytes)
                client_key_file.write(cert_bytes.decode("utf-8"))
            return client_key_file.name
        return None

    def _cleanup_cert_file(self, cert_path):
        if cert_path:
            os.remove(cert_path)

    def _get_ssl_options(self, cert_path):
        ssl_options = None
        if self.configuration.get("useSsl", False):
            ssl_options = generate_ssl_options_dict(protocol=self.configuration["sslProtocol"], cert_path=cert_path)
        return ssl_options

    def _get_ssl_context(self, sslProtocol, client_cert_path, client_key_path, cert_path):
        ssl_context = ssl.SSLContext(sslProtocol)
        ssl_context.load_cert_chain(client_cert_path, client_key_path)
        ssl_context.load_verify_locations(cert_path)
        ssl_context.verify_mode = ssl.CERT_REQUIRED
        return ssl_context


class ScyllaDB(Cassandra):
    @classmethod
    def type(cls):
        return "scylla"


register(Cassandra)
register(ScyllaDB)
