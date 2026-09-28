import os

import redis
from celery import Celery
from celery.schedules import crontab

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")

# Same reason as CACHES["default"]["OPTIONS"] = {"protocol": 2} in
# config/settings/base.py: redis-py 8 defaults to RESP3 (sends HELLO on
# connect), which the local dev Redis (5.0.14) doesn't understand.
#
# A first pass at this only patched kombu.transport.redis.Channel's
# connection class, which covers the Celery *broker*. It missed the Celery
# *result backend* (celery.backends.redis.RedisBackend), which opens its
# own redis-py connections directly (e.g. the pubsub connection in
# consume_from/subscribe) — confirmed by an actual `unknown command
# 'HELLO'` 500 the first time a real request in this environment triggered
# a task whose result Celery tries to track. Patching redis.Connection's
# __init__ in place, rather than swapping in a subclass some callers don't
# reference, fixes every caller (Kombu, the result backend, and anything
# else built on redis-py) in one place — protocol=2 (RESP2) is also what
# any newer Redis still speaks, so this is safe in production too.
#
# Step 15 found the connection-level patch alone still isn't enough for
# redis-py 8.1: ConnectionPool.__init__ decides, before any connection
# exists, that a pool with no explicit `protocol` is RESP3 and attaches a
# MaintNotificationsConfig to every connection it makes. The forced
# protocol=2 connection then rejects that config ("Maintenance
# notifications are only supported with hiredis and RESP3 parsers!") — so
# every Celery task dispatch (Kombu's broker pool) failed, e.g. the seat
# booking confirmation fan-out in seed_demo. Defaulting protocol=2 on the
# pool too means the pool never enables maintenance notifications at all.
_original_connection_init = redis.Connection.__init__
_original_pool_init = redis.ConnectionPool.__init__


def _resp2_connection_init(self, *args, **kwargs):
    kwargs.setdefault("protocol", 2)
    _original_connection_init(self, *args, **kwargs)


def _resp2_pool_init(self, *args, **kwargs):
    kwargs.setdefault("protocol", 2)
    _original_pool_init(self, *args, **kwargs)


redis.Connection.__init__ = _resp2_connection_init
redis.ConnectionPool.__init__ = _resp2_pool_init

app = Celery("bbms")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

app.conf.beat_schedule = {
    "release-expired-seat-holds": {
        "task": "apps.trips.tasks.release_expired_holds",
        "schedule": crontab(minute="*/1"),
    },
    "send-departure-reminders": {
        "task": "apps.notifications.tasks.send_departure_reminders",
        "schedule": crontab(minute="*/5"),
    },
}
