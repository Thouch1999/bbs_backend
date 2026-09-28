from django.urls import path

from . import views

urlpatterns = [
    path("operators/register/", views.OperatorRegisterView.as_view(), name="operator-register"),
    path("operators/me/", views.OperatorMeView.as_view(), name="operator-me"),
    path("operator/staff/", views.StaffListCreateView.as_view(), name="operator-staff-list"),
    path("operator/staff/<uuid:public_id>/", views.StaffDetailView.as_view(), name="operator-staff-detail"),
    path("operators/", views.OperatorListView.as_view(), name="operator-list"),
    path("operators/<uuid:public_id>/", views.AdminOperatorDetailView.as_view(), name="operator-detail"),
    path(
        "operators/<uuid:public_id>/checklist/",
        views.OperatorChecklistView.as_view(),
        name="operator-checklist",
    ),
    path("operators/<uuid:public_id>/notes/", views.OperatorReviewNoteView.as_view(), name="operator-notes"),
    path("operators/<uuid:public_id>/suspend/", views.OperatorSuspendView.as_view(), name="operator-suspend"),
    path(
        "operators/<uuid:public_id>/reinstate/",
        views.OperatorReinstateView.as_view(),
        name="operator-reinstate",
    ),
    path("operators/<uuid:public_id>/approve/", views.OperatorApproveView.as_view(), name="operator-approve"),
    path("operators/<uuid:public_id>/reject/", views.OperatorRejectView.as_view(), name="operator-reject"),
]
