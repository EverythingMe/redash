import sys

from rq import Queue as BaseQueue
from rq.job import Job as BaseJob
from rq.job import JobStatus
from rq.worker import (
    HerokuWorker,  # HerokuWorker implements graceful shutdown on SIGTERM
    Worker,
)

from redash import statsd_client

# HerokuWorker does not work in OSX https://github.com/getredash/redash/issues/5413
if sys.platform == "darwin":
    BaseWorker = Worker
else:
    BaseWorker = HerokuWorker


class CancellableJob(BaseJob):
    def cancel(self, pipeline=None):
        self.meta["cancelled"] = True
        self.save_meta()

        super().cancel(pipeline=pipeline)

    @property
    def is_cancelled(self):
        return self.meta.get("cancelled", False)


class StatsdRecordingQueue(BaseQueue):
    """
    RQ Queue Mixin that overrides `enqueue_call` to increment metrics via Statsd
    """

    def enqueue_job(self, *args, **kwargs):
        job = super().enqueue_job(*args, **kwargs)
        statsd_client.incr("rq.jobs.created.{}".format(self.name))
        return job


class CancellableQueue(BaseQueue):
    job_class = CancellableJob


class RedashQueue(StatsdRecordingQueue, CancellableQueue):
    pass


class StatsdRecordingWorker(BaseWorker):
    """
    RQ Worker Mixin that overrides `execute_job` to increment/modify metrics via Statsd
    """

    def execute_job(self, job, queue):
        statsd_client.incr("rq.jobs.running.{}".format(queue.name))
        statsd_client.incr("rq.jobs.started.{}".format(queue.name))
        try:
            super().execute_job(job, queue)
        finally:
            statsd_client.decr("rq.jobs.running.{}".format(queue.name))
            if job.get_status() == JobStatus.FINISHED:
                statsd_client.incr("rq.jobs.finished.{}".format(queue.name))
            else:
                statsd_client.incr("rq.jobs.failed.{}".format(queue.name))


class RedashWorker(StatsdRecordingWorker):
    queue_class = RedashQueue


Job = CancellableJob
Queue = RedashQueue
Worker = RedashWorker
