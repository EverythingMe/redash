from redash.handlers.query_results import error_messages, run_query
from redash.models import db
from tests import BaseTestCase


class TestRunQuery(BaseTestCase):
    def test_run_query_with_no_data_source(self):
        response, status = run_query(None, None, None, None, None)
        self.assertDictEqual(response, error_messages["no_data_source"][0])
        self.assertEqual(status, error_messages["no_data_source"][1])


class TestQueryResultsCacheHeaders(BaseTestCase):
    def test_uses_cache_headers_for_specific_result(self):
        query_result = self.factory.create_query_result()
        query = self.factory.create_query(latest_query_data=query_result)

        rv = self.make_request("get", "/api/queries/{}/results/{}.json".format(query.id, query_result.id))
        self.assertIn("Cache-Control", rv.headers)

    def test_doesnt_use_cache_headers_for_non_specific_result(self):
        query_result = self.factory.create_query_result()
        query = self.factory.create_query(latest_query_data=query_result)

        rv = self.make_request("get", "/api/queries/{}/results.json".format(query.id))
        self.assertNotIn("Cache-Control", rv.headers)

    def test_returns_404_if_no_cached_result_found(self):
        query = self.factory.create_query(latest_query_data=None)

        rv = self.make_request("get", "/api/queries/{}/results.json".format(query.id))
        self.assertEqual(404, rv.status_code)


class TestQueryResultsContentDispositionHeaders(BaseTestCase):
    def test_supports_unicode(self):
        query_result = self.factory.create_query_result()
        query = self.factory.create_query(name="עברית", latest_query_data=query_result)

        rv = self.make_request("get", "/api/queries/{}/results.json".format(query.id))
        # This is what gunicorn will do with it
        try:
            rv.headers["Content-Disposition"].encode("ascii")
        except Exception as e:
            self.fail(repr(e))


class TestQueryResultListAPI(BaseTestCase):
    def test_get_existing_result(self):
        query_result = self.factory.create_query_result()
        query = self.factory.create_query()

        rv = self.make_request(
            "post",
            "/api/query_results",
            data={
                "data_source_id": self.factory.data_source.id,
                "query": query.query_text,
            },
        )
        self.assertEqual(rv.status_code, 200)
        self.assertEqual(query_result.id, rv.json["query_result"]["id"])

    def test_execute_new_query(self):
        self.factory.create_query_result()
        query = self.factory.create_query()

        rv = self.make_request(
            "post",
            "/api/query_results",
            data={
                "data_source_id": self.factory.data_source.id,
                "query": query.query_text,
                "max_age": 0,
            },
        )

        self.assertEqual(rv.status_code, 200)
        self.assertNotIn("query_result", rv.json)
        self.assertIn("job", rv.json)

    def test_add_limit_change_query_sql(self):
        ds = self.factory.create_data_source(group=self.factory.org.default_group, type="pg")
        query = self.factory.create_query(query_text="SELECT 2", data_source=ds)
        self.factory.create_query_result(data_source=ds, query_hash=query.query_hash)

        rv = self.make_request(
            "post",
            "/api/query_results",
            data={"data_source_id": ds.id, "query": query.query_text, "apply_auto_limit": True},
        )

        self.assertEqual(rv.status_code, 200)
        self.assertNotIn("query_result", rv.json)
        self.assertIn("job", rv.json)

    def test_add_limit_no_change_for_nonsql(self):
        ds = self.factory.create_data_source(group=self.factory.org.default_group, type="prometheus")
        query = self.factory.create_query(query_text="SELECT 5", data_source=ds)
        query_result = self.factory.create_query_result(data_source=ds, query_hash=query.query_hash)

        rv = self.make_request(
            "post",
            "/api/query_results",
            data={"data_source_id": ds.id, "query": query.query_text, "apply_auto_limit": True},
        )

        self.assertEqual(rv.status_code, 200)
        self.assertEqual(query_result.id, rv.json["query_result"]["id"])

    def test_execute_query_without_access(self):
        group = self.factory.create_group()
        db.session.commit()
        user = self.factory.create_user(group_ids=[group.id])
        query = self.factory.create_query()

        rv = self.make_request(
            "post",
            "/api/query_results",
            data={
                "data_source_id": self.factory.data_source.id,
                "query": query.query_text,
                "max_age": 0,
            },
            user=user,
        )

        self.assertEqual(rv.status_code, 403)
        self.assertIn("job", rv.json)

    def test_execute_query_with_params(self):
        query = "SELECT {{param}}"

        rv = self.make_request(
            "post",
            "/api/query_results",
            data={
                "data_source_id": self.factory.data_source.id,
                "query": query,
                "max_age": 0,
            },
        )

        self.assertEqual(rv.status_code, 400)
        self.assertIn("job", rv.json)

        rv = self.make_request(
            "post",
            "/api/query_results",
            data={
                "data_source_id": self.factory.data_source.id,
                "query": query,
                "parameters": {"param": 1},
                "max_age": 0,
            },
        )

        self.assertEqual(rv.status_code, 200)
        self.assertIn("job", rv.json)

        rv = self.make_request(
            "post",
            "/api/query_results?p_param=1",
            data={
                "data_source_id": self.factory.data_source.id,
                "query": query,
                "max_age": 0,
            },
        )

        self.assertEqual(rv.status_code, 200)
        self.assertIn("job", rv.json)

    def test_execute_on_paused_data_source(self):
        self.factory.data_source.pause()

        rv = self.make_request(
            "post",
            "/api/query_results",
            data={
                "data_source_id": self.factory.data_source.id,
                "query": "SELECT 1",
                "max_age": 0,
            },
        )

        self.assertEqual(rv.status_code, 400)
        self.assertNotIn("query_result", rv.json)
        self.assertIn("job", rv.json)

    def test_execute_without_data_source(self):
        rv = self.make_request("post", "/api/query_results", data={"query": "SELECT 1", "max_age": 0})

        self.assertEqual(rv.status_code, 401)
        self.assertDictEqual(rv.json, error_messages["select_data_source"][0])


