from __future__ import absolute_import
from celery import Celery

_includes = (
    'cumulus.starcluster.tasks.cluster',
    'cumulus.starcluster.tasks.job',
    'cumulus.moab.tasks.mesh',
    'cumulus.task.status'
)

# Route short tasks to their own queue
routes = {
    'cumulus.starcluster.tasks.job.monitor_job': {
        'queue': 'monitor'
    },
    'cumulus.starcluster.tasks.job.monitor_process': {
        'queue': 'monitor'
    },
    'cumulus.task.status.monitor_status': {
        'queue': 'monitor'
    }
}

command = Celery('command',  backend='amqp', broker='amqp://guest:guest@localhost:5672/',
             include=_includes)

command.config_from_object('cumulus.starcluster.tasks.commandconfig')
command.conf.update(
    CELERY_ROUTES=routes
)

monitor = Celery('monitor',  backend='amqp', broker='amqp://guest:guest@localhost:5672/',
             include=_includes)

monitor.config_from_object('cumulus.starcluster.tasks.monitorconfig')
monitor.conf.update(
    CELERY_ROUTES=routes
)
