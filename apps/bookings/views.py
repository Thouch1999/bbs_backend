from datetime import datetime, time, timedelta
from decimal import Decimal

from django.db.models import Q
from django.utils import timezone
from drf_spectacular.utils import OpenApiExample, extend_schema
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import IsCounterAgent, IsOperatorManagerOrCounterAgent
from apps.core.serializers import ErrorResponseSerializer
from apps.payments import services as payment_services
from apps.payments.models import Payment
from apps.trips import services as trip_services

from . import services
from .models import Booking
from .serializers import (
    BookingSerializer,
    CancelBookingSerializer,
    CancellationPreviewSerializer,
    CreateBookingSerializer,
    CustomerLookupQuerySerializer,
    CustomerLookupSerializer,
    GuestLookupSerializer,
    OperatorBookingQuerySerializer,
    OperatorBookingSerializer,
    RescheduleBookingSerializer,
    ReschedulePreviewRequestSerializer,
    ReschedulePreviewSerializer,
)


def _service_error_response(exc) -> Response:
    return Response({"error": {"code": exc.code, "message": str(exc)}}, status=status.HTTP_400_BAD_REQUEST)


_CREATE_BOOKING_EXAMPLE = OpenApiExample(
    "Two-seat web booking",
    value={
        "trip_id": "3fae1d2e-7c3a-4a1a-9b8e-7b1e2a6f9c11",
        "hold_token": "eyJhbGciOi...",
        "passengers": [
            {"full_name": "Sok Dara", "age": 30, "gender": "male"},
            {"full_name": "Chan Sopheak", "age": 27, "gender": "female"},
        ],
        "contact_phone": "012345678",
        "contact_email": "sokdara@example.com",
        "currency": "USD",
    },
    request_only=True,
)


def _resolve_discount(data, *, user):
    """Validates data['promo_code'] against the trip/passenger count, if given.
    Returns (discount_amount, error_response_or_None)."""
    promo_code = data.get("promo_code", "")
    if not promo_code:
        return Decimal(0), None

    trip = data["trip_id"]
    subtotal = services.estimate_subtotal(trip, data["currency"], len(data["passengers"]))
    try:
        discount = payment_services.validate_promo_code(
            promo_code,
            subtotal=subtotal,
            trip=trip,
            user=user,
            contact_phone=data.get("contact_phone", ""),
        )
    except payment_services.PromoCodeError as exc:
        return None, _service_error_response(exc)
    return discount, None


class CreateBookingView(APIView):
    """
    Booking requires a passenger account (ad hoc, per direct request — the
    underlying services.create_booking() still accepts user=None for
    AgentCreateBookingView's counter-staff-assisted flow below, and for the
    guest PNR+phone lookup on already-existing bookings). contact_phone is
    still always required, account or not, since it's how SMS notifications
    are addressed.
    """

    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        request=CreateBookingSerializer,
        responses={201: BookingSerializer, 400: ErrorResponseSerializer},
        examples=[_CREATE_BOOKING_EXAMPLE],
    )
    def post(self, request):
        serializer = CreateBookingSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        user = request.user
        discount, error_response = _resolve_discount(data, user=user)
        if error_response is not None:
            return error_response

        try:
            booking = services.create_booking(
                data["trip_id"].id,
                data["hold_token"],
                data["passengers"],
                contact_phone=data["contact_phone"],
                contact_email=data.get("contact_email"),
                user=user,
                currency=data["currency"],
                boarding_stop_id=data["boarding_stop_id"].id if data.get("boarding_stop_id") else None,
                drop_stop_id=data["drop_stop_id"].id if data.get("drop_stop_id") else None,
                discount_amount=discount,
                promo_code=data.get("promo_code", ""),
            )
        except (
            trip_services.InvalidHoldTokenError,
            trip_services.SeatsUnavailableError,
            services.InvalidPassengerCountError,
        ) as exc:
            return _service_error_response(exc)

        return Response(BookingSerializer(booking).data, status=status.HTTP_201_CREATED)


