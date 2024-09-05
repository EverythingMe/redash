import requests
from flask_mail import Message

from redash import mail, models, settings
from redash.models import users
from redash.query_runner import NotSupported
from redash.tasks.worker import Queue
from redash.worker import get_job_logger, job

logger = get_job_logger(__name__)


@job("default")
def record_event(raw_event):
    event = models.Event.record(raw_event)
    models.db.session.commit()

    for hook in settings.EVENT_REPORTING_WEBHOOKS:
        logger.debug("Forwarding event to: %s", hook)
        try:
            data = {
                "schema": "iglu:io.redash.webhooks/event/jsonschema/1-0-0",
                "data": event.to_dict(),
            }
            response = requests.post(hook, json=data)
            if response.status_code != 200:
                logger.error("Failed posting to %s: %s", hook, response.content)
        except Exception:
            logger.exception("Failed posting to %s", hook)


@job("emails")
def send_mail(to, subject, html, text):
    try:
        message = Message(recipients=to, subject=subject, html=html, body=text)

        mail.send(message)
    except Exception:
        logger.exception("Failed sending message: %s", message.subject)


@job("queries", timeout=30, ttl=90)
def test_connection(data_source_id):
    try:
        data_source = models.DataSource.get_by_id(data_source_id)
        data_source.query_runner.test_connection()
        return True
    except Exception as e:
        return e


@job("schemas", queue_class=Queue, at_front=True, timeout=300, ttl=90)
def get_schema(data_source_id, refresh):
    try:
        data_source = models.DataSource.get_by_id(data_source_id)
        return data_source.get_schema(refresh)
    except NotSupported:
        return {
            "error": {
                "code": 1,
                "message": "Data source type does not support retrieving schema",
            }
        }
    except Exception as e:
        return {"error": {"code": 2, "message": "Error retrieving schema", "details": str(e)}}


def sync_user_details():
    users.sync_last_active_at()
