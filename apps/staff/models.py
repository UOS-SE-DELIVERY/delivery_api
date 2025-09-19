from django.db import models
from django.db.models import Q, F
from django.utils import timezone

class StaffRole(models.TextChoices):
    DELIVERY = "delivery", "Delivery"
    KITCHEN  = "kitchen",  "Kitchen"

class Staff(models.Model):
    id = models.BigAutoField(primary_key=True)
    name = models.TextField()
    role = models.CharField(max_length=20, choices=StaffRole.choices)
    active = models.BooleanField(default=True)

    class Meta:
        db_table = "staff"

    def __str__(self): return f"{self.name} ({self.role})"

class StaffShift(models.Model):
    id = models.BigAutoField(primary_key=True)
    staff = models.ForeignKey(Staff, on_delete=models.CASCADE, related_name="shifts")
    started_at = models.DateTimeField(default=timezone.now)
    ended_at = models.DateTimeField(null=True, blank=True)
    work_minutes = models.IntegerField(null=True, blank=True, editable=False)

    class Meta:
        db_table = "staff_shifts"
        constraints = [
            models.CheckConstraint(
                name="ck_shift_time_order",
                check=Q(ended_at__isnull=True) | Q(ended_at__gt=F("started_at")),
            ),
            # 직원별 '미종료' 근무는 1개만 허용 (부분 유니크)
            models.UniqueConstraint(
                fields=["staff"], condition=Q(ended_at__isnull=True), name="ux_staff_one_open_shift"
            ),
        ]

class StaffDailyHours(models.Model):
    staff = models.ForeignKey(Staff, on_delete=models.CASCADE, related_name="daily_hours")
    work_date = models.DateField()
    minutes = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "staff_daily_hours"
        unique_together = (("staff", "work_date"),)