class AgentCreateBookingView(CreateBookingView):
    """Counter-staff-assisted booking (FR5.5): booking_channel=counter, booked_by_staff set."""

    permission_classes = [IsCounterAgent]

    @extend_schema(
        request=CreateBookingSerializer,
        responses={201: BookingSerializer, 400: ErrorResponseSerializer},
        examples=[_CREATE_BOOKING_EXAMPLE],
    )
    def post(self, request):
        serializer = CreateBookingSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        trip = data["trip_id"]
        if trip.route.operator_id != request.user.operator_staff.operator_id:
            error = {"code": "cross_operator", "message": "This trip belongs to a different operator."}
            return Response({"error": error}, status=status.HTTP_403_FORBIDDEN)

        discount, error_response = _resolve_discount(data, user=None)
        if error_response is not None:
            return error_response

        try:
            booking = services.create_booking(
                trip.id,
                data["hold_token"],
                data["passengers"],
                contact_phone=data["contact_phone"],
                contact_email=data.get("contact_email"),
                booking_channel=Booking.Channel.COUNTER,
                booked_by_staff=request.user,
                currency=data["currency"],
                boarding_stop_id=data["boarding_stop_id"].id if data.get("boarding_stop_id") else None,
                drop_stop_id=data["drop_stop_id"].id if data.get("drop_stop_id") else None,
                discount_amount=discount,
                promo_code=data.get("promo_code", ""),
            )
        except (
            trip_services.InvalidHoldTokenError,
            trip_services.SeatsUnavailableError,
            services.InvalidPassengerCountError,
        ) as exc:
            return _service_error_response(exc)

        return Response(BookingSerializer(booking).data, status=status.HTTP_201_CREATED)


class GuestBookingLookupView(APIView):
    """Retrieve a booking by PNR + the exact contact phone on file — no account needed."""

    permission_classes = [permissions.AllowAny]

    @extend_schema(
        parameters=[GuestLookupSerializer], responses={200: BookingSerializer, 404: ErrorResponseSerializer}
    )
    def get(self, request):
        serializer = GuestLookupSerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        booking = services.find_guest_booking(
            serializer.validated_data["pnr"], serializer.validated_data["phone"]
        )
        if booking is None:
            return Response(
                {"error": {"code": "not_found", "message": "No booking matches that PNR and phone."}},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(BookingSerializer(booking).data)


class MyBookingsView(generics.ListAPIView):
    """?when=upcoming|past — default upcoming."""

    serializer_class = BookingSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = (
            Booking.objects.filter(user=self.request.user)
            .select_related("trip")
            .prefetch_related("passengers__trip_seat__seat", "payments")
            .order_by("-created_at")
        )
        when = self.request.query_params.get("when", "upcoming")
        now = timezone.now()
        if when == "past":
            return qs.filter(trip__departure_at__lt=now)
        return qs.filter(trip__departure_at__gte=now)


class BookingDetailView(generics.RetrieveAPIView):
    queryset = Booking.objects.select_related("trip")
    serializer_class = BookingSerializer
    permission_classes = [permissions.IsAuthenticated]
    lookup_field = "public_id"

    def get_queryset(self):
        return super().get_queryset().filter(user=self.request.user)


class CancelBookingView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        request=CancelBookingSerializer, responses={200: BookingSerializer, 400: ErrorResponseSerializer}
    )
    def post(self, request, public_id):
        booking = generics.get_object_or_404(Booking, public_id=public_id, user=request.user)
        serializer = CancelBookingSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            booking = services.cancel_booking(
                booking, reason=serializer.validated_data.get("reason", ""), actor=request.user
            )
        except services.BookingStateError as exc:
            return _service_error_response(exc)
        return Response(BookingSerializer(booking).data)


class CancellationPreviewView(APIView):
    """Read-only: shows the refund the user would get, before they confirm
    cancelling. Never mutates the booking (SRS/playbook: never a
    client-side guess at the refund policy either)."""

    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(responses={200: CancellationPreviewSerializer})
    def get(self, request, public_id):
        booking = generics.get_object_or_404(Booking, public_id=public_id, user=request.user)
        refund_percentage, refund_amount = services.estimate_cancellation_refund(booking)
        data = {"refund_percentage": refund_percentage, "refund_amount": refund_amount}
        return Response(CancellationPreviewSerializer(data).data)


class ReschedulePreviewView(APIView):
    """Read-only: shows the fare difference for a candidate new trip,
    before the user holds seats and confirms rescheduling."""

    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        parameters=[ReschedulePreviewRequestSerializer], responses={200: ReschedulePreviewSerializer}
    )
    def get(self, request, public_id):
        booking = generics.get_object_or_404(Booking, public_id=public_id, user=request.user)
        serializer = ReschedulePreviewRequestSerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        new_trip = serializer.validated_data["new_trip_id"]

        fare_difference_amount, reschedule_fee_amount = services.estimate_reschedule_charge(
            booking, new_trip
        )
        data = {
            "fare_difference_amount": fare_difference_amount,
            "reschedule_fee_amount": reschedule_fee_amount,
        }
        return Response(ReschedulePreviewSerializer(data).data)


class RescheduleBookingView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        request=RescheduleBookingSerializer,
        responses={200: BookingSerializer, 400: ErrorResponseSerializer},
    )
    def post(self, request, public_id):
        booking = generics.get_object_or_404(Booking, public_id=public_id, user=request.user)
        serializer = RescheduleBookingSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        new_trip = serializer.validated_data["new_trip_id"]
        new_hold_token = serializer.validated_data["new_hold_token"]
        try:
            booking = services.reschedule_booking(booking, new_trip.id, new_hold_token)
        except (
            services.BookingStateError,
            services.InvalidPassengerCountError,
            trip_services.InvalidHoldTokenError,
            trip_services.SeatsUnavailableError,
        ) as exc:
            return _service_error_response(exc)
        return Response(BookingSerializer(booking).data)


