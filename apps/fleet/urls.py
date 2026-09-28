from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("seat-layouts", views.SeatLayoutViewSet, basename="seat-layout")
router.register("buses", views.BusViewSet, basename="bus")

urlpatterns = [
    path("", include(router.urls)),
]
