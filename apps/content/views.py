from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import generics, permissions, viewsets

from apps.core.permissions import IsAdmin

from . import services
from .models import Announcement, Banner
from .serializers import AnnouncementSerializer, BannerSerializer


class AdminBannerViewSet(viewsets.ModelViewSet):
    queryset = Banner.objects.all()
    serializer_class = BannerSerializer
    permission_classes = [IsAdmin]
    lookup_field = "public_id"


class AdminAnnouncementViewSet(viewsets.ModelViewSet):
    queryset = Announcement.objects.all()
    serializer_class = AnnouncementSerializer
    permission_classes = [IsAdmin]
    lookup_field = "public_id"


@extend_schema(parameters=[OpenApiParameter("placement", str, enum=Banner.Placement.values)])
class PublicBannerListView(generics.ListAPIView):
    """Live banners for the passenger site (active and inside their schedule)."""

    serializer_class = BannerSerializer
    permission_classes = [permissions.AllowAny]
    pagination_class = None

    def get_queryset(self):
        return services.live_banners(self.request.query_params.get("placement"))


@extend_schema(parameters=[OpenApiParameter("audience", str, enum=["passengers", "operators"])])
class PublicAnnouncementListView(generics.ListAPIView):
    serializer_class = AnnouncementSerializer
    permission_classes = [permissions.AllowAny]
    pagination_class = None

    def get_queryset(self):
        audience = self.request.query_params.get("audience", "passengers")
        return services.live_announcements("operators" if audience == "operators" else "passengers")
