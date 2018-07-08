import json
import time

from flask import request
from flask_login import current_user, login_required
from redash import models, redis_connection
from redash.authentication import current_org
from redash.handlers import routes
from redash.handlers.base import json_response
from redash.permissions import require_super_admin
from redash.tasks.queries import QueryTaskTracker
from redash.tasks import record_event


@routes.route('/api/admin/queries/outdated', methods=['GET'])
@require_super_admin
@login_required
def outdated_queries():
    manager_status = redis_connection.hgetall('redash:status')
    query_ids = json.loads(manager_status.get('query_ids', '[]'))
    if query_ids:
        outdated_queries = (models.db.session.query(models.Query)
                            .outerjoin(models.QueryResult)
                            .filter(models.Query.id.in_(query_ids))
                            .order_by(models.Query.created_at.desc()))
    else:
        outdated_queries = []

    record_event({
        'action': 'list',
        'object_id': 'admin/outdated_queries',
        'object_type': 'outdated_queries'
    })

    return json_response(
        dict(queries=[q.to_dict(with_stats=True, with_last_modified_by=False)
                      for q in outdated_queries],
             updated_at=manager_status['last_refresh_at']))


@routes.route('/api/admin/queries/tasks', methods=['GET'])
@require_super_admin
@login_required
def queries_tasks():
    record_event({
        'action': 'list',
        'object_type': 'celery_tasks',
        'object_id': 'admin/tasks',
    })
    global_limit = int(request.args.get('limit', 50))
    waiting_limit = int(request.args.get('waiting_limit', global_limit))
    progress_limit = int(request.args.get('progress_limit', global_limit))
    done_limit = int(request.args.get('done_limit', global_limit))

    waiting = QueryTaskTracker.all(QueryTaskTracker.WAITING_LIST, limit=waiting_limit)
    in_progress = QueryTaskTracker.all(QueryTaskTracker.IN_PROGRESS_LIST, limit=progress_limit)
    done = QueryTaskTracker.all(QueryTaskTracker.DONE_LIST, limit=done_limit)
    record_event({
        'action': 'view',
        'object_type': 'api_call',
        'object_id': 'admin/tasks',
    })

    response = {
        'waiting': [t.data for t in waiting if t is not None],
        'in_progress': [t.data for t in in_progress if t is not None],
        'done': [t.data for t in done if t is not None]
    }

    return json_response(response)
