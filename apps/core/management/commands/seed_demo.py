"""
Realistic demo dataset for clicking through the API/admin before the
frontend exists. Idempotent: every entity with a natural stable identity
(operators, seat layouts, buses, routes, demo passengers, promo codes) is
looked up with get_or_create/update_or_create, and trips are keyed on
(route, bus, departure_at) so a same-day re-run never creates duplicates —
only bookings are randomized, and that step is skipped per-trip once a trip
already has demo bookings.
"""

import random
import uuid
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import OperatorStaff, SavedPassenger, User
from apps.bookings import services as booking_services
from apps.bookings.models import Booking
from apps.content.models import Announcement, Banner
from apps.fleet.models import Bus, Seat, SeatLayout
from apps.operators.models import Operator
from apps.payments import services as payment_services
from apps.payments.models import Payment, PromoCode
from apps.routes.models import City, Route, RouteStop, Stop
from apps.trips import services as trip_services
from apps.trips.models import Trip, TripSeat

OPERATORS = [
    ("Demo Operator — Golden Bus", "ក្រុមហ៊ុនមាស", "+85511000001"),
]

# Domestic only, per docs/DECISIONS.md #3 (cross-border not in v1).
ROUTE_PAIRS = [
    ("Phnom Penh", "Siem Reap"),
    ("Siem Reap", "Phnom Penh"),
    ("Phnom Penh", "Sihanoukville"),
    ("Sihanoukville", "Phnom Penh"),
    ("Phnom Penh", "Battambang"),
    ("Battambang", "Siem Reap"),
    ("Phnom Penh", "Kampot"),
    ("Kampot", "Kep"),
]

# Back-office logins (Step 15) so the operator dashboard can be clicked
# through. Password is only set when the account is first created.
DEMO_STAFF_PASSWORD = "Demo12345!"
DEMO_OWNER_EMAILS = ["owner1@bbms.test"]
DEMO_COUNTER_AGENT_EMAIL = "counter1@bbms.test"
DEMO_CONDUCTOR_EMAIL = "conductor1@bbms.test"
DEMO_ADMIN_EMAIL = "admin@bbms.test"

# A couple of real terminals per city, so route stop lists aren't empty.
STOPS = {
    "Phnom Penh": [("Central Market Terminal", "ចំណតផ្សារធំថ្មី"), ("Olympic Stadium", "ពហុកីឡដ្ឋានអូឡាំពិក")],
    "Siem Reap": [("Siem Reap Bus Station", "ចំណតឡានក្រុងសៀមរាប"), ("Old Market", "ផ្សារចាស់")],
    "Sihanoukville": [("Sihanoukville Terminal", "ចំណតក្រុងព្រះសីហនុ")],
    "Battambang": [("Battambang Terminal", "ចំណតបាត់ដំបង")],
    "Kampot": [("Kampot Terminal", "ចំណតកំពត")],
    "Kep": [("Kep Crab Market", "ផ្សារក្តាមកែប")],
    "Kampong Thom": [("Kampong Thom Rest Stop", "ចំណតសម្រាកកំពង់ធំ")],
}

DEPARTURE_HOURS = [7, 13, 19]
NUM_TRIPS = 60
NUM_TRIP_DAYS = 14
NUM_PASSENGERS = 10
PROMO_CODES = [
    {
        "code": "DEMO10",
        "discount_type": PromoCode.DiscountType.PERCENT,
        "value": Decimal(10),
        "description_km": "បញ្ចុះតម្លៃ ១០% លើសំបុត្រទាំងអស់",
        "description_en": "10% off any ticket",
    },
    {
        "code": "DEMOFIVE",
        "discount_type": PromoCode.DiscountType.FIXED,
        "value": Decimal("5.00"),
        "description_km": "បញ្ចុះតម្លៃ $៥ ភ្លាមៗ",
        "description_en": "$5 off instantly",
    },
]

