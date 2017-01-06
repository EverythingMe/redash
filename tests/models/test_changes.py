from tests import BaseTestCase

from redash.models import db, Query, ChangeTrackingMixin


def create_query(factory):
    obj = Query(name='Query',
                description='',
                query_text='SELECT 1',
                user=factory.user,
                data_source=factory.data_source,
                org=factory.org)
    db.session.add(obj)
    return obj


class TestChangesProperty(BaseTestCase):
    def test_returns_initial_state(self):
        obj = create_query(self.factory)
        db.session.commit()
        for change in Query.Change.query.filter(Query.Change.object == obj):
            for k in change.change:
                self.assertIsNone(change.change[k]['previous'])


class TestLogChange(BaseTestCase):
    def obj(self):
        obj = Query(name='Query',
                    description='',
                    query_text='SELECT 1',
                    user=self.factory.user,
                    data_source=self.factory.data_source,
                    org=self.factory.org)

        return obj

    def test_properly_logs_first_creation(self):
        obj = create_query(self.factory)
        obj.record_changes(changed_by=self.factory.user)
        change = Query.Change.last_change(obj)

        self.assertIsNotNone(change)
        self.assertEqual(change.object_version, 1)

    def test_skips_unnecessary_fields(self):
        obj = create_query(self.factory)
        obj.record_changes(changed_by=self.factory.user)
        change = Query.Change.last_change(obj)

        self.assertIsNotNone(change)
        self.assertEqual(change.object_version, 1)
        for field in ChangeTrackingMixin.skipped_fields:
            self.assertNotIn(field, change.change)

    def test_properly_log_modification(self):
        obj = create_query(self.factory)
        obj.record_changes(changed_by=self.factory.user)
        obj.name = 'Query 2'
        obj.description = 'description'
        db.session.flush()
        obj.record_changes(changed_by=self.factory.user)

        change = Query.Change.last_change(obj)

        self.assertIsNotNone(change)
        self.assertEqual(change.object_version, 2)
        self.assertEqual(change.object_version, obj.version)
        self.assertIn('name', change.change)
        self.assertIn('description', change.change)

    def test_logs_create_method(self):
        q = Query(name='Query', description='', query_text='',
                  user=self.factory.user, data_source=self.factory.data_source,
                  org=self.factory.org)
        change = Query.Change.last_change(q)

        self.assertIsNotNone(change)
        self.assertEqual(q.user, change.user)