class TestQueryResultAPI(BaseTestCase):
    def test_has_no_access_to_data_source(self):
        ds = self.factory.create_data_source(group=self.factory.create_group())
        query_result = self.factory.create_query_result(data_source=ds)

        rv = self.make_request("get", "/api/query_results/{}".format(query_result.id))
        self.assertEqual(rv.status_code, 403)

    def test_has_view_only_access_to_data_source(self):
        ds = self.factory.create_data_source(group=self.factory.org.default_group, view_only=True)
        query_result = self.factory.create_query_result(data_source=ds)

        rv = self.make_request("get", "/api/query_results/{}".format(query_result.id))
        self.assertEqual(rv.status_code, 200)

    def test_has_full_access_to_data_source(self):
        ds = self.factory.create_data_source(group=self.factory.org.default_group, view_only=False)
        query_result = self.factory.create_query_result(data_source=ds)

        rv = self.make_request("get", "/api/query_results/{}".format(query_result.id))
        self.assertEqual(rv.status_code, 200)

    def test_execute_new_query(self):
        query = self.factory.create_query()

        rv = self.make_request("post", "/api/queries/{}/results".format(query.id), data={"parameters": {}})

        self.assertEqual(rv.status_code, 200)
        self.assertIn("job", rv.json)

    def test_execute_but_has_no_access_to_data_source(self):
        ds = self.factory.create_data_source(group=self.factory.create_group())
        query = self.factory.create_query(data_source=ds)

        rv = self.make_request("post", "/api/queries/{}/results".format(query.id))
        self.assertEqual(rv.status_code, 403)
        self.assertDictEqual(rv.json, error_messages["no_permission"][0])

    def test_execute_with_no_parameter_values(self):
        query = self.factory.create_query()

        rv = self.make_request("post", "/api/queries/{}/results".format(query.id))

        self.assertEqual(rv.status_code, 200)
        self.assertIn("job", rv.json)

    def test_prevents_execution_of_unsafe_queries_on_view_only_data_sources(self):
        ds = self.factory.create_data_source(group=self.factory.org.default_group, view_only=True)
        query = self.factory.create_query(data_source=ds, options={"parameters": [{"name": "foo", "type": "text"}]})

        rv = self.make_request("post", "/api/queries/{}/results".format(query.id), data={"parameters": {}})
        self.assertEqual(rv.status_code, 403)
        self.assertDictEqual(rv.json, error_messages["unsafe_on_view_only"][0])

    def test_allows_execution_of_safe_queries_on_view_only_data_sources(self):
        ds = self.factory.create_data_source(group=self.factory.org.default_group, view_only=True)
        query = self.factory.create_query(data_source=ds, options={"parameters": [{"name": "foo", "type": "number"}]})

        rv = self.make_request("post", "/api/queries/{}/results".format(query.id), data={"parameters": {}})
        self.assertEqual(rv.status_code, 200)

    def test_get_latest_query_result_with_apply_auto_limit(self):
        query = self.factory.create_query(
            options={"parameters": [{"name": "foo", "type": "number"}], "apply_auto_limit": True}
        )
        rv = self.make_request(
            "post",
            "/api/queries/{}/results".format(query.id),
            data={"parameters": {}, "apply_auto_limit": True},
        )

        self.assertEqual(rv.status_code, 200)
        self.assertIn("job", rv.json)

    def test_prevents_execution_of_unsafe_queries_using_api_key(self):
        ds = self.factory.create_data_source(group=self.factory.org.default_group, view_only=True)
        query = self.factory.create_query(data_source=ds, options={"parameters": [{"name": "foo", "type": "text"}]})

        data = {"parameters": {"foo": "bar"}}
        rv = self.make_request(
            "post",
            "/api/queries/{}/results?api_key={}".format(query.id, query.api_key),
            data=data,
        )
        self.assertEqual(rv.status_code, 403)
        self.assertDictEqual(rv.json, error_messages["unsafe_when_shared"][0])

    def test_access_with_query_api_key(self):
        ds = self.factory.create_data_source(group=self.factory.org.default_group, view_only=False)
        query = self.factory.create_query()
        query_result = self.factory.create_query_result(data_source=ds, query_text=query.query_text)

        rv = self.make_request(
            "get",
            "/api/queries/{}/results/{}.json?api_key={}".format(query.id, query_result.id, query.api_key),
            user=False,
        )
        self.assertEqual(rv.status_code, 200)

    def test_access_query_using_dashboard_api_key(self):
        query = self.factory.create_query()
        query_result = self.factory.create_query_result(query_text=query.query_text)

        dashboard1 = self.factory.create_dashboard()
        visualization1 = self.factory.create_visualization(query_rel=query)
        self.factory.create_widget(visualization=visualization1, dashboard=dashboard1)
        api_key = self.factory.create_api_key(object=dashboard1).api_key

        rv = self.make_request(
            "get",
            "/api/queries/{}/results/{}.json?api_key={}".format(query.id, query_result.id, api_key),
            user=False,
        )
        self.assertEqual(rv.status_code, 200)

    def test_reject_query_using_unrelated_dashboard_api_key(self):
        query = self.factory.create_query()
        query_result = self.factory.create_query_result(query_text=query.query_text)

        dashboard1 = self.factory.create_dashboard()
        api_key = self.factory.create_api_key(object=dashboard1).api_key

        rv = self.make_request(
            "get",
            "/api/queries/{}/results/{}.json?api_key={}".format(query.id, query_result.id, api_key),
            user=False,
        )
        self.assertEqual(rv.status_code, 403)

    def test_access_with_query_api_key_without_query_result_id(self):
        ds = self.factory.create_data_source(group=self.factory.org.default_group, view_only=False)
        query = self.factory.create_query()
        query_result = self.factory.create_query_result(
            data_source=ds, query_text=query.query_text, query_hash=query.query_hash
        )
        query.latest_query_data = query_result

        rv = self.make_request(
            "get",
            "/api/queries/{}/results.json?api_key={}".format(query.id, query.api_key),
            user=False,
        )
        self.assertEqual(rv.status_code, 200)

    def test_query_api_key_and_different_query_result(self):
        ds = self.factory.create_data_source(group=self.factory.org.default_group, view_only=False)
        query = self.factory.create_query(query_text="SELECT 8")
        query_result2 = self.factory.create_query_result(data_source=ds, query_hash="something-different")

        rv = self.make_request(
            "get",
            "/api/queries/{}/results/{}.json?api_key={}".format(query.id, query_result2.id, query.api_key),
            user=False,
        )
        self.assertEqual(rv.status_code, 404)

    def test_signed_in_user_and_different_query_result(self):
        ds2 = self.factory.create_data_source(group=self.factory.org.admin_group, view_only=False)
        query = self.factory.create_query(query_text="SELECT 8")
        query_result2 = self.factory.create_query_result(data_source=ds2, query_hash="something-different")

        rv = self.make_request("get", "/api/queries/{}/results/{}.json".format(query.id, query_result2.id))
        self.assertEqual(rv.status_code, 403)


