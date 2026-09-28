from django.db.models import Q
from django.utils import timezone

from .models import Announcement, Banner


def _live(qs, now=None):
    now = now or timezone.now()
    return qs.filter(is_active=True).filter(
        Q(starts_at__isnull=True) | Q(starts_at__lte=now), Q(ends_at__isnull=True) | Q(ends_at__gt=now)
    )


def live_banners(placement: str | None = None):
    qs = _live(Banner.objects.all())
    return qs.filter(placement=placement) if placement else qs


def live_announcements(audience: str):
    """audience: "passengers" or "operators" — "all" announcements reach both."""
    return _live(Announcement.objects.filter(audience__in=[Announcement.Audience.ALL, audience]))
