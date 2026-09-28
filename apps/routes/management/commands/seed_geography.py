from django.core.management.base import BaseCommand

from apps.routes.models import City

CITIES = [
    # Cambodia — all 25 first-level administrative divisions (24 provinces + Phnom Penh capital)
    ("Phnom Penh", "ភ្នំពេញ", "KH"),
    ("Banteay Meanchey", "បន្ទាយមានជ័យ", "KH"),
    ("Battambang", "បាត់ដំបង", "KH"),
    ("Kampong Cham", "កំពង់ចាម", "KH"),
    ("Kampong Chhnang", "កំពង់ឆ្នាំង", "KH"),
    ("Kampong Speu", "កំពង់ស្ពឺ", "KH"),
    ("Kampong Thom", "កំពង់ធំ", "KH"),
    ("Kampot", "កំពត", "KH"),
    ("Kandal", "កណ្ដាល", "KH"),
    ("Kep", "កែប", "KH"),
    ("Koh Kong", "កោះកុង", "KH"),
    ("Kratie", "ក្រចេះ", "KH"),
    ("Mondulkiri", "មណ្ឌលគិរី", "KH"),
    ("Oddar Meanchey", "ឧត្តរមានជ័យ", "KH"),
    ("Pailin", "ប៉ៃលិន", "KH"),
    ("Sihanoukville", "ព្រះសីហនុ", "KH"),
    ("Preah Vihear", "ព្រះវិហារ", "KH"),
    ("Prey Veng", "ព្រៃវែង", "KH"),
    ("Pursat", "ពោធិ៍សាត់", "KH"),
    ("Ratanakiri", "រតនគិរី", "KH"),
    ("Siem Reap", "សៀមរាប", "KH"),
    ("Stung Treng", "ស្ទឹងត្រែង", "KH"),
    ("Svay Rieng", "ស្វាយរៀង", "KH"),
    ("Takeo", "តាកែវ", "KH"),
    ("Tboung Khmum", "ត្បូងឃ្មុំ", "KH"),
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
