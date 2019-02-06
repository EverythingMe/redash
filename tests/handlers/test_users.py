from redash import models
from tests import BaseTestCase
from mock import patch

class TestUserListResourcePost(BaseTestCase):
    def test_returns_403_for_non_admin(self):
        rv = self.make_request('post', "/api/users")
        self.assertEqual(rv.status_code, 403)

    def test_returns_400_when_missing_fields(self):
        admin = self.factory.create_admin()

        rv = self.make_request('post', "/api/users", user=admin)
        self.assertEqual(rv.status_code, 400)

        rv = self.make_request('post', '/api/users', data={'name': 'User'}, user=admin)
        self.assertEqual(rv.status_code, 400)

        rv = self.make_request('post', '/api/users', data={'name': 'User', 'email': 'bademailaddress'}, user=admin)
        self.assertEqual(rv.status_code, 400)

    def test_returns_400_when_using_temporary_email(self):
        admin = self.factory.create_admin()

        test_user = {'name': 'User', 'email': 'user@mailinator.com', 'password': 'test'}
        rv = self.make_request('post', '/api/users', data=test_user, user=admin)
        self.assertEqual(rv.status_code, 400)

        test_user['email'] = 'arik@qq.com'
        rv = self.make_request('post', '/api/users', data=test_user, user=admin)
        self.assertEqual(rv.status_code, 400)

    def test_creates_user(self):
        admin = self.factory.create_admin()

        test_user = {'name': 'User', 'email': 'user@example.com', 'password': 'test'}
        rv = self.make_request('post', '/api/users', data=test_user, user=admin)

        self.assertEqual(rv.status_code, 200)
        self.assertEqual(rv.json['name'], test_user['name'])
        self.assertEqual(rv.json['email'], test_user['email'])

    def test_creates_user_case_insensitive_email(self):
        admin = self.factory.create_admin()

        test_user = {'name': 'User', 'email': 'User@Example.com', 'password': 'test'}
        rv = self.make_request('post', '/api/users', data=test_user, user=admin)

        self.assertEqual(rv.status_code, 200)
        self.assertEqual(rv.json['name'], test_user['name'])
        self.assertEqual(rv.json['email'], 'user@example.com')

    def test_returns_400_when_email_taken(self):
        admin = self.factory.create_admin()

        test_user = {'name': 'User', 'email': admin.email, 'password': 'test'}
        rv = self.make_request('post', '/api/users', data=test_user, user=admin)

        self.assertEqual(rv.status_code, 400)

    def test_returns_400_when_email_taken_case_insensitive(self):
        admin = self.factory.create_admin()

        test_user1 = {'name': 'User', 'email': 'user@example.com', 'password': 'test'}
        rv = self.make_request('post', '/api/users', data=test_user1, user=admin)

        self.assertEqual(rv.status_code, 200)
        self.assertEqual(rv.json['email'], 'user@example.com')

        test_user2 = {'name': 'User', 'email': 'user@Example.com', 'password': 'test'}
        rv = self.make_request('post', '/api/users', data=test_user2, user=admin)

        self.assertEqual(rv.status_code, 400)


