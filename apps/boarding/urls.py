from django.urls import path

from . import views

urlpatterns = [
    path(
        "boarding/trips/<uuid:public_id>/validate-qr/",
        views.ValidateQrView.as_view(),
        name="boarding-validate-qr",
    ),
    path(
        "boarding/trips/<uuid:public_id>/board-pnr/",
        views.BoardByPnrView.as_view(),
        name="boarding-board-pnr",
    ),
    path("boarding/trips/<uuid:public_id>/sync/", views.OfflineSyncView.as_view(), name="boarding-sync"),
    path("boarding/trips/<uuid:public_id>/manifest/", views.ManifestView.as_view(), name="boarding-manifest"),
]
