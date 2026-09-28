from django.core.management.base import BaseCommand

from apps.routes.models import City

CITIES = [
    # Cambodia
    ("Phnom Penh", "ភ្នំពេញ", "KH"),
    ("Siem Reap", "សៀមរាប", "KH"),
    ("Sihanoukville", "ព្រះសីហនុ", "KH"),
    ("Battambang", "បាត់ដំបង", "KH"),
    ("Kampot", "កំពត", "KH"),
    ("Kep", "កែប", "KH"),
    ("Kampong Cham", "កំពង់ចាម", "KH"),
    ("Poipet", "ប៉ោយប៉ែត", "KH"),
    # Cross-border (SRS 14: not in v1 routes, but seeded so the data model is ready)
    ("Bangkok", "បាងកក", "TH"),
    ("Ho Chi Minh City", "ហូជីមិញ", "VN"),
]


class Command(BaseCommand):
    help = "Seeds real Cambodian cities/provinces (plus Bangkok and Ho Chi Minh City) with Khmer names."

    def handle(self, *args, **options):
        created, updated = 0, 0
        for name_en, name_km, country_code in CITIES:
            _city, was_created = City.objects.update_or_create(
                name_en=name_en,
                defaults={"name_km": name_km, "country_code": country_code, "is_active": True},
            )
            created += was_created
            updated += not was_created

        self.stdout.write(self.style.SUCCESS(f"seed_geography: {created} created, {updated} updated."))
