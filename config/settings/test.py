from .development import *  # noqa: F401,F403

# MD5 is fine for tests — we're not protecting anything, just speeding up
# the hundreds of User.objects.create_user() calls across the test suite.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# Run Celery tasks (notification fan-out) inline, synchronously, so tests
# don't depend on a worker process consuming the broker.
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