def _operator_bookings(request):
    return Booking.objects.filter(trip__route__operator=request.user.operator_staff.operator)


def _operator_booking_rows(request):
    """_operator_bookings with everything OperatorBookingSerializer reads
    pre-joined/prefetched (no per-row queries in the list)."""
    return (
        _operator_bookings(request)
        .select_related(
            "trip__route__origin_city",
            "trip__route__destination_city",
            "trip__bus",
            "boarding_stop__stop",
            "drop_stop__stop",
            "booked_by_staff",
        )
        .prefetch_related("passengers__trip_seat__seat", "payments")
    )


@extend_schema(parameters=[OperatorBookingQuerySerializer])
class OperatorBookingListView(generics.ListAPIView):
    """Back office bookings list (owner/manager or counter agent)."""

    serializer_class = OperatorBookingSerializer
    permission_classes = [IsOperatorManagerOrCounterAgent]

    def get_queryset(self):
        query = OperatorBookingQuerySerializer(data=self.request.query_params)
        query.is_valid(raise_exception=True)
        params = query.validated_data

        qs = _operator_booking_rows(self.request).order_by("-created_at")
        if params.get("trip"):
            qs = qs.filter(trip__public_id=params["trip"])
        if params.get("status"):
            qs = qs.filter(status=params["status"])
        if params.get("channel"):
            qs = qs.filter(booking_channel=params["channel"])
        if params.get("search"):
            term = params["search"].strip()
            qs = qs.filter(
                Q(pnr__iexact=term)
                | Q(contact_phone__icontains=term)
                | Q(passengers__full_name__icontains=term)
            ).distinct()
        tz = timezone.get_current_timezone()
        if params.get("date_from"):
            start = timezone.make_aware(datetime.combine(params["date_from"], time.min), tz)
            qs = qs.filter(trip__departure_at__gte=start)
        if params.get("date_to"):
            end = timezone.make_aware(datetime.combine(params["date_to"], time.min), tz) + timedelta(days=1)
            qs = qs.filter(trip__departure_at__lt=end)
        if params.get("pending_counter"):
            qs = qs.filter(
                status=Booking.Status.PENDING_PAYMENT,
                payments__provider=Payment.Provider.COUNTER,
                payments__status=Payment.Status.PENDING,
            ).distinct()
        return qs


class OperatorCancellationPreviewView(APIView):
    permission_classes = [IsOperatorManagerOrCounterAgent]

    @extend_schema(responses={200: CancellationPreviewSerializer})
    def get(self, request, public_id):
        booking = generics.get_object_or_404(_operator_bookings(request), public_id=public_id)
        refund_percentage, refund_amount = services.estimate_cancellation_refund(booking)
        data = {"refund_percentage": refund_percentage, "refund_amount": refund_amount}
        return Response(CancellationPreviewSerializer(data).data)


class OperatorCancelBookingView(APIView):
    """Cancels on the customer's behalf (walk-in / phone request). Same
    service and refund tiers as a passenger self-cancel; the audit log
    records the staff member as the actor."""

    permission_classes = [IsOperatorManagerOrCounterAgent]

    @extend_schema(
        request=CancelBookingSerializer,
        responses={200: OperatorBookingSerializer, 400: ErrorResponseSerializer},
    )
    def post(self, request, public_id):
        booking = generics.get_object_or_404(_operator_bookings(request), public_id=public_id)
        serializer = CancelBookingSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            booking = services.cancel_booking(
                booking, reason=serializer.validated_data.get("reason", ""), actor=request.user
            )
        except services.BookingStateError as exc:
            return _service_error_response(exc)
        booking = _operator_booking_rows(request).get(pk=booking.pk)
        return Response(OperatorBookingSerializer(booking).data)


class CustomerLookupView(APIView):
    """Counter-booking autofill for a returning customer, by phone."""

    permission_classes = [IsOperatorManagerOrCounterAgent]

    @extend_schema(
        parameters=[CustomerLookupQuerySerializer],
        responses={200: CustomerLookupSerializer, 404: ErrorResponseSerializer},
    )
    def get(self, request):
        query = CustomerLookupQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        operator = request.user.operator_staff.operator
        customer = services.find_returning_customer(operator, query.validated_data["phone"])
        if customer is None:
            return Response(
                {"error": {"code": "not_found", "message": "No earlier booking with this phone."}},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(CustomerLookupSerializer(customer).data)