class TestQueryResultDropdownResource(BaseTestCase):
    def test_checks_for_access_to_the_query(self):
        ds2 = self.factory.create_data_source(group=self.factory.org.admin_group, view_only=False)
        query = self.factory.create_query(data_source=ds2)

        rv = self.make_request("get", "/api/queries/{}/dropdown".format(query.id))

        self.assertEqual(rv.status_code, 403)


class TestQueryDropdownsResource(BaseTestCase):
    def test_prevents_access_if_unassociated_and_doesnt_have_access(self):
        query = self.factory.create_query()
        ds2 = self.factory.create_data_source(group=self.factory.org.admin_group, view_only=False)
        unrelated_dropdown_query = self.factory.create_query(data_source=ds2)

        # unrelated_dropdown_query has not been associated with query
        # user does not have direct access to unrelated_dropdown_query

        rv = self.make_request(
            "get",
            "/api/queries/{}/dropdowns/{}".format(query.id, unrelated_dropdown_query.id),
        )

        self.assertEqual(rv.status_code, 403)

    def test_allows_access_if_unassociated_but_user_has_access(self):
        query = self.factory.create_query()

        query_result = self.factory.create_query_result()
        data = {"rows": [], "columns": [{"name": "whatever"}]}
        query_result = self.factory.create_query_result(data=data)
        unrelated_dropdown_query = self.factory.create_query(latest_query_data=query_result)

        # unrelated_dropdown_query has not been associated with query
        # user has direct access to unrelated_dropdown_query

        rv = self.make_request(
            "get",
            "/api/queries/{}/dropdowns/{}".format(query.id, unrelated_dropdown_query.id),
        )

        self.assertEqual(rv.status_code, 200)

    def test_allows_access_if_associated_and_has_access_to_parent(self):
        query_result = self.factory.create_query_result()
        data = {"rows": [], "columns": [{"name": "whatever"}]}
        query_result = self.factory.create_query_result(data=data)
        dropdown_query = self.factory.create_query(latest_query_data=query_result)

        options = {"parameters": [{"name": "param", "type": "query", "queryId": dropdown_query.id}]}
        query = self.factory.create_query(options=options)

        # dropdown_query has been associated with query
        # user has access to query

        rv = self.make_request("get", "/api/queries/{}/dropdowns/{}".format(query.id, dropdown_query.id))

        self.assertEqual(rv.status_code, 200)

    def test_prevents_access_if_associated_and_doesnt_have_access_to_parent(self):
        ds2 = self.factory.create_data_source(group=self.factory.org.admin_group, view_only=False)
        dropdown_query = self.factory.create_query(data_source=ds2)
        options = {"parameters": [{"name": "param", "type": "query", "queryId": dropdown_query.id}]}
        query = self.factory.create_query(data_source=ds2, options=options)

        # dropdown_query has been associated with query
        # user doesnt have access to either query

        rv = self.make_request("get", "/api/queries/{}/dropdowns/{}".format(query.id, dropdown_query.id))

        self.assertEqual(rv.status_code, 403)


