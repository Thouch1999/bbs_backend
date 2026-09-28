from django.db.models import Case, IntegerField, Q, Value, When
from drf_spectacular.utils import extend_schema
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import IsAdmin
from apps.core.serializers import ErrorResponseSerializer

from . import services
from .models import SupportTicket
from .serializers import (
    AdminTicketQuerySerializer,
    MessageCreateSerializer,
    OpenTicketSerializer,
    StaffReplySerializer,
    SupportMessageSerializer,
    SupportTicketDetailSerializer,
    SupportTicketSerializer,
    TicketUpdateSerializer,
)


def _tickets():
    return SupportTicket.objects.select_related("booking").prefetch_related("messages__author")


class MyTicketListCreateView(APIView):
    """A signed-in customer's own tickets; POST opens one (optionally about
    one of their bookings)."""

    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(responses=SupportTicketSerializer(many=True))
    def get(self, request):
        return Response(SupportTicketSerializer(_tickets().filter(user=request.user), many=True).data)

    @extend_schema(
        request=OpenTicketSerializer,
        responses={201: SupportTicketDetailSerializer, 400: ErrorResponseSerializer},
    )
    def post(self, request):
        serializer = OpenTicketSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        booking = data.get("booking_id")
        if booking is not None and booking.user_id != request.user.id:
            error = {"code": "not_your_booking", "message": "That booking is not on your account."}
            return Response({"error": error}, status=status.HTTP_400_BAD_REQUEST)
        ticket = services.open_ticket(
            category=data["category"],
            subject=data["subject"],
            body=data["body"],
            contact_phone=data.get("contact_phone") or request.user.phone,
            contact_name=data.get("contact_name") or request.user.full_name,
            user=request.user,
            booking=booking,
            language=request.user.preferred_language,
        )
        return Response(
            SupportTicketDetailSerializer(_tickets().get(pk=ticket.pk)).data, status=status.HTTP_201_CREATED
        )


class MyTicketDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def _ticket(self, request, public_id):
        return generics.get_object_or_404(_tickets(), public_id=public_id, user=request.user)

    @extend_schema(responses=SupportTicketDetailSerializer)
    def get(self, request, public_id):
        return Response(SupportTicketDetailSerializer(self._ticket(request, public_id)).data)

    @extend_schema(
        operation_id="support_tickets_reply",
        request=MessageCreateSerializer,
        responses={201: SupportMessageSerializer},
    )
    def post(self, request, public_id):
        ticket = self._ticket(request, public_id)
        serializer = MessageCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        message = services.add_customer_message(
            ticket, body=serializer.validated_data["body"], user=request.user
        )
        return Response(SupportMessageSerializer(message).data, status=status.HTTP_201_CREATED)


@extend_schema(parameters=[AdminTicketQuerySerializer])
class AdminTicketListView(generics.ListAPIView):
    serializer_class = SupportTicketSerializer
    permission_classes = [IsAdmin]

    def get_queryset(self):
        query = AdminTicketQuerySerializer(data=self.request.query_params)
        query.is_valid(raise_exception=True)
        params = query.validated_data
        qs = _tickets()
        if params.get("category"):
            qs = qs.filter(category=params["category"])
        if params.get("status"):
            qs = qs.filter(status=params["status"])
        if params.get("search"):
            term = params["search"].strip()
            qs = qs.filter(
                Q(booking__pnr__iexact=term)
                | Q(contact_phone__icontains=term)
                | Q(contact_name__icontains=term)
                | Q(subject__icontains=term)
            )
        # Unresolved first (open, then waiting on the customer), then the most
        # urgent SLA. An explicit rank, not order_by("status"): alphabetically
        # "resolved" would sort before "waiting_customer".
        rank = Case(
            When(status=SupportTicket.Status.OPEN, then=Value(0)),
            When(status=SupportTicket.Status.WAITING_CUSTOMER, then=Value(1)),
            default=Value(2),
            output_field=IntegerField(),
        )
        return qs.annotate(status_rank=rank).order_by("status_rank", "sla_due_at")


class AdminTicketDetailView(APIView):
    permission_classes = [IsAdmin]

    @extend_schema(responses=SupportTicketDetailSerializer)
    def get(self, request, public_id):
        return Response(
            SupportTicketDetailSerializer(generics.get_object_or_404(_tickets(), public_id=public_id)).data
        )

    @extend_schema(request=TicketUpdateSerializer, responses=SupportTicketDetailSerializer)
    def patch(self, request, public_id):
        ticket = generics.get_object_or_404(SupportTicket, public_id=public_id)
        serializer = TicketUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.update_ticket(ticket, **serializer.validated_data)
        return Response(SupportTicketDetailSerializer(_tickets().get(pk=ticket.pk)).data)


class AdminTicketReplyView(APIView):
    permission_classes = [IsAdmin]

    @extend_schema(request=StaffReplySerializer, responses={201: SupportTicketDetailSerializer})
    def post(self, request, public_id):
        ticket = generics.get_object_or_404(SupportTicket, public_id=public_id)
        serializer = StaffReplySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.reply_as_staff(ticket, staff=request.user, **serializer.validated_data)
        return Response(
            SupportTicketDetailSerializer(_tickets().get(pk=ticket.pk)).data, status=status.HTTP_201_CREATED
        )
