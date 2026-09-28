from django.urls import path

from apps.web.views import (
    auth,
    boarding,
    bookings,
    checkout,
    home,
    payment,
    profile,
    search,
    seats,
    support,
    ticket,
)

app_name = "web"

urlpatterns = [
    path("", home.home_view, name="home"),
    path("search/", search.search_results_view, name="search_results"),
    path("trips/<uuid:public_id>/seats/", seats.seat_selection_view, name="seat_selection"),
    path("seats/release/", seats.release_hold_view, name="release_hold"),
    path("boarding-stops/", boarding.boarding_stop_view, name="boarding_stops"),
    path("checkout/", checkout.checkout_view, name="checkout"),
    path("payment-result/", payment.payment_result_view, name="payment_result"),
    path("ticket/", ticket.ticket_view, name="ticket"),
    path("ticket/pdf/", ticket.ticket_pdf_view, name="ticket_pdf"),
    path("my-bookings/", bookings.my_bookings_view, name="my_bookings"),
    path("bookings/<uuid:public_id>/", bookings.booking_detail_view, name="booking_detail"),
    path("profile/", profile.profile_view, name="profile"),
    path(
        "profile/saved-passengers/add/",
        profile.saved_passenger_create_view,
        name="saved_passenger_create",
    ),
    path(
        "profile/saved-passengers/<uuid:public_id>/edit/",
        profile.saved_passenger_update_view,
        name="saved_passenger_update",
    ),
    path(
        "profile/saved-passengers/<uuid:public_id>/delete/",
        profile.saved_passenger_delete_view,
        name="saved_passenger_delete",
    ),
    path("support/", support.support_view, name="support"),
    path("set-currency/", home.set_currency_view, name="set_currency"),
    path("login/", auth.login_view, name="login"),
    path("logout/", auth.logout_view, name="logout"),
    path("register/", auth.register_view, name="register"),
    path("forgot-password/", auth.forgot_password_request_view, name="forgot_password_request"),
    path(
        "forgot-password/confirm/",
        auth.forgot_password_confirm_view,
        name="forgot_password_confirm",
    ),
]
