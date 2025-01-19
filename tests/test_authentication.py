import importlib
import json
import os
import subprocess
import time

import jwcrypto.jwk
import jwt
import requests
from flask import request
from mock import Mock, patch
from sqlalchemy.orm.exc import NoResultFound

from redash import models, settings
from redash.authentication import (
    api_key_load_user_from_request,
    get_login_url,
    hmac_load_user_from_request,
    jwt_auth,
    oidc,
    org_settings,
    sign,
)
from redash.authentication.google_oauth import (
    create_and_login_user,
    verify_profile,
)
from tests import BaseTestCase


class TestApiKeyAuthentication(BaseTestCase):
    #
    # This is a bad way to write these tests, but the way Flask works doesn't make it easy to write them properly...
    #
    def setUp(self):
        super(TestApiKeyAuthentication, self).setUp()
        self.api_key = "10"
        self.query = self.factory.create_query(api_key=self.api_key)
        models.db.session.flush()
        self.query_url = "/{}/api/queries/{}".format(self.factory.org.slug, self.query.id)
        self.queries_url = "/{}/api/queries".format(self.factory.org.slug)

    def test_no_api_key(self):
        with self.app.test_client() as c:
            c.get(self.query_url)
            self.assertIsNone(api_key_load_user_from_request(request))

    def test_wrong_api_key(self):
        with self.app.test_client() as c:
            c.get(self.query_url, query_string={"api_key": "whatever"})
            self.assertIsNone(api_key_load_user_from_request(request))

    def test_correct_api_key(self):
        with self.app.test_client() as c:
            c.get(self.query_url, query_string={"api_key": self.api_key})
            self.assertIsNotNone(api_key_load_user_from_request(request))

    def test_no_query_id(self):
        with self.app.test_client() as c:
            c.get(self.queries_url, query_string={"api_key": self.api_key})
            self.assertIsNone(api_key_load_user_from_request(request))

    def test_user_api_key(self):
        user = self.factory.create_user(api_key="user_key")
        models.db.session.flush()
        with self.app.test_client() as c:
            c.get(self.queries_url, query_string={"api_key": user.api_key})
            self.assertEqual(user.id, api_key_load_user_from_request(request).id)

    def test_disabled_user_api_key(self):
        user = self.factory.create_user(api_key="user_key")
        user.disable()
        models.db.session.flush()
        with self.app.test_client() as c:
            c.get(self.queries_url, query_string={"api_key": user.api_key})
            self.assertEqual(None, api_key_load_user_from_request(request))

    def test_api_key_header(self):
        with self.app.test_client() as c:
            c.get(self.query_url, headers={"Authorization": "Key {}".format(self.api_key)})
            self.assertIsNotNone(api_key_load_user_from_request(request))

    def test_api_key_header_with_wrong_key(self):
        with self.app.test_client() as c:
            c.get(self.query_url, headers={"Authorization": "Key oops"})
            self.assertIsNone(api_key_load_user_from_request(request))

    def test_api_key_for_wrong_org(self):
        other_user = self.factory.create_admin(org=self.factory.create_org())

        with self.app.test_client() as c:
            rv = c.get(
                self.query_url,
                headers={"Authorization": "Key {}".format(other_user.api_key)},
            )
            self.assertEqual(404, rv.status_code)


class TestHMACAuthentication(BaseTestCase):
    #
    # This is a bad way to write these tests, but the way Flask works doesn't make it easy to write them properly...
    #
    def setUp(self):
        super(TestHMACAuthentication, self).setUp()
        self.api_key = "10"
        self.query = self.factory.create_query(api_key=self.api_key)
        models.db.session.flush()
        self.path = "/{}/api/queries/{}".format(self.query.org.slug, self.query.id)
        self.expires = time.time() + 1800

    def signature(self, expires):
        return sign(self.query.api_key, self.path, expires)

    def test_no_signature(self):
        with self.app.test_client() as c:
            c.get(self.path)
            self.assertIsNone(hmac_load_user_from_request(request))

    def test_wrong_signature(self):
        with self.app.test_client() as c:
            c.get(
                self.path,
                query_string={"signature": "whatever", "expires": self.expires},
            )
            self.assertIsNone(hmac_load_user_from_request(request))

    def test_correct_signature(self):
        with self.app.test_client() as c:
            c.get(
                self.path,
                query_string={
                    "signature": self.signature(self.expires),
                    "expires": self.expires,
                },
            )
            self.assertIsNotNone(hmac_load_user_from_request(request))

    def test_no_query_id(self):
        with self.app.test_client() as c:
            c.get(
                "/{}/api/queries".format(self.query.org.slug),
                query_string={"api_key": self.api_key},
            )
            self.assertIsNone(hmac_load_user_from_request(request))

    def test_user_api_key(self):
        user = self.factory.create_user(api_key="user_key")
        path = "/api/queries/"
        models.db.session.flush()

        signature = sign(user.api_key, path, self.expires)
        with self.app.test_client() as c:
            c.get(
                path,
                query_string={
                    "signature": signature,
                    "expires": self.expires,
                    "user_id": user.id,
                },
            )
            self.assertEqual(user.id, hmac_load_user_from_request(request).id)


