from django.db import models

from apps.core.models import PublicIdModel, TimeStampedModel


class City(PublicIdModel, TimeStampedModel):
    name_en = models.CharField(max_length=100)
    name_km = models.CharField(max_length=100)
    country_code = models.CharField(max_length=2, default="KH", help_text="ISO 3166-1 alpha-2")
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "routes_city"
        indexes = [models.Index(fields=["name_en"]), models.Index(fields=["name_km"])]

    def __str__(self):
        return self.name_en


class Stop(PublicIdModel, TimeStampedModel):
    city = models.ForeignKey(City, on_delete=models.PROTECT, related_name="stops")
    name_en = models.CharField(max_length=150)
    name_km = models.CharField(max_length=150)
    address = models.CharField(max_length=255, blank=True)
    lat = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    lng = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "routes_stop"
        indexes = [models.Index(fields=["city"])]
        ordering = ["name_en"]

    def __str__(self):
        return self.name_en


class Route(PublicIdModel, TimeStampedModel):
    operator = models.ForeignKey("operators.Operator", on_delete=models.CASCADE, related_name="routes")
    origin_city = models.ForeignKey(City, on_delete=models.PROTECT, related_name="routes_originating")
    destination_city = models.ForeignKey(City, on_delete=models.PROTECT, related_name="routes_terminating")
    name = models.CharField(max_length=150, blank=True)
    distance_km = models.DecimalField(max_digits=6, decimal_places=1, null=True, blank=True)
    estimated_duration_minutes = models.PositiveIntegerField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "routes_route"
        indexes = [
            models.Index(fields=["operator"]),
            models.Index(fields=["origin_city", "destination_city"]),
        ]
        ordering = ["-created_at"]

    def __str__(self):
        return self.name or f"{self.origin_city.name_en} → {self.destination_city.name_en}"


class RouteStop(PublicIdModel, TimeStampedModel):
    route = models.ForeignKey(Route, on_delete=models.CASCADE, related_name="route_stops")
    stop = models.ForeignKey(Stop, on_delete=models.PROTECT, related_name="route_stops")
    sequence = models.PositiveSmallIntegerField(help_text="1-based order along the route, from origin.")
    offset_minutes = models.PositiveIntegerField(help_text="Minutes from departure at sequence 1.")

    class Meta:
        db_table = "routes_route_stop"
        constraints = [
            models.UniqueConstraint(fields=["route", "sequence"], name="unique_route_sequence"),
        ]
        ordering = ["sequence"]

    def __str__(self):
        return f"{self.route_id} #{self.sequence}: {self.stop.name_en}"