BANNERS = [
    {
        "title_km": "កម្ពុជាថ្ងៃឈប់សម្រាក — បញ្ចុះតម្លៃ ១០%",
        "title_en": "Cambodia Holiday Sale — 10% Off",
        "body_km": "កក់ដំណើរទៅសៀមរាប ឬព្រះសីហនុ ហើយសន្សំបានភ្លាមៗ។",
        "body_en": "Book a trip to Siem Reap or Sihanoukville and save instantly.",
        "placement": Banner.Placement.HOME,
        "background_color": "#0D5F6E",
        "link_url": "/search",
        "sort_order": 1,
    },
    {
        "title_km": "ទាញយកកម្មវិធីទូរស័ព្ទ BBMS",
        "title_en": "Get the BBMS Mobile App",
        "body_km": "កក់សំបុត្រលឿនជាងមុន ជាមួយកម្មវិធីទូរស័ព្ទរបស់យើង។",
        "body_en": "Book even faster with our mobile app.",
        "placement": Banner.Placement.SEARCH,
        "background_color": "#F58220",
        "link_url": "",
        "sort_order": 1,
    },
    {
        "title_km": "សូមអរគុណសម្រាប់ការកក់!",
        "title_en": "Thanks for booking with us!",
        "body_km": "កុំភ្លេចរក្សាទុកសំបុត្រអេឡិចត្រូនិចរបស់អ្នក។",
        "body_en": "Don't forget to save your e-ticket.",
        "placement": Banner.Placement.MY_BOOKINGS,
        "background_color": "#16A34A",
        "link_url": "",
        "sort_order": 1,
    },
]

ANNOUNCEMENTS = [
    {
        "message_km": "ការថែទាំប្រព័ន្ធនឹងធ្វើឡើងនៅថ្ងៃអាទិត្យ ម៉ោង ២ ទៀបភ្លឺ។ ការកក់អាចរងផលប៉ះពាល់មួយរយៈខ្លី។",
        "message_en": "Scheduled maintenance this Sunday at 2 AM. Bookings may be briefly affected.",
        "severity": Announcement.Severity.WARNING,
        "audience": Announcement.Audience.ALL,
    },
    {
        "message_km": "ក្រុមហ៊ុនប្រតិបត្តិការថ្មីអាចដាក់ពាក្យស្នើសុំចូលរួមជាមួយវេទិកា BBMS ឥឡូវនេះ។",
        "message_en": "New bus operators can now apply to join the BBMS marketplace.",
        "severity": Announcement.Severity.INFO,
        "audience": Announcement.Audience.OPERATORS,
    },
]


