from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("admin/cities", views.AdminCityViewSet, basename="admin-city")
router.register("cities", views.CityViewSet, basename="city")
router.register("stops", views.StopViewSet, basename="stop")
router.register("routes", views.RouteViewSet, basename="route")

route_stop_list = views.RouteStopViewSet.as_view({"get": "list", "post": "create"})
route_stop_detail = views.RouteStopViewSet.as_view(
    {"get": "retrieve", "put": "update", "patch": "partial_update", "delete": "destroy"}
)

urlpatterns = [
    path("", include(router.urls)),
    path("routes/<uuid:route_public_id>/stops/", route_stop_list, name="route-stop-list"),
    path(
        "routes/<uuid:route_public_id>/stops/bulk/",
        views.RouteStopListReplaceView.as_view(),
        name="route-stop-list-replace",
    ),
    path(
        "routes/<uuid:route_public_id>/stops/<uuid:public_id>/",
        route_stop_detail,
        name="route-stop-detail",
    ),
]