class TestQueryResultExcelResponse(BaseTestCase):
    def test_renders_excel_file(self):
        query = self.factory.create_query()
        query_result = self.factory.create_query_result()

        rv = self.make_request(
            "get",
            "/api/queries/{}/results/{}.xlsx".format(query.id, query_result.id),
            is_json=False,
        )
        self.assertEqual(rv.status_code, 200)

    def test_renders_excel_file_when_rows_have_missing_columns(self):
        query = self.factory.create_query()
        data = {
            "rows": [{"test": 1}, {"test": 2, "test2": 3}],
            "columns": [{"name": "test"}, {"name": "test2"}],
        }
        query_result = self.factory.create_query_result(data=data)

        rv = self.make_request(
            "get",
            "/api/queries/{}/results/{}.xlsx".format(query.id, query_result.id),
            is_json=False,
        )
        self.assertEqual(rv.status_code, 200)


class TestJobResource(BaseTestCase):
    def test_cancels_queued_queries(self):
        QUEUED = 1
        FAILED = 4

        query = self.factory.create_query()
        job_id = self.make_request(
            "post",
            f"/api/queries/{query.id}/results",
            data={"parameters": {}},
        ).json[
            "job"
        ]["id"]

        status = self.make_request("get", f"/api/jobs/{job_id}").json["job"]["status"]
        self.assertEqual(status, QUEUED)

        self.make_request("delete", f"/api/jobs/{job_id}")

        job = self.make_request("get", f"/api/jobs/{job_id}").json["job"]
        self.assertEqual(job["status"], FAILED)
        self.assertTrue("cancelled" in job["error"])