class TestUserListGet(BaseTestCase):
    def create_filters_fixtures(self):
        class PlainObject(object):
            pass

        result = PlainObject()
        now = models.db.func.now()

        result.enabled_active1 = self.factory.create_user(disabled_at=None, is_invitation_pending=None)
        result.enabled_active2 = self.factory.create_user(disabled_at=None, is_invitation_pending=False)
        result.enabled_pending = self.factory.create_user(disabled_at=None, is_invitation_pending=True)
        result.disabled_active1 = self.factory.create_user(disabled_at=now, is_invitation_pending=None)
        result.disabled_active2 = self.factory.create_user(disabled_at=now, is_invitation_pending=False)
        result.disabled_pending = self.factory.create_user(disabled_at=now, is_invitation_pending=True)

        return result

    def make_request_and_check_users(self, method, path, expected_users, unexpected_users, *args, **kwargs):
        rv = self.make_request(method, path, *args, **kwargs)
        user_ids = map(lambda u: u['id'], rv.json['results'])
        for user in expected_users:
            self.assertIn(user.id, user_ids)
        for user in unexpected_users:
            self.assertNotIn(user.id, user_ids)

    def test_returns_users_for_given_org_only(self):
        user1 = self.factory.user
        user2 = self.factory.create_user()
        org = self.factory.create_org()
        user3 = self.factory.create_user(org=org)

        self.make_request_and_check_users('get', '/api/users', [user1, user2], [user3])

    def test_gets_all_enabled(self):
        users = self.create_filters_fixtures()
        self.make_request_and_check_users(
            'get', '/api/users',
            [users.enabled_active1, users.enabled_active2, users.enabled_pending],
            [users.disabled_active1, users.disabled_active2, users.disabled_pending]
        )

    def test_gets_all_disabled(self):
        users = self.create_filters_fixtures()
        self.make_request_and_check_users(
            'get', '/api/users?disabled=true',
            [users.disabled_active1, users.disabled_active2, users.disabled_pending],
            [users.enabled_active1, users.enabled_active2, users.enabled_pending]
        )

    def test_gets_all_enabled_and_active(self):
        users = self.create_filters_fixtures()
        self.make_request_and_check_users(
            'get', '/api/users?pending=false',
            [users.enabled_active1, users.enabled_active2],
            [users.enabled_pending, users.disabled_active1, users.disabled_active2, users.disabled_pending]
        )

    def test_gets_all_enabled_and_pending(self):
        users = self.create_filters_fixtures()
        self.make_request_and_check_users(
            'get', '/api/users?pending=true',
            [users.enabled_pending],
            [users.enabled_active1, users.enabled_active2, users.disabled_active1, users.disabled_active2, users.disabled_pending]
        )

    def test_gets_all_disabled_and_active(self):
        users = self.create_filters_fixtures()
        self.make_request_and_check_users(
            'get', '/api/users?disabled=true&pending=false',
            [users.disabled_active1, users.disabled_active2],
            [users.disabled_pending, users.enabled_active1, users.enabled_active2, users.enabled_pending]
        )

    def test_gets_all_disabled_and_pending(self):
        users = self.create_filters_fixtures()
        self.make_request_and_check_users(
            'get', '/api/users?disabled=true&pending=true',
            [users.disabled_pending],
            [users.disabled_active1, users.disabled_active2, users.enabled_active1, users.enabled_active2, users.enabled_pending]
        )


class TestUserResourceGet(BaseTestCase):
    def test_returns_api_key_for_your_own_user(self):
        rv = self.make_request('get', "/api/users/{}".format(self.factory.user.id))
        self.assertIn('api_key', rv.json)

    def test_returns_api_key_for_other_user_when_admin(self):
        other_user = self.factory.user
        admin = self.factory.create_admin()

        rv = self.make_request('get', "/api/users/{}".format(other_user.id), user=admin)
        self.assertIn('api_key', rv.json)

    def test_doesnt_return_api_key_for_other_user(self):
        other_user = self.factory.create_user()

        rv = self.make_request('get', "/api/users/{}".format(other_user.id))
        self.assertNotIn('api_key', rv.json)

    def test_doesnt_return_user_from_different_org(self):
        org = self.factory.create_org()
        other_user = self.factory.create_user(org=org)

        rv = self.make_request('get', "/api/users/{}".format(other_user.id))
        self.assertEqual(rv.status_code, 404)