class TestSessionAuthentication(BaseTestCase):
    def test_prefers_api_key_over_session_user_id(self):
        user = self.factory.create_user()
        query = self.factory.create_query(user=user)

        other_org = self.factory.create_org()
        other_user = self.factory.create_user(org=other_org)
        models.db.session.flush()

        rv = self.make_request(
            "get",
            "/api/queries/{}?api_key={}".format(query.id, query.api_key),
            user=other_user,
        )
        self.assertEqual(rv.status_code, 200)


class TestCreateAndLoginUserOIDC(BaseTestCase):
    def test_logins_valid_user(self):
        user = self.factory.create_user(email="test@example.com")
        with patch("redash.authentication.login_user") as login_user_mock:
            oidc.create_and_login_user(self.factory.org, user.name, user.email)
            login_user_mock.assert_called_once_with(user, remember=True)

    def test_creates_vaild_new_user(self):
        email = "test@example.com"
        name = "Test User"

        with patch("redash.authentication.login_user") as login_user_mock:
            oidc.create_and_login_user(self.factory.org, name, email)

            self.assertTrue(login_user_mock.called)
            user = models.User.query.filter(models.User.email == email).one()
            self.assertEqual(user.email, email)

    def test_updates_user_name(self):
        user = self.factory.create_user(email="test@example.com")

        with patch("redash.authentication.login_user") as login_user_mock:
            create_and_login_user(self.factory.org, "New Name", user.email)
            login_user_mock.assert_called_once_with(user, remember=True)


class TestCreateAndLoginUser(BaseTestCase):
    def test_logins_valid_user(self):
        user = self.factory.create_user(email="test@example.com")

        with patch("redash.authentication.login_user") as login_user_mock:
            create_and_login_user(self.factory.org, user.name, user.email)
            login_user_mock.assert_called_once_with(user, remember=True)

    def test_creates_vaild_new_user(self):
        email = "test@example.com"
        name = "Test User"

        with patch("redash.authentication.login_user") as login_user_mock:
            create_and_login_user(self.factory.org, name, email)

            self.assertTrue(login_user_mock.called)
            user = models.User.query.filter(models.User.email == email).one()
            self.assertEqual(user.email, email)

    def test_updates_user_name(self):
        user = self.factory.create_user(email="test@example.com")

        with patch("redash.authentication.login_user") as login_user_mock:
            create_and_login_user(self.factory.org, "New Name", user.email)
            login_user_mock.assert_called_once_with(user, remember=True)


class TestVerifyProfile(BaseTestCase):
    def test_no_domain_allowed_for_org(self):
        profile = dict(email="arik@example.com")
        self.assertFalse(verify_profile(self.factory.org, profile))

    def test_domain_not_in_org_domains_list(self):
        profile = dict(email="arik@example.com")
        self.factory.org.settings[models.Organization.SETTING_GOOGLE_APPS_DOMAINS] = ["example.org"]
        self.assertFalse(verify_profile(self.factory.org, profile))

    def test_domain_in_org_domains_list(self):
        profile = dict(email="arik@example.com")
        self.factory.org.settings[models.Organization.SETTING_GOOGLE_APPS_DOMAINS] = ["example.com"]
        self.assertTrue(verify_profile(self.factory.org, profile))

        self.factory.org.settings[models.Organization.SETTING_GOOGLE_APPS_DOMAINS] = [
            "example.org",
            "example.com",
        ]
        self.assertTrue(verify_profile(self.factory.org, profile))

    def test_org_in_public_mode_accepts_any_domain(self):
        profile = dict(email="arik@example.com")
        self.factory.org.settings[models.Organization.SETTING_IS_PUBLIC] = True
        self.factory.org.settings[models.Organization.SETTING_GOOGLE_APPS_DOMAINS] = []
        self.assertTrue(verify_profile(self.factory.org, profile))

    def test_user_not_in_domain_but_account_exists(self):
        profile = dict(email="arik@example.com")
        self.factory.create_user(email="arik@example.com")
        self.factory.org.settings[models.Organization.SETTING_GOOGLE_APPS_DOMAINS] = ["example.org"]
        self.assertTrue(verify_profile(self.factory.org, profile))


