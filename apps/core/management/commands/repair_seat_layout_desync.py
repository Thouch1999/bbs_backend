"""
One-off repair for the demo dataset's known seat-layout desync (see
CLAUDE.md's Status section and apps/fleet/services.py's BusInUseError
docstring): some trips' TripSeat rows were snapshotted from a seat layout
their bus no longer uses, because generate_trip_seats() only runs once, at
trip-creation time, and nothing used to stop a bus's seat_layout being
changed afterward (fixed going forward by BusInUseError, but the already-bad
rows were deliberately left uncorrected rather than risking destructive
repair against real demo bookings).

This command is non-destructive to bookings: it never deletes or recreates
a TripSeat row that any BookingPassenger points at — including a CANCELLED
booking's, since cancelling only frees the seat (flips TripSeat.status back
to available) and never deletes the historical BookingPassenger row, so a
status of "available" alone does NOT mean a TripSeat is safe to delete (a
first version of this command learned that the hard way: it 500'd on
Django's own ProtectedError, which is exactly what PROTECT is for). It only:
  - remaps every TripSeat a BookingPassenger references (held, booked, or
    a cancelled booking's now-available seat) to the equivalent seat_number
    in the bus's CURRENT layout — invisible to those bookings, since
    BookingPassenger.trip_seat is a FK to the TripSeat row, not to Seat —
    and
  - deletes and regenerates only the TripSeat rows nothing references at
    all, to match the current layout exactly.

A referenced seat with no same-numbered seat in the current layout can't
be auto-remapped (the two layouts have a different shape) and is left
untouched and reported, for manual handling.

Defaults to a dry run — nothing is written unless --apply is passed. Take
a real backup first (scripts/backup_db.sh), the same as every other
data-mutating operation in this project's history.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.trips.models import Trip, TripSeat


class Command(BaseCommand):
    help = "Repair TripSeat rows desynced from their bus's current seat layout (dry run unless --apply)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply", action="store_true", help="Actually write the repair. Without this, only reports."
        )
        parser.add_argument(
            "--trip", dest="trip_public_id", default=None, help="Limit to a single trip's public_id."
        )

    def handle(self, *args, **options):
        apply = options["apply"]
        trip_public_id = options["trip_public_id"]

        candidate_trips = Trip.objects.select_related("bus").filter(trip_seats__isnull=False).distinct()
        trips = [
            t
            for t in candidate_trips.iterator()
            if t.trip_seats.exclude(seat__seat_layout_id=t.bus.seat_layout_id).exists()
        ]

        if trip_public_id:
            trips = [t for t in trips if str(t.public_id) == trip_public_id]

        if not trips:
            self.stdout.write(self.style.SUCCESS("No desynced trips found."))
            return

        self.stdout.write(f"{len(trips)} desynced trip(s) found. apply={apply}")

        total_remapped = 0
        total_regenerated = 0
        total_unresolved = 0

        for trip in trips:
            result = self._repair_trip(trip, apply=apply)
            total_remapped += result["remapped"]
            total_regenerated += result["regenerated"]
            total_unresolved += len(result["unresolved"])
            unresolved_note = (
                f", {len(result['unresolved'])} UNRESOLVED (seat_number(s) "
                f"{sorted(s for s in result['unresolved'])}) — needs manual reassignment"
                if result["unresolved"]
                else ""
            )
            self.stdout.write(
                f"  trip {trip.public_id}: remapped {result['remapped']} booking-referenced seat(s), "
                f"regenerated {result['regenerated']} available seat(s){unresolved_note}"
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Total: {total_remapped} remapped, {total_regenerated} regenerated, "
                f"{total_unresolved} unresolved across {len(trips)} trip(s)."
            )
        )
        if not apply:
            self.stdout.write(self.style.WARNING("Dry run — no changes were written. Re-run with --apply."))

    def _repair_trip(self, trip, *, apply) -> dict:
        from apps.bookings.models import BookingPassenger

        new_layout = trip.bus.seat_layout
        new_seats_by_number = {s.seat_number: s for s in new_layout.seats.all()}

        trip_seats = list(trip.trip_seats.select_related("seat").all())
        referenced_ids = set(
            BookingPassenger.objects.filter(trip_seat__trip=trip).values_list("trip_seat_id", flat=True)
        )
        referenced = [ts for ts in trip_seats if ts.id in referenced_ids]
        unreferenced = [ts for ts in trip_seats if ts.id not in referenced_ids]

        remapped = []
        unresolved = []
        for ts in referenced:
            if ts.seat.seat_layout_id == new_layout.id:
                continue  # already correct
            new_seat = new_seats_by_number.get(ts.seat.seat_number)
            if new_seat is None:
                unresolved.append(ts.seat.seat_number)
            else:
                remapped.append((ts, new_seat))

        remapped_seat_ids = {new_seat.id for _, new_seat in remapped}
        already_correct_referenced_seat_ids = {
            ts.seat_id for ts in referenced if ts.seat.seat_layout_id == new_layout.id
        }
        # Unreferenced TripSeats already on the current layout — from an
        # earlier run of this same command, or genuinely never desynced —
        # must be left alone and excluded from "needs generating", or a
        # second run (unavoidable while any trip has an unresolved seat,
        # since that keeps it in the candidate list forever) would try to
        # bulk_create a duplicate row and hit the unique_trip_seat constraint.
        already_correct_unreferenced_seat_ids = {
            ts.seat_id for ts in unreferenced if ts.seat.seat_layout_id == new_layout.id
        }
        used_seat_ids = (
            remapped_seat_ids | already_correct_referenced_seat_ids | already_correct_unreferenced_seat_ids
        )
        fresh_available_seats = [s for s in new_seats_by_number.values() if s.id not in used_seat_ids]

        stale_unreferenced_ids = [ts.id for ts in unreferenced if ts.seat.seat_layout_id != new_layout.id]

        if apply:
            with transaction.atomic():
                for ts, new_seat in remapped:
                    ts.seat = new_seat
                    ts.save(update_fields=["seat"])
                TripSeat.objects.filter(id__in=stale_unreferenced_ids).delete()
                TripSeat.objects.bulk_create(
                    [
                        TripSeat(trip=trip, seat=seat, status=TripSeat.Status.AVAILABLE)
                        for seat in fresh_available_seats
                    ]
                )
                trip.seats_available = TripSeat.objects.filter(
                    trip=trip, status=TripSeat.Status.AVAILABLE
                ).count()
                trip.save(update_fields=["seats_available", "updated_at"])

        return {
            "remapped": len(remapped),
            "regenerated": len(fresh_available_seats),
            "unresolved": unresolved,
        }
