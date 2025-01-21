import textwrap
from unittest import TestCase

from redash import settings
from redash.models import OPERATORS, Alert, db, next_state
from tests import BaseTestCase


class TestAlertAll(BaseTestCase):
    def test_returns_all_alerts_for_given_groups(self):
        ds1 = self.factory.data_source
        group = self.factory.create_group()
        ds2 = self.factory.create_data_source(group=group)

        query1 = self.factory.create_query(data_source=ds1)
        query2 = self.factory.create_query(data_source=ds2)

        alert1 = self.factory.create_alert(query_rel=query1)
        alert2 = self.factory.create_alert(query_rel=query2)
        db.session.flush()

        alerts = Alert.all(group_ids=[group.id, self.factory.default_group.id])
        self.assertIn(alert1, alerts)
        self.assertIn(alert2, alerts)

        alerts = Alert.all(group_ids=[self.factory.default_group.id])
        self.assertIn(alert1, alerts)
        self.assertNotIn(alert2, alerts)

        alerts = Alert.all(group_ids=[group.id])
        self.assertNotIn(alert1, alerts)
        self.assertIn(alert2, alerts)

    def test_return_each_alert_only_once(self):
        group = self.factory.create_group()
        self.factory.data_source.add_group(group)

        alert = self.factory.create_alert()

        alerts = Alert.all(group_ids=[self.factory.default_group.id, group.id])
        self.assertEqual(1, len(list(alerts)))
        self.assertIn(alert, alerts)


def get_results(value):
    return {"rows": [{"foo": value}], "columns": [{"name": "foo", "type": "STRING"}]}


class TestAlertEvaluate(BaseTestCase):
    def create_alert(self, results, column="foo", value="1"):
        result = self.factory.create_query_result(data=results)
        query = self.factory.create_query(latest_query_data_id=result.id)
        alert = self.factory.create_alert(
            query_rel=query, options={"selector": "first", "op": "equals", "column": column, "value": value}
        )
        return alert

    def test_evaluate_triggers_alert_when_equal(self):
        alert = self.create_alert(get_results(1))
        self.assertEqual(alert.evaluate(), Alert.TRIGGERED_STATE)

    def test_evaluate_number_value_and_string_threshold(self):
        alert = self.create_alert(get_results(1), value="string")
        self.assertEqual(alert.evaluate(), Alert.UNKNOWN_STATE)

    def test_evaluate_return_unknown_when_missing_column(self):
        alert = self.create_alert(get_results(1), column="bar")
        self.assertEqual(alert.evaluate(), Alert.UNKNOWN_STATE)

    def test_evaluate_return_unknown_when_empty_results(self):
        results = {"rows": [], "columns": [{"name": "foo", "type": "STRING"}]}
        alert = self.create_alert(results)
        self.assertEqual(alert.evaluate(), Alert.UNKNOWN_STATE)

    def test_evaluates_correctly_with_first_selector(self):
        results = {"rows": [{"foo": 1}, {"foo": 2}], "columns": [{"name": "foo", "type": "INTEGER"}]}
        alert = self.create_alert(results)
        alert.options["selector"] = "first"
        self.assertEqual(alert.evaluate(), Alert.TRIGGERED_STATE)
        results = {
            "rows": [{"foo": "test"}, {"foo": "test"}, {"foo": "test"}],
            "columns": [{"name": "foo", "type": "STRING"}],
        }
        alert = self.create_alert(results)
        alert.options["selector"] = "first"
        alert.options["op"] = "<"
        self.assertEqual(alert.evaluate(), Alert.UNKNOWN_STATE)

    def test_evaluates_correctly_with_min_selector(self):
        results = {"rows": [{"foo": 2}, {"foo": 1}], "columns": [{"name": "foo", "type": "INTEGER"}]}
        alert = self.create_alert(results)
        alert.options["selector"] = "min"
        self.assertEqual(alert.evaluate(), Alert.TRIGGERED_STATE)
        results = {
            "rows": [{"foo": "test"}, {"foo": "test"}, {"foo": "test"}],
            "columns": [{"name": "foo", "type": "STRING"}],
        }
        alert = self.create_alert(results)
        alert.options["selector"] = "min"
        self.assertEqual(alert.evaluate(), Alert.UNKNOWN_STATE)

    def test_evaluates_correctly_with_max_selector(self):
        results = {"rows": [{"foo": 1}, {"foo": 2}], "columns": [{"name": "foo", "type": "INTEGER"}]}
        alert = self.create_alert(results)
        alert.options["selector"] = "max"
        self.assertEqual(alert.evaluate(), Alert.OK_STATE)
        results = {
            "rows": [{"foo": "test"}, {"foo": "test"}, {"foo": "test"}],
            "columns": [{"name": "foo", "type": "STRING"}],
        }
        alert = self.create_alert(results)
        alert.options["selector"] = "max"
        self.assertEqual(alert.evaluate(), Alert.UNKNOWN_STATE)

    def test_evaluate_alerts_without_query_rel(self):
        query = self.factory.create_query(latest_query_data_id=None)
        alert = self.factory.create_alert(
            query_rel=query, options={"selector": "first", "op": "equals", "column": "foo", "value": "1"}
        )
        self.assertEqual(alert.evaluate(), Alert.UNKNOWN_STATE)

    def test_evaluate_return_unknown_when_value_is_none(self):
        alert = self.create_alert(get_results(None))
        self.assertEqual(alert.evaluate(), Alert.UNKNOWN_STATE)