class TestUserResourcePost(BaseTestCase):
    def test_returns_403_for_non_admin_changing_not_his_own(self):
        other_user = self.factory.create_user()

        rv = self.make_request('post', "/api/users/{}".format(other_user.id), data={"name": "New Name"})
        self.assertEqual(rv.status_code, 403)

    def test_returns_200_for_non_admin_changing_his_own(self):
        rv = self.make_request('post', "/api/users/{}".format(self.factory.user.id), data={"name": "New Name"})
        self.assertEqual(rv.status_code, 200)

    def test_returns_200_for_admin_changing_other_user(self):
        admin = self.factory.create_admin()

        rv = self.make_request('post', "/api/users/{}".format(self.factory.user.id), data={"name": "New Name"}, user=admin)
        self.assertEqual(rv.status_code, 200)

    def test_fails_password_change_without_old_password(self):
        rv = self.make_request('post', "/api/users/{}".format(self.factory.user.id), data={"password": "new password"})
        self.assertEqual(rv.status_code, 403)

    def test_fails_password_change_with_incorrect_old_password(self):
        rv = self.make_request('post', "/api/users/{}".format(self.factory.user.id), data={"password": "new password", "old_password": "wrong"})
        self.assertEqual(rv.status_code, 403)

    def test_changes_password(self):
        new_password = "new password"
        old_password = "old password"

        self.factory.user.hash_password(old_password)
        models.db.session.add(self.factory.user)

        rv = self.make_request('post', "/api/users/{}".format(self.factory.user.id), data={"password": new_password, "old_password": old_password})
        self.assertEqual(rv.status_code, 200)

        user = models.User.query.get(self.factory.user.id)
        self.assertTrue(user.verify_password(new_password))

    def test_returns_400_when_using_temporary_email(self):
        admin = self.factory.create_admin()

        test_user = {'email': 'user@mailinator.com'}
        rv = self.make_request('post', '/api/users/{}'.format(self.factory.user.id), data=test_user, user=admin)
        self.assertEqual(rv.status_code, 400)

        test_user['email'] = 'arik@qq.com'
        rv = self.make_request('post', '/api/users', data=test_user, user=admin)
        self.assertEqual(rv.status_code, 400)

    def test_changing_email_ends_any_other_sessions_of_current_user(self):
        with self.client as c:
            # visit profile page
            self.make_request('get', "/api/users/{}".format(self.factory.user.id))
            with c.session_transaction() as sess:
                previous = sess['user_id']

            # change e-mail address - this will result in a new `user_id` value inside the session
            self.make_request('post', "/api/users/{}".format(self.factory.user.id), data={"email": "john@doe.com"})

            # force the old `user_id`, simulating that the user is logged in from another browser
            with c.session_transaction() as sess:
                sess['user_id'] = previous
            rv = self.get_request("/api/users/{}".format(self.factory.user.id))

            self.assertEqual(rv.status_code, 404)

    def test_changing_email_does_not_end_current_session(self):
        self.make_request('get', "/api/users/{}".format(self.factory.user.id))

        with self.client as c:
            with c.session_transaction() as sess:
                previous = sess['user_id']

        self.make_request('post', "/api/users/{}".format(self.factory.user.id), data={"email": "john@doe.com"})

        with self.client as c:
            with c.session_transaction() as sess:
                current = sess['user_id']

        # make sure the session's `user_id` has changed to reflect the new identity, thus not logging the user out
        self.assertNotEquals(previous, current)

    def test_admin_can_delete_user(self):
        admin_user = self.factory.create_admin()
        other_user = self.factory.create_user(is_invitation_pending=True)

        rv = self.make_request('delete', "/api/users/{}".format(other_user.id), user=admin_user)

        self.assertEqual(rv.status_code, 200)
        self.assertEqual(models.User.query.get(other_user.id), None)


