from django.urls import path

from . import views

urlpatterns = [
    path("operator/bookings/", views.OperatorBookingListView.as_view(), name="operator-booking-list"),
    path(
        "operator/bookings/<uuid:public_id>/cancel/",
        views.OperatorCancelBookingView.as_view(),
        name="operator-booking-cancel",
    ),
    path(
        "operator/bookings/<uuid:public_id>/cancellation-preview/",
        views.OperatorCancellationPreviewView.as_view(),
        name="operator-booking-cancellation-preview",
    ),
    path("operator/customers/lookup/", views.CustomerLookupView.as_view(), name="operator-customer-lookup"),
    path("bookings/", views.CreateBookingView.as_view(), name="booking-create"),
    path("bookings/agent/", views.AgentCreateBookingView.as_view(), name="booking-agent-create"),
    path("bookings/lookup/", views.GuestBookingLookupView.as_view(), name="booking-guest-lookup"),
    path("bookings/my/", views.MyBookingsView.as_view(), name="booking-my"),
    path("bookings/<uuid:public_id>/", views.BookingDetailView.as_view(), name="booking-detail"),
    path("bookings/<uuid:public_id>/cancel/", views.CancelBookingView.as_view(), name="booking-cancel"),
    path(
        "bookings/<uuid:public_id>/cancellation-preview/",
        views.CancellationPreviewView.as_view(),
        name="booking-cancellation-preview",
    ),
    path(
        "bookings/<uuid:public_id>/reschedule/",
        views.RescheduleBookingView.as_view(),
        name="booking-reschedule",
    ),
    path(
        "bookings/<uuid:public_id>/reschedule-preview/",
        views.ReschedulePreviewView.as_view(),
        name="booking-reschedule-preview",
    ),
]
