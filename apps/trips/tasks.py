from celery import shared_task

from . import services


@shared_task
def release_expired_holds():
    return services.release_expired_holds()
