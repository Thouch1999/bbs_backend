import time

from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import connections
from django.db.utils import OperationalError

_LOCK_KEY = "deploy:bootstrap-lock"
# Long enough for migrate + collectstatic even on a cold first boot; if the
# winning replica dies mid-bootstrap the lock still expires instead of
# wedging every other replica forever.
_LOCK_TIMEOUT_SECONDS = 300
_WAIT_POLL_SECONDS = 2


class Command(BaseCommand):
    """
    Runs pending migrations and collectstatic exactly once per deploy, even
    when multiple backend replicas start at the same time. Only the replica
    that wins a Redis lock (cache.add — an atomic SET NX under the hood)
    actually runs them; every other replica waits for the lock to clear
    (meaning the winner finished) before its entrypoint continues on to
    start gunicorn. Safe to call unconditionally from every replica's
    entrypoint.
    """

    help = "Run migrate + collectstatic exactly once across concurrently-starting replicas."

    def handle(self, *args, **options):
        self._wait_for_database()

        if cache.add(_LOCK_KEY, "1", timeout=_LOCK_TIMEOUT_SECONDS):
            self.stdout.write("Acquired deploy lock — running migrate and collectstatic.")
            try:
                call_command("migrate", interactive=False)
                call_command("collectstatic", interactive=False, verbosity=0)
            finally:
                cache.delete(_LOCK_KEY)
            self.stdout.write("Deploy bootstrap complete.")
            return

        self.stdout.write("Another replica holds the deploy lock — waiting for it to finish.")
        while cache.get(_LOCK_KEY) is not None:
            time.sleep(_WAIT_POLL_SECONDS)
        self.stdout.write("Deploy lock cleared — proceeding.")

    def _wait_for_database(self, *, max_attempts=30):
        for attempt in range(1, max_attempts + 1):
            try:
                with connections["default"].cursor() as cursor:
                    cursor.execute("SELECT 1")
                return
            except OperationalError:
                if attempt == max_attempts:
                    raise
                time.sleep(2)
