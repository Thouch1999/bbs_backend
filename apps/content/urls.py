from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("admin/banners", views.AdminBannerViewSet, basename="admin-banner")
router.register("admin/announcements", views.AdminAnnouncementViewSet, basename="admin-announcement")

urlpatterns = [
    path("", include(router.urls)),
    path("content/banners/", views.PublicBannerListView.as_view(), name="content-banners"),
    path("content/announcements/", views.PublicAnnouncementListView.as_view(), name="content-announcements"),
]