class TestGetLoginUrl(BaseTestCase):
    def test_when_multi_org_enabled_and_org_exists(self):
        with self.app.test_request_context("/{}/".format(self.factory.org.slug)):
            self.assertEqual(get_login_url(next=None), "/{}/login".format(self.factory.org.slug))

    def test_when_multi_org_enabled_and_org_doesnt_exist(self):
        with self.app.test_request_context("/{}_notexists/".format(self.factory.org.slug)):
            self.assertEqual(get_login_url(next=None), "/")


class TestRedirectToUrlAfterLoggingIn(BaseTestCase):
    def setUp(self):
        super(TestRedirectToUrlAfterLoggingIn, self).setUp()
        self.user = self.factory.user
        self.password = "test1234"

    def test_no_next_param(self):
        response = self.post_request(
            "/login",
            data={"email": self.user.email, "password": self.password},
            org=self.factory.org,
        )
        self.assertEqual(response.location, "/{}/".format(self.user.org.slug))

    def test_simple_path_in_next_param(self):
        response = self.post_request(
            "/login?next=queries",
            data={"email": self.user.email, "password": self.password},
            org=self.factory.org,
        )
        self.assertEqual(response.location, "queries")

    def test_starts_scheme_url_in_next_param(self):
        response = self.post_request(
            "/login?next=https://redash.io",
            data={"email": self.user.email, "password": self.password},
            org=self.factory.org,
        )
        self.assertEqual(response.location, "./")

    def test_without_scheme_url_in_next_param(self):
        response = self.post_request(
            "/login?next=//redash.io",
            data={"email": self.user.email, "password": self.password},
            org=self.factory.org,
        )
        self.assertEqual(response.location, "./")

    def test_without_scheme_with_path_url_in_next_param(self):
        response = self.post_request(
            "/login?next=//localhost/queries",
            data={"email": self.user.email, "password": self.password},
            org=self.factory.org,
        )
        self.assertEqual(response.location, "/queries")


class TestRemoteUserAuth(BaseTestCase):
    DEFAULT_SETTING_OVERRIDES = {"REDASH_REMOTE_USER_LOGIN_ENABLED": "true"}

    def setUp(self):
        # Apply default setting overrides to every test
        self.override_settings(None)

        super(TestRemoteUserAuth, self).setUp()

    def override_settings(self, overrides):
        """Override settings for testing purposes.

        This helper method can be used to override specific environmental
        variables to enable / disable Re:Dash features for the duration
        of the test.

        Note that these overrides only affect code that checks the value of
        the setting at runtime. It doesn't affect code that only checks the
        value during program initialization.

        :param dict overrides: a dict of environmental variables to override
            when the settings are reloaded
        """
        variables = self.DEFAULT_SETTING_OVERRIDES.copy()
        variables.update(overrides or {})
        with patch.dict(os.environ, variables):
            importlib.reload(settings)

        # Queue a cleanup routine that reloads the settings without overrides
        # once the test ends
        self.addCleanup(lambda: importlib.reload(settings))

    def assert_correct_user_attributes(
        self,
        user,
        email="test@example.com",
        name="test@example.com",
        groups=None,
        org=None,
    ):
        """Helper to assert that the user attributes are correct."""
        groups = groups or []
        if self.factory.org.default_group.id not in groups:
            groups.append(self.factory.org.default_group.id)

        self.assertIsNotNone(user)
        self.assertEqual(user.email, email)
        self.assertEqual(user.name, name)
        self.assertEqual(user.org, org or self.factory.org)
        self.assertCountEqual(user.group_ids, groups)

    def get_test_user(self, email="test@example.com", org=None):
        """Helper to fetch an user from the database."""

        # Expire all cached objects to ensure these values are read directly
        # from the database.
        models.db.session.expire_all()

        return models.User.get_by_email_and_org(email, org or self.factory.org)

    def test_remote_login_disabled(self):
        self.override_settings({"REDASH_REMOTE_USER_LOGIN_ENABLED": "false"})

        self.get_request(
            "/remote_user/login",
            org=self.factory.org,
            headers={"X-Forwarded-Remote-User": "test@example.com"},
        )

        with self.assertRaises(NoResultFound):
            self.get_test_user()

    def test_remote_login_default_header(self):
        self.get_request(
            "/remote_user/login",
            org=self.factory.org,
            headers={"X-Forwarded-Remote-User": "test@example.com"},
        )

        self.assert_correct_user_attributes(self.get_test_user())

    def test_remote_login_custom_header(self):
        self.override_settings({"REDASH_REMOTE_USER_HEADER": "X-Custom-User"})

        self.get_request(
            "/remote_user/login",
            org=self.factory.org,
            headers={"X-Custom-User": "test@example.com"},
        )

        self.assert_correct_user_attributes(self.get_test_user())


