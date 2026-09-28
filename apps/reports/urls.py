from django.urls import path

from . import views

urlpatterns = [
    path(
        "reports/operator/dashboard/", views.OperatorDashboardView.as_view(), name="report-operator-dashboard"
    ),
    path("reports/operator/revenue/", views.OperatorRevenueView.as_view(), name="report-operator-revenue"),
    path(
        "reports/operator/occupancy/", views.OperatorOccupancyView.as_view(), name="report-operator-occupancy"
    ),
    path(
        "reports/operator/cancellations/",
        views.OperatorCancellationSummaryView.as_view(),
        name="report-operator-cancellations",
    ),
    path(
        "reports/operator/trips/<uuid:public_id>/manifest/export/",
        views.OperatorManifestExportView.as_view(),
        name="report-operator-manifest-export",
    ),
    path("reports/admin/overview/", views.AdminOverviewView.as_view(), name="report-admin-overview"),
    path("reports/admin/operators/", views.AdminOperatorSummaryView.as_view(), name="report-admin-operators"),
    path("reports/admin/daily/", views.AdminDailySeriesView.as_view(), name="report-admin-daily"),
    path(
        "reports/admin/system-status/",
        views.AdminSystemStatusView.as_view(),
        name="report-admin-system-status",
    ),
    path("reports/admin/totals/", views.AdminPlatformTotalsView.as_view(), name="report-admin-totals"),
    path("reports/admin/top-routes/", views.AdminTopRoutesView.as_view(), name="report-admin-top-routes"),
    path(
        "reports/admin/reconciliation/",
        views.AdminReconciliationView.as_view(),
        name="report-admin-reconciliation",
    ),
]