class TestUserDisable(BaseTestCase):
    def test_non_admin_cannot_disable_user(self):
        other_user = self.factory.create_user()
        self.assertFalse(other_user.is_disabled)

        rv = self.make_request('post', "/api/users/{}/disable".format(other_user.id), user=other_user)
        self.assertEqual(rv.status_code, 403)

        # user should stay enabled
        other_user = models.User.query.get(other_user.id)
        self.assertFalse(other_user.is_disabled)

    def test_admin_can_disable_user(self):
        admin_user = self.factory.create_admin()
        other_user = self.factory.create_user()
        self.assertFalse(other_user.is_disabled)

        rv = self.make_request('post', "/api/users/{}/disable".format(other_user.id), user=admin_user)
        self.assertEqual(rv.status_code, 200)

        # user should become disabled
        other_user = models.User.query.get(other_user.id)
        self.assertTrue(other_user.is_disabled)

    def test_admin_can_disable_another_admin(self):
        admin_user1 = self.factory.create_admin()
        admin_user2 = self.factory.create_admin()
        self.assertFalse(admin_user2.is_disabled)

        rv = self.make_request('post', "/api/users/{}/disable".format(admin_user2.id), user=admin_user1)
        self.assertEqual(rv.status_code, 200)

        # user should become disabled
        admin_user2 = models.User.query.get(admin_user2.id)
        self.assertTrue(admin_user2.is_disabled)

    def test_admin_cannot_disable_self(self):
        admin_user = self.factory.create_admin()
        self.assertFalse(admin_user.is_disabled)

        rv = self.make_request('post', "/api/users/{}/disable".format(admin_user.id), user=admin_user)
        self.assertEqual(rv.status_code, 403)

        # user should stay enabled
        admin_user = models.User.query.get(admin_user.id)
        self.assertFalse(admin_user.is_disabled)

    def test_admin_can_enable_user(self):
        admin_user = self.factory.create_admin()
        other_user = self.factory.create_user(disabled_at='2018-03-08 00:00')
        self.assertTrue(other_user.is_disabled)

        rv = self.make_request('delete', "/api/users/{}/disable".format(other_user.id), user=admin_user)
        self.assertEqual(rv.status_code, 200)

        # user should become enabled
        other_user = models.User.query.get(other_user.id)
        self.assertFalse(other_user.is_disabled)

    def test_admin_can_enable_another_admin(self):
        admin_user1 = self.factory.create_admin()
        admin_user2 = self.factory.create_admin(disabled_at='2018-03-08 00:00')
        self.assertTrue(admin_user2.is_disabled)

        rv = self.make_request('delete', "/api/users/{}/disable".format(admin_user2.id), user=admin_user1)
        self.assertEqual(rv.status_code, 200)

        # user should become enabled
        admin_user2 = models.User.query.get(admin_user2.id)
        self.assertFalse(admin_user2.is_disabled)

    def test_disabled_user_cannot_login(self):
        user = self.factory.create_user(disabled_at='2018-03-08 00:00')
        user.hash_password('password')

        self.db.session.add(user)
        self.db.session.commit()

        with patch('redash.handlers.authentication.login_user') as login_user_mock:
            rv = self.post_request('/login', data={'email': user.email, 'password': 'password'}, org=self.factory.org)
            # login handler should not be called
            login_user_mock.assert_not_called()
            # check if error is raised
            self.assertEquals(rv.status_code, 200)
            self.assertIn('Wrong email or password', rv.data)

    def test_disabled_user_should_not_access_api(self):
        # Note: some API does not require user, so check the one which requires

        # 1. create user; the user should have access to API
        user = self.factory.create_user()
        rv = self.make_request('get', '/api/dashboards', user=user)
        self.assertEquals(rv.status_code, 200)

        # 2. disable user; now API access should be forbidden
        user.disable()
        self.db.session.add(user)
        self.db.session.commit()

        rv = self.make_request('get', '/api/dashboards', user=user)
        self.assertNotEquals(rv.status_code, 200)

    def test_disabled_user_should_not_receive_restore_password_email(self):
        admin_user = self.factory.create_admin()

        # user should receive email
        user = self.factory.create_user()
        with patch('redash.handlers.users.send_password_reset_email') as send_password_reset_email_mock:
            send_password_reset_email_mock.return_value = 'reset_token'
            rv = self.make_request('post', '/api/users/{}/reset_password'.format(user.id), user=admin_user)
            self.assertEqual(rv.status_code, 200)
            send_password_reset_email_mock.assert_called_with(user)

        # disable user; now should not receive email
        user.disable()
        self.db.session.add(user)
        self.db.session.commit()

        with patch('redash.handlers.users.send_password_reset_email') as send_password_reset_email_mock:
            send_password_reset_email_mock.return_value = 'reset_token'
            rv = self.make_request('post', '/api/users/{}/reset_password'.format(user.id), user=admin_user)
            self.assertEqual(rv.status_code, 404)
            send_password_reset_email_mock.assert_not_called()


class TestUserRegenerateApiKey(BaseTestCase):
    def test_non_admin_cannot_regenerate_other_user_api_key(self):
        admin_user = self.factory.create_admin()
        other_user = self.factory.create_user()
        orig_api_key = other_user.api_key

        rv = self.make_request('post', "/api/users/{}/regenerate_api_key".format(other_user.id), user=admin_user)
        self.assertEqual(rv.status_code, 200)

        other_user = models.User.query.get(other_user.id)
        self.assertNotEquals(orig_api_key, other_user.api_key)

    def test_admin_can_regenerate_other_user_api_key(self):
        user1 = self.factory.create_user()
        user2 = self.factory.create_user()
        orig_user2_api_key = user2.api_key

        rv = self.make_request('post', "/api/users/{}/regenerate_api_key".format(user2.id), user=user1)
        self.assertEqual(rv.status_code, 403)

        user = models.User.query.get(user2.id)
        self.assertEquals(orig_user2_api_key, user.api_key)

    def test_admin_can_regenerate_api_key_myself(self):
        admin_user = self.factory.create_admin()
        orig_api_key = admin_user.api_key

        rv = self.make_request('post', "/api/users/{}/regenerate_api_key".format(admin_user.id), user=admin_user)
        self.assertEqual(rv.status_code, 200)

        user = models.User.query.get(admin_user.id)
        self.assertNotEquals(orig_api_key, user.api_key)

    def test_user_can_regenerate_api_key_myself(self):
        user = self.factory.create_user()
        orig_api_key = user.api_key

        rv = self.make_request('post', "/api/users/{}/regenerate_api_key".format(user.id), user=user)
        self.assertEqual(rv.status_code, 200)

        user = models.User.query.get(user.id)
        self.assertNotEquals(orig_api_key, user.api_key)