class TestUserForgotPassword(BaseTestCase):
    def test_user_should_receive_password_reset_link(self):
        user = self.factory.create_user()

        with patch("redash.handlers.authentication.send_password_reset_email") as send_password_reset_email_mock:
            response = self.post_request("/forgot", org=user.org, data={"email": user.email})
            self.assertEqual(response.status_code, 200)
            send_password_reset_email_mock.assert_called_with(user)

    def test_disabled_user_should_not_receive_password_reset_link(self):
        user = self.factory.create_user()
        user.disable()
        self.db.session.add(user)
        self.db.session.commit()

        with patch(
            "redash.handlers.authentication.send_password_reset_email"
        ) as send_password_reset_email_mock, patch(
            "redash.handlers.authentication.send_user_disabled_email"
        ) as send_user_disabled_email_mock:
            response = self.post_request("/forgot", org=user.org, data={"email": user.email})
            self.assertEqual(response.status_code, 200)
            send_password_reset_email_mock.assert_not_called()
            send_user_disabled_email_mock.assert_called_with(user)


class TestJWTAuthentication(BaseTestCase):
    def setUp(self):
        super(TestJWTAuthentication, self).setUp()
        self.auth_audience = "My Org"
        self.auth_issuer = "Admin"
        self.token_name = "jwt-token"
        self.rsa_private_key = "/tmp/jwtRS256.key"
        self.rsa_public_key = "/tmp/jwtRS256.pem"

        if not os.path.exists(self.rsa_public_key):
            subprocess.check_output(["openssl", "genrsa", "-out", self.rsa_private_key, "4096"])
            subprocess.check_output(
                ["openssl", "rsa", "-pubout", "-in", self.rsa_private_key, "-out", self.rsa_public_key]
            )

        org_settings["auth_jwt_login_enabled"] = True
        org_settings["auth_jwt_auth_public_certs_url"] = "file://{}".format(self.rsa_public_key)
        org_settings["auth_jwt_auth_issuer"] = self.auth_issuer
        org_settings["auth_jwt_auth_audience"] = self.auth_audience
        org_settings["auth_jwt_auth_header_name"] = self.token_name

    def tearDown(self):
        org_settings["auth_jwt_login_enabled"] = False
        org_settings["auth_jwt_auth_public_certs_url"] = ""
        org_settings["auth_jwt_auth_issuer"] = ""
        org_settings["auth_jwt_auth_audience"] = ""
        org_settings["auth_jwt_auth_header_name"] = ""

    def test_jwt_no_token(self):
        response = self.get_request("/data_sources", org=self.factory.org)
        self.assertEqual(response.status_code, 302)

    def test_jwt_from_pem_file(self):
        user = self.factory.create_user()

        issued_at_timestamp = time.time()
        expiration_timestamp = issued_at_timestamp + 60

        data = {
            "aud": self.auth_audience,
            "email": user.email,
            "exp": expiration_timestamp,
            "iat": issued_at_timestamp,
            "iss": self.auth_issuer,
        }
        with open(self.rsa_private_key) as keyfile:
            sign_key = keyfile.read().strip()
        token_data = jwt.encode(data, sign_key, algorithm="RS256")

        response = self.get_request("/data_sources", org=self.factory.org, headers={self.token_name: token_data})
        self.assertEqual(response.status_code, 200)

    @patch.object(requests, "get")
    def test_jwk_decode(self, mock_get):
        with open(self.rsa_public_key, "rb") as keyfile:
            public_key = jwcrypto.jwk.JWK.from_pem(keyfile.read())
            jwk_keys = {"keys": [json.loads(public_key.export())]}

        mockresponse = Mock()
        mockresponse.json = lambda: jwk_keys
        mock_get.return_value = mockresponse

        keys = jwt_auth.get_public_keys("http://localhost/key.jwt")
        self.assertEqual(keys[0].key_size, 4096)
