from django.db import models
from django.contrib.auth.hashers import make_password, check_password

class StaffRole(models.TextChoices):
    OWNER = "OWNER", "Owner"
    MANAGER = "MANAGER", "Manager"
    KITCHEN = "KITCHEN", "Kitchen"
    DELIVERY = "DELIVERY","Delivery"

class Staff(models.Model):
    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def is_anonymous(self) -> bool:
        return False

    username = models.CharField(max_length=30, unique=True)
    password = models.CharField(max_length=256)  # Django 해시 저장(pbkkdf2_...)
    role = models.CharField(max_length=16, choices=StaffRole.choices, default=StaffRole.DELIVERY)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "staff_staff"

    def set_password(self, raw_password: str):
        self.password = make_password(raw_password)

    def check_password(self, raw_password: str) -> bool:
        return check_password(raw_password, self.password)

    def __str__(self):
        return f"{self.username}({self.role})"
