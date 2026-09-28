from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("operator/trips", views.TripViewSet, basename="operator-trip")

urlpatterns = [
    path("", include(router.urls)),
    path("trips/search/", views.TripSearchView.as_view(), name="trip-search"),
    path("trips/<uuid:public_id>/", views.TripDetailView.as_view(), name="trip-detail"),
    path("trips/<uuid:public_id>/seats/", views.TripSeatMapView.as_view(), name="trip-seat-map"),
    path("trips/<uuid:public_id>/stops/", views.TripStopsView.as_view(), name="trip-stops"),
    path("trips/<uuid:public_id>/hold/", views.HoldSeatsView.as_view(), name="trip-hold-seats"),
    path("trips/<uuid:public_id>/release/", views.ReleaseSeatsView.as_view(), name="trip-release-seats"),
]
