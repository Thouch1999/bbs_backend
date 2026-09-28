from django.db import models

from apps.core.models import PublicIdModel, TimeStampedModel


class SeatLayout(PublicIdModel, TimeStampedModel):
    class BusType(models.TextChoices):
        SEATED = "seated", "Seated"
        SLEEPER = "sleeper", "Sleeper"
        MIXED = "mixed", "Mixed"

    operator = models.ForeignKey("operators.Operator", on_delete=models.CASCADE, related_name="seat_layouts")
    name = models.CharField(max_length=100)
    bus_type = models.CharField(max_length=10, choices=BusType.choices, default=BusType.SEATED)
    deck_count = models.PositiveSmallIntegerField(default=1)

    class Meta:
        db_table = "fleet_seat_layout"
        indexes = [models.Index(fields=["operator"])]

    def __str__(self):
        return f"{self.name} ({self.get_bus_type_display()})"


class Seat(PublicIdModel, TimeStampedModel):
    class SeatType(models.TextChoices):
        NORMAL = "normal", "Normal"
        VIP = "vip", "VIP"

    seat_layout = models.ForeignKey(SeatLayout, on_delete=models.CASCADE, related_name="seats")
    seat_number = models.CharField(max_length=10, help_text='e.g. "A1"')
    deck = models.PositiveSmallIntegerField(default=1)
    row_position = models.PositiveSmallIntegerField()
    col_position = models.PositiveSmallIntegerField()
    seat_type = models.CharField(max_length=10, choices=SeatType.choices, default=SeatType.NORMAL)
    is_female_only = models.BooleanField(default=False)

    class Meta:
        db_table = "fleet_seat"
        constraints = [
            models.UniqueConstraint(fields=["seat_layout", "seat_number"], name="unique_layout_seat_number"),
        ]
        ordering = ["deck", "row_position", "col_position"]

    def __str__(self):
        return f"{self.seat_layout_id}:{self.seat_number}"


class Bus(PublicIdModel, TimeStampedModel):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        MAINTENANCE = "maintenance", "Maintenance"
        RETIRED = "retired", "Retired"

    operator = models.ForeignKey("operators.Operator", on_delete=models.CASCADE, related_name="buses")
    seat_layout = models.ForeignKey(SeatLayout, on_delete=models.PROTECT, related_name="buses")
    plate_number = models.CharField(max_length=20)
    amenities = models.JSONField(default=list, blank=True, help_text='e.g. ["ac", "wifi", "usb", "toilet"]')
    photo = models.ImageField(upload_to="fleet/buses/", null=True, blank=True)
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.ACTIVE)
    registration_expires_on = models.DateField(null=True, blank=True)
    insurance_expires_on = models.DateField(null=True, blank=True)

    class Meta:
        db_table = "fleet_bus"
        constraints = [
            models.UniqueConstraint(fields=["operator", "plate_number"], name="unique_operator_plate_number"),
        ]
        indexes = [models.Index(fields=["operator"])]
        ordering = ["plate_number"]

    def __str__(self):
        return self.plate_number