class TestNextState(TestCase):
    def test_numeric_value(self):
        self.assertEqual(Alert.TRIGGERED_STATE, next_state(OPERATORS.get("=="), 1, "1"))
        self.assertEqual(Alert.TRIGGERED_STATE, next_state(OPERATORS.get("=="), 1, "1.0"))
        self.assertEqual(Alert.TRIGGERED_STATE, next_state(OPERATORS.get(">"), "5", 1))

    def test_numeric_value_and_plain_string(self):
        self.assertEqual(Alert.UNKNOWN_STATE, next_state(OPERATORS.get("=="), 1, "string"))

    def test_non_numeric_value(self):
        self.assertEqual(Alert.OK_STATE, next_state(OPERATORS.get("=="), "string", "1.0"))

    def test_string_value(self):
        self.assertEqual(Alert.TRIGGERED_STATE, next_state(OPERATORS.get("=="), "string", "string"))

    def test_boolean_value(self):
        self.assertEqual(Alert.TRIGGERED_STATE, next_state(OPERATORS.get("=="), False, "false"))
        self.assertEqual(Alert.TRIGGERED_STATE, next_state(OPERATORS.get("!="), False, "true"))


class TestAlertRenderTemplate(BaseTestCase):
    def create_alert(self, results, column="foo", value="5"):
        result = self.factory.create_query_result(data=results)
        query = self.factory.create_query(latest_query_data_id=result.id)
        alert = self.factory.create_alert(
            query_rel=query, options={"selector": "first", "op": "equals", "column": column, "value": value}
        )
        return alert

    def test_render_custom_alert_template(self):
        old_host = settings.HOST
        old_multi_org = settings.MULTI_ORG
        settings.HOST = "http://localhost:5001"
        settings.MULTI_ORG = False
        alert = self.create_alert(get_results(1))
        custom_alert = """
        <pre>
        ALERT_STATUS        {{ALERT_STATUS}}
        ALERT_SELECTOR      {{ALERT_SELECTOR}}
        ALERT_CONDITION     {{ALERT_CONDITION}}
        ALERT_THRESHOLD     {{ALERT_THRESHOLD}}
        ALERT_NAME          {{ALERT_NAME}}
        ALERT_URL           {{{ALERT_URL}}}
        QUERY_NAME          {{QUERY_NAME}}
        QUERY_URL           {{{QUERY_URL}}}
        QUERY_RESULT_VALUE  {{QUERY_RESULT_VALUE}}
        QUERY_RESULT_ROWS   {{{QUERY_RESULT_ROWS}}}
        QUERY_RESULT_COLS   {{{QUERY_RESULT_COLS}}}
        </pre>
        """
        expected = """
        <pre>
        ALERT_STATUS        UNKNOWN
        ALERT_SELECTOR      first
        ALERT_CONDITION     equals
        ALERT_THRESHOLD     5
        ALERT_NAME          %s
        ALERT_URL           http://localhost:5001/alerts/%d
        QUERY_NAME          Query
        QUERY_URL           http://localhost:5001/queries/%d
        QUERY_RESULT_VALUE  1
        QUERY_RESULT_ROWS   [{'foo': 1}]
        QUERY_RESULT_COLS   [{'name': 'foo', 'type': 'STRING'}]
        </pre>
        """ % (
            alert.name,
            alert.id,
            alert.query_id,
        )
        result = alert.render_template(textwrap.dedent(custom_alert))
        self.assertMultiLineEqual(result, textwrap.dedent(expected))
        settings.HOST = old_host
        settings.MULTI_ORG = old_multi_org

    def test_render_custom_alert_template_query_table(self):
        alert = self.create_alert(get_results(1))
        custom_alert = """
        <table>
        {{#QUERY_RESULT_TABLE}}
          <tr>
            {{#.}}
            <td>{{.}}</td>
            {{/.}}
          </tr>
        {{/QUERY_RESULT_TABLE}}
        </table>
        """
        expected = """
        <table>
          <tr>
            <td>1</td>
          </tr>
        </table>
        """
        result = alert.render_template(textwrap.dedent(custom_alert))
        self.assertMultiLineEqual(result, textwrap.dedent(expected))