class Command(BaseCommand):
    help = "Seeds a realistic, idempotent demo dataset: operators, fleet, routes, trips, bookings, promos."

    def handle(self, *args, **options):
        call_command("seed_geography")
        rng = random.Random(42)  # fixed seed: re-running produces the same booking mix

        with transaction.atomic():
            operators = self._seed_operators()
            self._seed_operator_staff(operators)
            layouts = {op.id: self._seed_seat_layouts(op) for op in operators}
            buses = self._seed_buses(operators, layouts)
            routes = self._seed_routes(operators)
            self._seed_route_stops(routes)
            trips = self._seed_trips(routes, buses)
            passengers = self._seed_passengers()
            self._seed_promo_codes()
            self._seed_content()
            new_bookings = self._seed_bookings(trips, passengers, rng)

        self.stdout.write(
            self.style.SUCCESS(
                f"seed_demo: {len(operators)} operators, {len(buses)} buses, {len(routes)} routes, "
                f"{len(trips)} trips, {len(passengers)} passengers, {new_bookings} bookings created "
                "(0 new bookings on a same-day re-run is expected, not a bug)."
            )
        )

    def _seed_operators(self):
        operators = []
        for name, name_km, phone in OPERATORS:
            slug = name.split("—")[0].strip().lower().replace(" ", "-")
            operator, _ = Operator.objects.update_or_create(
                name=name,
                defaults={
                    "name_km": name_km,
                    "contact_phone": phone,
                    "contact_email": f"{slug}@demo.bbms.test",
                    "status": Operator.Status.APPROVED,
                    "commission_rate": Decimal("10.00"),
                    "approved_at": timezone.now(),
                },
            )
            operators.append(operator)
        return operators

    def _seed_operator_staff(self, operators):
        def _account(email, full_name, operator, staff_role, **perms):
            user, created = User.objects.get_or_create(
                email=email,
                defaults={"full_name": full_name, "role": User.Role.OPERATOR_STAFF},
            )
            if created:
                user.set_password(DEMO_STAFF_PASSWORD)
                user.save(update_fields=["password"])
            OperatorStaff.objects.get_or_create(
                user=user, defaults={"operator": operator, "staff_role": staff_role, **perms}
            )

        for operator, email in zip(operators, DEMO_OWNER_EMAILS, strict=True):
            _account(email, f"Owner — {operator.name_km}", operator, OperatorStaff.StaffRole.OWNER,
                     can_create_bookings=True)
        _account(DEMO_COUNTER_AGENT_EMAIL, "Demo Counter Agent", operators[0],
                 OperatorStaff.StaffRole.COUNTER_AGENT, can_create_bookings=True)
        _account(DEMO_CONDUCTOR_EMAIL, "Demo Conductor", operators[0],
                 OperatorStaff.StaffRole.CONDUCTOR, can_validate_tickets=True)

        # Platform admin login for the Step 16 admin panel (password set once).
        admin, created = User.objects.get_or_create(
            email=DEMO_ADMIN_EMAIL,
            defaults={"full_name": "Demo Admin", "role": User.Role.ADMIN, "is_staff": True},
        )
        if created:
            admin.set_password(DEMO_STAFF_PASSWORD)
            admin.save(update_fields=["password"])

    def _seed_route_stops(self, routes):
        cities = {c.name_en: c for c in City.objects.filter(name_en__in=STOPS.keys())}
        stops_by_city = {}
        for city_name, names in STOPS.items():
            if city_name not in cities:
                continue
            stops_by_city[city_name] = [
                Stop.objects.get_or_create(
                    city=cities[city_name], name_en=name_en, defaults={"name_km": name_km}
                )[0]
                for name_en, name_km in names
            ]
        for route in routes:
            if route.route_stops.exists():
                continue
            origin_stops = stops_by_city.get(route.origin_city.name_en, [])
            destination_stops = stops_by_city.get(route.destination_city.name_en, [])
            duration = route.estimated_duration_minutes or 300
            ordered = [(stop, i * 20) for i, stop in enumerate(origin_stops)]
            last = len(destination_stops) - 1
            ordered += [(stop, duration - 20 * (last - i)) for i, stop in enumerate(destination_stops)]
            for sequence, (stop, offset) in enumerate(ordered, start=1):
                RouteStop.objects.create(route=route, stop=stop, sequence=sequence, offset_minutes=offset)

    def _seed_seat_layouts(self, owner: Operator):
        specs = [
            ("Demo Standard 45", SeatLayout.BusType.SEATED, Seat.SeatType.NORMAL, 9, 5),
            ("Demo VIP 40", SeatLayout.BusType.SEATED, Seat.SeatType.VIP, 10, 4),
        ]
        layouts = []
        for name, bus_type, seat_type, rows, cols in specs:
            layout, _ = SeatLayout.objects.update_or_create(
                operator=owner, name=name, defaults={"bus_type": bus_type, "deck_count": 1}
            )
            if not layout.seats.exists():
                Seat.objects.bulk_create(
                    [
                        Seat(
                            seat_layout=layout,
                            seat_number=f"{chr(65 + row)}{col + 1}",
                            deck=1,
                            row_position=row + 1,
                            col_position=col + 1,
                            seat_type=seat_type,
                        )
                        for row in range(rows)
                        for col in range(cols)
                    ]
                )
            layouts.append(layout)
        return layouts

    def _seed_buses(self, operators, layouts):
        buses = []
        for i in range(6):
            operator = operators[i % len(operators)]
            # Each operator's buses use that operator's own layouts — a bus on
            # another operator's layout can't even be edited (BusSerializer
            # rejects a cross-operator seat_layout_id).
            operator_layouts = layouts[operator.id]
            layout = operator_layouts[(i // len(operators)) % len(operator_layouts)]
            # get_or_create, not update_or_create: a bus already has trips
            # generated from whatever layout it had when created (trip
            # creation snapshots seat_layout.seats into that trip's
            # TripSeats — see generate_trip_seats), so silently reassigning
            # seat_layout on a reseed would desync existing trips from the
            # bus exactly like the real BusInUseError bug this project hit
            # (apps/fleet/services.py) — found via this dev database's own
            # demo data after several reseeds left 37 trips' seats pointing
            # at a different layout than their bus's current one.
            bus, _ = Bus.objects.get_or_create(
                operator=operator,
                plate_number=f"DEMO-{i + 1:03d}",
                defaults={
                    "seat_layout": layout,
                    "amenities": ["ac", "wifi", "usb"],
                    "status": Bus.Status.ACTIVE,
                },
            )
            buses.append(bus)
        return buses

    def _seed_routes(self, operators):
        cities = {c.name_en: c for c in City.objects.filter(country_code="KH")}
        routes = []
        for i, (origin_name, dest_name) in enumerate(ROUTE_PAIRS):
            operator = operators[i % len(operators)]
            route, _ = Route.objects.update_or_create(
                operator=operator,
                origin_city=cities[origin_name],
                destination_city=cities[dest_name],
                defaults={
                    "name": f"{origin_name} - {dest_name}",
                    "distance_km": Decimal("180.0"),
                    "estimated_duration_minutes": 300,
                    "is_active": True,
                },
            )
            routes.append(route)
        return routes

    def _seed_trips(self, routes, buses):
        today = timezone.localdate()
        tz = timezone.get_current_timezone()
        trips = []
        count = 0
        for day_offset in range(NUM_TRIP_DAYS):
            for route_index, route in enumerate(routes):
                if count >= NUM_TRIPS:
                    break
                # _seed_routes cycles operators as `i % len(operators)`, so one
                # operator's own routes always sit exactly len(operators) apart
                # in route_index (e.g. 0, 3, 6). Keying the hour by route_index
                # directly made every one of an operator's routes land on the
                # identical hour every day (same residue mod len(DEPARTURE_HOURS)
                # == len(operators) == 3) — and combined with a bus keyed the
                # same way, that manufactured a same-bus/same-time conflict on
                # nearly every trip, only visible once the schedule page's
                # redesign started highlighting conflict rows. Keying off each
                # route's position *within* its own operator decorrelates the
                # two: real fleet conflicts can still happen, just aren't
                # guaranteed on almost every row.
                route_position = route_index // len(OPERATORS)
                hour = DEPARTURE_HOURS[(day_offset + route_position) % len(DEPARTURE_HOURS)]
                departure_at = timezone.make_aware(
                    datetime.combine(today + timedelta(days=day_offset), time(hour=hour)), tz
                )
                operator_buses = [b for b in buses if b.operator_id == route.operator_id] or buses
                bus = operator_buses[route_position % len(operator_buses)]

                trip, created = Trip.objects.get_or_create(
                    route=route,
                    bus=bus,
                    departure_at=departure_at,
                    defaults={
                        "arrival_at": departure_at
                        + timedelta(minutes=route.estimated_duration_minutes or 300),
                        "base_fare_usd": Decimal("8.00"),
                        "base_fare_khr": Decimal(32800),
                        "status": Trip.Status.SCHEDULED,
                    },
                )
                if created:
                    trip_services.generate_trip_seats(trip)
                trips.append(trip)
                count += 1
            if count >= NUM_TRIPS:
                break
        return trips

    def _seed_passengers(self):
        passengers = []
        for i in range(NUM_PASSENGERS):
            email = f"passenger{i + 1}@bbms.test"
            user, created = User.objects.get_or_create(
                email=email,
                defaults={
                    "full_name": f"Demo Passenger {i + 1}",
                    "phone": f"+855100{i:05d}",
                    "role": User.Role.PASSENGER,
                    "preferred_language": User.Language.KM if i % 2 == 0 else User.Language.EN,
                },
            )
            if created:
                # Booking now requires a logged-in passenger account (ad hoc,
                # per direct request), so these demo accounts need a real,
                # usable password to actually demo with — not an unusable
                # one, which was fine back when guest checkout existed.
                user.set_password(DEMO_STAFF_PASSWORD)
                user.save(update_fields=["password"])
                if i < 5:
                    SavedPassenger.objects.get_or_create(
                        user=user, full_name=user.full_name, defaults={"age": 30, "gender": "other"}
                    )
            passengers.append(user)
        return passengers

    def _seed_promo_codes(self):
        now = timezone.now()
        for spec in PROMO_CODES:
            PromoCode.objects.update_or_create(
                code=spec["code"],
                defaults={
                    "discount_type": spec["discount_type"],
                    "value": spec["value"],
                    "description_km": spec["description_km"],
                    "description_en": spec["description_en"],
                    "valid_from": now - timedelta(days=1),
                    "valid_until": now + timedelta(days=90),
                    "is_active": True,
                },
            )

    def _seed_content(self):
        for spec in BANNERS:
            defaults = {k: v for k, v in spec.items() if k not in {"title_en", "placement"}}
            Banner.objects.update_or_create(
                title_en=spec["title_en"],
                placement=spec["placement"],
                defaults={**defaults, "is_active": True},
            )
        for spec in ANNOUNCEMENTS:
            defaults = {k: v for k, v in spec.items() if k != "message_en"}
            Announcement.objects.update_or_create(
                message_en=spec["message_en"],
                defaults={**defaults, "is_active": True},
            )

    def _seed_bookings(self, trips, passengers, rng: random.Random) -> int:
        created_count = 0
        for trip in trips:
            already_seeded = Booking.objects.filter(
                trip=trip, contact_phone__startswith="+855100"
            ).exists()
            if already_seeded:
                continue

            available_seats = list(
                trip.trip_seats.filter(status=TripSeat.Status.AVAILABLE).select_related("seat")
            )
            rng.shuffle(available_seats)
            num_bookings = rng.randint(1, 4)
            seat_cursor = 0

            for _ in range(num_bookings):
                seats_for_booking = rng.randint(1, 2)
                if seat_cursor + seats_for_booking > len(available_seats):
                    break
                trip_seats = available_seats[seat_cursor : seat_cursor + seats_for_booking]
                seat_cursor += seats_for_booking

                passenger_user = rng.choice(passengers)
                session_key = f"seed-{uuid.uuid4().hex[:12]}"
                token = trip_services.hold_seats(
                    trip.id, [ts.seat_id for ts in trip_seats], session_key=session_key
                )
                booking = booking_services.create_booking(
                    trip.id,
                    token,
                    [{"full_name": passenger_user.full_name, "age": rng.randint(18, 65)} for _ in trip_seats],
                    contact_phone=passenger_user.phone,
                    contact_email=f"{passenger_user.phone.lstrip('+')}@demo.bbms.test",
                    user=passenger_user,
                    booking_channel=Booking.Channel.WEB if rng.random() < 0.8 else Booking.Channel.COUNTER,
                )
                created_count += 1

                # ~55% stay confirmed+paid, ~20% get confirmed then cancelled+refunded
                # (a subset of that same 75%), ~25% stay pending_payment (never paid).
                outcome = rng.random()
                if outcome < 0.75:
                    booking_services.confirm_booking(booking)
                    payment = Payment.objects.create(
                        booking=booking,
                        provider=Payment.Provider.MOCK,
                        status=Payment.Status.SUCCEEDED,
                        amount=booking.total_amount,
                        currency=booking.currency,
                    )
                    if outcome < 0.20:
                        booking_services.cancel_booking(booking, reason="Demo: change of plans")
                        if booking.refund_amount and booking.refund_amount > 0:
                            payment_services.create_refund(
                                payment, booking.refund_amount, reason="Demo cancellation"
                            )
        return created_count
