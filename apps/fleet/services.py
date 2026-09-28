from django.db import transaction

from .models import Bus, Seat, SeatLayout

# Keys the passenger site knows how to render (TripCard's icon map +
# booking.json's amenities.* labels). Anything else would show up as a raw
# key on the trip card.
ALLOWED_AMENITIES = ("ac", "wifi", "usb", "toilet", "blanket", "water")
MAX_ROWS = 20
MAX_COLUMNS = 10


class SeatLayoutError(Exception):
    code = "seat_layout_error"


class SeatLayoutInUseError(SeatLayoutError):
    """Trips were already generated from this layout's seats; rewriting them
    would orphan (PROTECT-referenced) TripSeats and every booking on them."""

    code = "seat_layout_in_use"


def layout_in_use(seat_layout: SeatLayout) -> bool:
    from apps.trips.models import TripSeat

    return TripSeat.objects.filter(seat__seat_layout=seat_layout).exists()


def _ensure_editable(seat_layout: SeatLayout) -> None:
    if layout_in_use(seat_layout):
        raise SeatLayoutInUseError(
            "Trips already use this seat layout, so its seats can't change. Save it as a new layout instead."
        )


class BusInUseError(SeatLayoutError):
    """
    A bus's seat_layout is read at trip-creation time to generate that
    trip's TripSeats (apps.trips.services.generate_trip_seats) — those
    TripSeats keep pointing at the *old* layout's Seat rows forever after,
    since nothing re-generates them if the bus is later reassigned. Found
    live in this project's own demo data: a bus reassigned to a new
    layout left every one of its already-scheduled trips with TripSeats
    whose seat_layout_id no longer matched trip.bus.seat_layout_id, so
    every hold on those seats 400'd with "Unknown seats" (HoldSeatsView
    scopes the lookup to trip.bus.seat_layout_id). Never allow this again.
    """

    code = "bus_in_use"


def bus_has_active_trips(bus: Bus) -> bool:
    from apps.trips.services import BOOKABLE_STATUSES

    return bus.trips.filter(status__in=BOOKABLE_STATUSES).exists()


def update_bus(bus: Bus, **changes) -> Bus:
    """Business logic for editing a bus (CLAUDE.md: never in views/
    serializers). Changing seat_layout is only safe while the bus has no
    trip still relying on its current layout — see BusInUseError."""
    new_layout = changes.get("seat_layout")
    if new_layout is not None and new_layout.id != bus.seat_layout_id and bus_has_active_trips(bus):
        raise BusInUseError(
            "This bus is scheduled on trips using its current seat layout — reassign or "
            "cancel those trips first, or use a different bus for new schedules."
        )
    for field, value in changes.items():
        setattr(bus, field, value)
    bus.save()
    return bus


@transaction.atomic
def build_seat_layout(seat_layout: SeatLayout, decks: int, rows: int, columns: list) -> list[Seat]:
    """
    Replaces every seat on this layout with a fresh grid. `columns` gives the
    left-to-right slots for one row — a column letter, or None for an aisle
    gap — repeated for `rows` rows across `decks` decks. Deck 2 seat numbers
    are prefixed "U" (upper) so they never collide with deck 1's.
    """
    _ensure_editable(seat_layout)
    seat_layout.seats.all().delete()
    seat_layout.deck_count = decks
    seat_layout.save(update_fields=["deck_count", "updated_at"])

    seats = []
    for deck in range(1, decks + 1):
        deck_prefix = "U" if deck == 2 else ""
        for row in range(1, rows + 1):
            for col_index, letter in enumerate(columns, start=1):
                if letter is None:
                    continue
                seats.append(
                    Seat(
                        seat_layout=seat_layout,
                        seat_number=f"{deck_prefix}{letter}{row}",
                        deck=deck,
                        row_position=row,
                        col_position=col_index,
                    )
                )
    Seat.objects.bulk_create(seats)
    # bulk_create doesn't populate .id on real MySQL (only MariaDB/Postgres
    # support RETURNING there), and SeatLayoutViewSet.build serializes this
    # return value directly — a refetch keeps every Seat's id usable
    # regardless of backend.
    return list(seat_layout.seats.order_by("id"))


@transaction.atomic
def replace_layout_seats(seat_layout: SeatLayout, *, deck_count: int, seats: list[dict]) -> list[Seat]:
    """
    Saves the visual layout builder's output verbatim: an explicit list of
    seats with their own grid positions, types and female-only flags —
    exactly the fields the passenger SeatMap renders from (row_position →
    grid row, col_position → grid column, empty columns = aisles), so what
    the operator draws is what passengers see.
    """
    _ensure_editable(seat_layout)
    if not seats:
        raise SeatLayoutError("A seat layout needs at least one seat.")

    numbers, positions = set(), set()
    for seat in seats:
        number = seat["seat_number"].strip().upper()
        position = (seat["deck"], seat["row_position"], seat["col_position"])
        if not number:
            raise SeatLayoutError("Every seat needs a seat number.")
        if number in numbers:
            raise SeatLayoutError(f"Seat number {number} is used twice.")
        if position in positions:
            deck, row, col = position
            raise SeatLayoutError(f"Two seats share deck {deck}, row {row}, column {col}.")
        if seat["deck"] > deck_count:
            raise SeatLayoutError(
                f"Seat {number} is on deck {seat['deck']}, but the layout has {deck_count}."
            )
        numbers.add(number)
        positions.add(position)
        seat["seat_number"] = number

    seat_layout.seats.all().delete()
    seat_layout.deck_count = deck_count
    seat_layout.save(update_fields=["deck_count", "updated_at"])
    Seat.objects.bulk_create(
        [
            Seat(
                seat_layout=seat_layout,
                seat_number=seat["seat_number"],
                deck=seat["deck"],
                row_position=seat["row_position"],
                col_position=seat["col_position"],
                seat_type=seat.get("seat_type", Seat.SeatType.NORMAL),
                is_female_only=seat.get("is_female_only", False),
            )
            for seat in sorted(seats, key=lambda s: (s["deck"], s["row_position"], s["col_position"]))
        ]
    )
    # bulk_create doesn't populate .id on real MySQL (only MariaDB/Postgres
    # support RETURNING there) — refetch so this function's declared
    # `list[Seat]` return is actually usable by any caller, not just the
    # one that happens to refetch on its own.
    return list(seat_layout.seats.order_by("id"))
