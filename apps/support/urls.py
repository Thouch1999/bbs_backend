from django.urls import path

from . import views

urlpatterns = [
    path("support/tickets/", views.MyTicketListCreateView.as_view(), name="support-my-tickets"),
    path("support/tickets/<uuid:public_id>/", views.MyTicketDetailView.as_view(), name="support-my-ticket"),
    path("admin/support/tickets/", views.AdminTicketListView.as_view(), name="admin-support-tickets"),
    path(
        "admin/support/tickets/<uuid:public_id>/",
        views.AdminTicketDetailView.as_view(),
        name="admin-support-ticket",
    ),
    path(
        "admin/support/tickets/<uuid:public_id>/reply/",
        views.AdminTicketReplyView.as_view(),
        name="admin-support-ticket-reply",
    ),
]
