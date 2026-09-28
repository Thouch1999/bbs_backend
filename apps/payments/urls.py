from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("admin/promo-codes", views.AdminPromoCodeViewSet, basename="admin-promo-code")

urlpatterns = [
    path("", include(router.urls)),
    path("admin/payments/", views.AdminPaymentListView.as_view(), name="admin-payment-list"),
    path(
        "admin/payments/<uuid:public_id>/",
        views.AdminPaymentDetailView.as_view(),
        name="admin-payment-detail",
    ),
    path(
        "admin/reconciliation/summary/",
        views.AdminReconciliationSummaryView.as_view(),
        name="admin-reconciliation-summary",
    ),
    path("admin/payouts/balances/", views.AdminOperatorBalancesView.as_view(), name="admin-payout-balances"),
    path("admin/payouts/", views.AdminPayoutListCreateView.as_view(), name="admin-payout-list"),
    path("payments/", views.InitiatePaymentView.as_view(), name="payment-initiate"),
    path("payments/webhook/<str:provider>/", views.WebhookView.as_view(), name="payment-webhook"),
    path(
        "payments/<uuid:public_id>/mark-counter-paid/",
        views.MarkCounterPaidView.as_view(),
        name="payment-mark-counter-paid",
    ),
    path("payments/<uuid:public_id>/refund/", views.CreateRefundView.as_view(), name="payment-refund"),
    path("promo-codes/validate/", views.PromoValidateView.as_view(), name="promo-validate"),
]
