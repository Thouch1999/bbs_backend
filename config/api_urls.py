from django.urls import include, path

from apps.core.views import AdminAuditLogListView, PublicConfigView

urlpatterns = [
    path("config/", PublicConfigView.as_view(), name="public-config"),
    path("admin/audit-logs/", AdminAuditLogListView.as_view(), name="admin-audit-logs"),
    path("", include("apps.accounts.urls")),
    path("", include("apps.operators.urls")),
    path("", include("apps.routes.urls")),
    path("", include("apps.fleet.urls")),
    path("", include("apps.trips.urls")),
    path("", include("apps.bookings.urls")),
    path("", include("apps.payments.urls")),
    # notifications (Step 9) has no HTTP API of its own — it's fan-out
    # triggered internally by bookings/payments/trips, per the playbook.
    path("", include("apps.boarding.urls")),
    path("", include("apps.reports.urls")),
    path("", include("apps.content.urls")),
    path("", include("apps.support.urls")),
]
