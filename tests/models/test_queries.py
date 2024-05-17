import datetime

import mock
import pytest

from redash.models import Event, Group, Query, QueryResult, db
from redash.utils import gen_query_hash, utcnow
from tests import BaseTestCase


class QueryTest(BaseTestCase):
    def test_changing_query_text_changes_hash(self):
        q = self.factory.create_query()
        old_hash = q.query_hash

        q.query_text = "SELECT 2;"
        db.session.flush()
        self.assertNotEqual(old_hash, q.query_hash)

    def create_tagged_query(self, tags):
        ds = self.factory.create_data_source(group=self.factory.default_group)
        query = self.factory.create_query(data_source=ds, tags=tags)
        return query

    def test_all_tags(self):
        self.create_tagged_query(tags=["tag1"])
        self.create_tagged_query(tags=["tag1", "tag2"])
        self.create_tagged_query(tags=["tag1", "tag2", "tag3"])

        self.assertEqual(
            list(Query.all_tags(self.factory.user)),
            [("tag1", 3), ("tag2", 2), ("tag3", 1)],
        )

    def test_search_finds_in_name(self):
        q1 = self.factory.create_query(name="Testing seåřċħ")
        q2 = self.factory.create_query(name="Testing seåřċħing")
        q3 = self.factory.create_query(name="Testing seå řċħ")
        queries = list(Query.search("seåřċħ", [self.factory.default_group.id]))

        self.assertIn(q1, queries)
        self.assertIn(q2, queries)
        self.assertNotIn(q3, queries)

    def test_search_finds_in_description(self):
        q1 = self.factory.create_query(description="Testing seåřċħ")
        q2 = self.factory.create_query(description="Testing seåřċħing")
        q3 = self.factory.create_query(description="Testing seå řċħ")

        queries = Query.search("seåřċħ", [self.factory.default_group.id])

        self.assertIn(q1, queries)
        self.assertIn(q2, queries)
        self.assertNotIn(q3, queries)

    def test_search_finds_in_multi_byte_name_and_description(self):
        q1 = self.factory.create_query(name="日本語の名前テスト")
        q2 = self.factory.create_query(description="日本語の説明文テスト")
        q3 = self.factory.create_query(description="Testing search")

        queries = Query.search("テスト", [self.factory.default_group.id], multi_byte_search=True)

        self.assertIn(q1, queries)
        self.assertIn(q2, queries)
        self.assertNotIn(q3, queries)

    def test_search_by_id_returns_query(self):
        q1 = self.factory.create_query(description="Testing search")
        q2 = self.factory.create_query(description="Testing searching")
        q3 = self.factory.create_query(description="Testing sea rch")
        db.session.flush()
        queries = Query.search(str(q3.id), [self.factory.default_group.id])

        self.assertIn(q3, queries)
        self.assertNotIn(q1, queries)
        self.assertNotIn(q2, queries)

    def test_search_by_number(self):
        q = self.factory.create_query(description="Testing search 12345")
        db.session.flush()
        queries = Query.search("12345", [self.factory.default_group.id])

        self.assertIn(q, queries)

    def test_search_respects_groups(self):
        other_group = Group(org=self.factory.org, name="Other Group")
        db.session.add(other_group)
        ds = self.factory.create_data_source(group=other_group)

        q1 = self.factory.create_query(description="Testing search", data_source=ds)
        q2 = self.factory.create_query(description="Testing searching")
        q3 = self.factory.create_query(description="Testing sea rch")

        queries = list(Query.search("Testing", [self.factory.default_group.id]))

        self.assertNotIn(q1, queries)
        self.assertIn(q2, queries)
        self.assertIn(q3, queries)

        queries = list(Query.search("Testing", [other_group.id, self.factory.default_group.id]))
        self.assertIn(q1, queries)
        self.assertIn(q2, queries)
        self.assertIn(q3, queries)

        queries = list(Query.search("Testing", [other_group.id]))
        self.assertIn(q1, queries)
        self.assertNotIn(q2, queries)
        self.assertNotIn(q3, queries)

    def test_returns_each_query_only_once(self):
        other_group = self.factory.create_group()
        second_group = self.factory.create_group()
        ds = self.factory.create_data_source(group=other_group)
        ds.add_group(second_group, False)

        self.factory.create_query(description="Testing search", data_source=ds)
        db.session.flush()
        queries = list(
            Query.search(
                "Testing",
                [self.factory.default_group.id, other_group.id, second_group.id],
            )
        )

        self.assertEqual(1, len(queries))

    def test_save_updates_updated_at_field(self):
        # This should be a test of ModelTimestampsMixin, but it's easier to test in context of existing model... :-\
        one_day_ago = utcnow().date() - datetime.timedelta(days=1)
        q = self.factory.create_query(created_at=one_day_ago, updated_at=one_day_ago)
        db.session.flush()
        q.name = "x"
        db.session.flush()
        self.assertNotEqual(q.updated_at, one_day_ago)

    def test_search_is_case_insensitive(self):
        q = self.factory.create_query(name="Testing search")

        self.assertIn(q, Query.search("testing", [self.factory.default_group.id]))

    def test_search_query_parser_or(self):
        q1 = self.factory.create_query(name="Testing")
        q2 = self.factory.create_query(name="search")

        queries = list(Query.search("testing or search", [self.factory.default_group.id]))
        self.assertIn(q1, queries)
        self.assertIn(q2, queries)

    def test_search_query_parser_negation(self):
        q1 = self.factory.create_query(name="Testing")
        q2 = self.factory.create_query(name="search")

        queries = list(Query.search("testing -search", [self.factory.default_group.id]))
        self.assertIn(q1, queries)
        self.assertNotIn(q2, queries)

    def test_search_query_parser_parenthesis(self):
        q1 = self.factory.create_query(name="Testing search")
        q2 = self.factory.create_query(name="Testing searching")
        q3 = self.factory.create_query(name="Testing finding")

        queries = list(Query.search("testing (search or finding)", [self.factory.default_group.id]))
        self.assertIn(q1, queries)
        self.assertIn(q2, queries)
        self.assertIn(q3, queries)

    def test_search_query_parser_hyphen(self):
        q1 = self.factory.create_query(name="Testing search")
        q2 = self.factory.create_query(name="Testing-search")

        queries = list(Query.search("testing search", [self.factory.default_group.id]))
        self.assertIn(q1, queries)
        self.assertIn(q2, queries)

    @pytest.mark.skip(reason="sqlalchemy-searchable > 1.0 doesn't support searching for emails")
    def test_search_query_parser_emails(self):
        q1 = self.factory.create_query(name="janedoe@example.com")
        q2 = self.factory.create_query(name="johndoe@example.com")

        queries = list(Query.search("example", [self.factory.default_group.id]))
        self.assertIn(q1, queries)
        self.assertIn(q2, queries)

        queries = list(Query.search("com", [self.factory.default_group.id]))
        self.assertIn(q1, queries)
        self.assertIn(q2, queries)

        queries = list(Query.search("johndoe", [self.factory.default_group.id]))
        self.assertNotIn(q1, queries)
        self.assertIn(q2, queries)

    def test_past_scheduled_queries(self):
        query = self.factory.create_query()
        one_day_ago = (utcnow() - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        one_day_later = (utcnow() + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        query1 = self.factory.create_query(schedule={"interval": "3600", "until": one_day_ago})
        query2 = self.factory.create_query(schedule={"interval": "3600", "until": one_day_later})
        oq = staticmethod(lambda: [query1, query2])
        with mock.patch.object(query.query.filter(), "order_by", oq):
            res = query.past_scheduled_queries()
            self.assertTrue(query1 in res)
            self.assertFalse(query2 in res)

    def test_search_by_user_finds_in_user(self):
        u1 = self.factory.create_user(name="John")
        u2 = self.factory.create_user(name="Jane")

        self.factory._user = u1
        q1 = self.factory.create_query(name="Testing search with John")
        q2 = self.factory.create_query(description="Description search")

        self.factory._user = u2
        q3 = self.factory.create_query(name="Testing search with Jane")

        queries = Query.search_by_user("search", u1, multi_byte_search=True)

        self.assertIn(q1, queries)
        self.assertIn(q2, queries)
        self.assertNotIn(q3, queries)

    def test_search_by_user_finds_in_multi_byte_user(self):
        u1 = self.factory.create_user(name="大谷")
        u2 = self.factory.create_user(name="翔平")

        self.factory._user = u1
        q1 = self.factory.create_query(name="日本語の名前テスト")
        q2 = self.factory.create_query(description="日本語の説明文テスト")

        self.factory._user = u2
        q3 = self.factory.create_query(name="日本語の名前テスト")

        queries = Query.search_by_user("名前", u1, multi_byte_search=True)

        self.assertIn(q1, queries)
        self.assertNotIn(q2, queries)
        self.assertNotIn(q3, queries)


class QueryRecentTest(BaseTestCase):
    def test_global_recent(self):
        q1 = self.factory.create_query()
        q2 = self.factory.create_query()
        db.session.flush()
        e = Event(
            org=self.factory.org,
            user=self.factory.user,
            action="edit",
            object_type="query",
            object_id=q1.id,
        )
        db.session.add(e)
        recent = Query.recent([self.factory.default_group.id])
        self.assertIn(q1, recent)
        self.assertNotIn(q2, recent)

    def test_recent_excludes_drafts(self):
        q1 = self.factory.create_query()
        q2 = self.factory.create_query(is_draft=True)

        db.session.add_all(
            [
                Event(
                    org=self.factory.org,
                    user=self.factory.user,
                    action="edit",
                    object_type="query",
                    object_id=q1.id,
                ),
                Event(
                    org=self.factory.org,
                    user=self.factory.user,
                    action="edit",
                    object_type="query",
                    object_id=q2.id,
                ),
            ]
        )
        recent = Query.recent([self.factory.default_group.id])

        self.assertIn(q1, recent)
        self.assertNotIn(q2, recent)

    def test_recent_for_user(self):
        q1 = self.factory.create_query()
        q2 = self.factory.create_query()
        db.session.flush()
        e = Event(
            org=self.factory.org,
            user=self.factory.user,
            action="edit",
            object_type="query",
            object_id=q1.id,
        )
        db.session.add(e)
        recent = Query.recent([self.factory.default_group.id], user_id=self.factory.user.id)

        self.assertIn(q1, recent)
        self.assertNotIn(q2, recent)

        recent = Query.recent([self.factory.default_group.id], user_id=self.factory.user.id + 1)
        self.assertNotIn(q1, recent)
        self.assertNotIn(q2, recent)

    def test_respects_groups(self):
        q1 = self.factory.create_query()
        ds = self.factory.create_data_source(group=self.factory.create_group())
        q2 = self.factory.create_query(data_source=ds)
        db.session.flush()
        Event(
            org=self.factory.org,
            user=self.factory.user,
            action="edit",
            object_type="query",
            object_id=q1.id,
        )
        Event(
            org=self.factory.org,
            user=self.factory.user,
            action="edit",
            object_type="query",
            object_id=q2.id,
        )

        recent = Query.recent([self.factory.default_group.id])

        self.assertIn(q1, recent)
        self.assertNotIn(q2, recent)


class TestQueryByUser(BaseTestCase):
    def test_returns_only_users_queries(self):
        q = self.factory.create_query(user=self.factory.user)
        q2 = self.factory.create_query(user=self.factory.create_user())

        queries = Query.by_user(self.factory.user)

        # not using self.assertIn/NotIn because otherwise this fails :O
        self.assertTrue(q in list(queries))
        self.assertFalse(q2 in list(queries))

    def test_returns_drafts_by_the_user(self):
        q = self.factory.create_query(is_draft=True)
        q2 = self.factory.create_query(is_draft=True, user=self.factory.create_user())

        queries = Query.by_user(self.factory.user)

        # not using self.assertIn/NotIn because otherwise this fails :O
        self.assertTrue(q in queries)
        self.assertFalse(q2 in queries)

    def test_returns_only_queries_from_groups_the_user_is_member_in(self):
        q = self.factory.create_query()
        q2 = self.factory.create_query(data_source=self.factory.create_data_source(group=self.factory.create_group()))

        queries = Query.by_user(self.factory.user)

        # not using self.assertIn/NotIn because otherwise this fails :O
        self.assertTrue(q in queries)
        self.assertFalse(q2 in queries)


class TestQueryFork(BaseTestCase):
    def assert_visualizations(self, origin_q, origin_v, forked_q, forked_v):
        self.assertEqual(origin_v.options, forked_v.options)
        self.assertEqual(origin_v.type, forked_v.type)
        self.assertNotEqual(origin_v.id, forked_v.id)
        self.assertNotEqual(origin_v.query_rel, forked_v.query_rel)
        self.assertEqual(forked_q.id, forked_v.query_rel.id)

    def test_fork_with_visualizations(self):
        # prepare original query and visualizations
        data_source = self.factory.create_data_source(group=self.factory.create_group())
        query = self.factory.create_query(data_source=data_source, description="this is description")

        # create default TABLE - query factory does not create it
        self.factory.create_visualization(query_rel=query, name="Table", description="", type="TABLE", options={})

        visualization_chart = self.factory.create_visualization(
            query_rel=query,
            description="chart vis",
            type="CHART",
            options={
                "yAxis": [{"type": "linear"}, {"type": "linear", "opposite": True}],
                "series": {"stacking": None},
                "globalSeriesType": "line",
                "sortX": True,
                "seriesOptions": {"count": {"zIndex": 0, "index": 0, "type": "line", "yAxis": 0}},
                "xAxis": {"labels": {"enabled": True}, "type": "datetime"},
                "columnMapping": {"count": "y", "created_at": "x"},
                "bottomMargin": 50,
                "legend": {"enabled": True},
            },
        )
        visualization_box = self.factory.create_visualization(
            query_rel=query, description="box vis", type="BOXPLOT", options={}
        )
        fork_user = self.factory.create_user()
        forked_query = query.fork(fork_user)
        db.session.flush()

        forked_visualization_chart = None
        forked_visualization_box = None
        forked_table = None
        count_table = 0
        for v in forked_query.visualizations:
            if v.description == "chart vis":
                forked_visualization_chart = v
            if v.description == "box vis":
                forked_visualization_box = v
            if v.type == "TABLE":
                count_table += 1
                forked_table = v

        self.assert_visualizations(query, visualization_chart, forked_query, forked_visualization_chart)
        self.assert_visualizations(query, visualization_box, forked_query, forked_visualization_box)

        self.assertEqual(forked_query.org, query.org)
        self.assertEqual(forked_query.data_source, query.data_source)
        self.assertEqual(forked_query.latest_query_data, query.latest_query_data)
        self.assertEqual(forked_query.description, query.description)
        self.assertEqual(forked_query.query_text, query.query_text)
        self.assertEqual(forked_query.query_hash, query.query_hash)
        self.assertEqual(forked_query.user, fork_user)
        self.assertEqual(forked_query.description, query.description)
        self.assertTrue(forked_query.name.startswith("Copy"))
        # num of TABLE must be 1. default table only
        self.assertEqual(count_table, 1)
        self.assertEqual(forked_table.name, "Table")
        self.assertEqual(forked_table.description, "")
        self.assertEqual(forked_table.options, {})

    def test_fork_from_query_that_has_no_visualization(self):
        # prepare original query and visualizations
        data_source = self.factory.create_data_source(group=self.factory.create_group())
        query = self.factory.create_query(data_source=data_source, description="this is description")

        # create default TABLE - query factory does not create it
        self.factory.create_visualization(query_rel=query, name="Table", description="", type="TABLE", options={})

        fork_user = self.factory.create_user()

        forked_query = query.fork(fork_user)

        count_table = 0
        count_vis = 0
        for v in forked_query.visualizations:
            count_vis += 1
            if v.type == "TABLE":
                count_table += 1

        self.assertEqual(count_table, 1)
        self.assertEqual(count_vis, 1)

    def test_fork_keeps_query_tags(self):
        query = self.factory.create_query(tags=["test", "query"])

        forked_query = query.fork(self.factory.user)

        self.assertEqual(query.tags, forked_query.tags)


class TestQueryUpdateLatestResult(BaseTestCase):
    def setUp(self):
        super(TestQueryUpdateLatestResult, self).setUp()
        self.data_source = self.factory.data_source
        self.query = "SELECT 1"
        self.query_hash = gen_query_hash(self.query, {}, False)
        self.runtime = 123
        self.utcnow = utcnow()
        self.data = {"columns": {}, "rows": []}

    def test_updates_existing_queries(self):
        query1 = self.factory.create_query(query_text=self.query)
        query2 = self.factory.create_query(query_text=self.query)
        query3 = self.factory.create_query(query_text=self.query, is_archived=True)

        query_result = QueryResult.store_result(
            self.data_source.org_id,
            self.data_source,
            self.query_hash,
            self.query,
            self.data,
            self.runtime,
            self.utcnow,
        )

        Query.update_latest_result(query_result)

        self.assertEqual(query1.latest_query_data, query_result)
        self.assertEqual(query2.latest_query_data, query_result)
        self.assertEqual(query3.latest_query_data, None)

    def test_doesnt_update_queries_with_different_hash(self):
        query1 = self.factory.create_query(query_text=self.query)
        query2 = self.factory.create_query(query_text=self.query)
        query3 = self.factory.create_query(query_text=self.query + "123")

        query_result = QueryResult.store_result(
            self.data_source.org_id,
            self.data_source,
            self.query_hash,
            self.query,
            self.data,
            self.runtime,
            self.utcnow,
        )

        Query.update_latest_result(query_result)

        self.assertEqual(query1.latest_query_data, query_result)
        self.assertEqual(query2.latest_query_data, query_result)
        self.assertNotEqual(query3.latest_query_data, query_result)

    def test_doesnt_update_queries_with_different_data_source(self):
        query1 = self.factory.create_query(query_text=self.query)
        query2 = self.factory.create_query(query_text=self.query)
        query3 = self.factory.create_query(query_text=self.query, data_source=self.factory.create_data_source())

        query_result = QueryResult.store_result(
            self.data_source.org_id,
            self.data_source,
            self.query_hash,
            self.query,
            self.data,
            self.runtime,
            self.utcnow,
        )

        Query.update_latest_result(query_result)

        self.assertEqual(query1.latest_query_data, query_result)
        self.assertEqual(query2.latest_query_data, query_result)
        self.assertNotEqual(query3.latest_query_data, query_result)
