# apps/staff/management/commands/create_staff.py
import getpass
from django.core.management.base import BaseCommand, CommandError
from apps.staff.models import Staff, StaffRole

class Command(BaseCommand):
    help = "Create or reset a staff account (OWNER/MANAGER/KITCHEN/DELIVERY)."

    def add_arguments(self, parser):
        parser.add_argument("username", help="staff username")
        parser.add_argument("--role", choices=[c[0] for c in StaffRole.choices],
                            default=StaffRole.MANAGER, help="default=MANAGER")
        parser.add_argument("--password", help="plain password (omit to prompt)")
        parser.add_argument("--inactive", action="store_true",
                            help="create as inactive")
        parser.add_argument("--reset-password", action="store_true",
                            help="reset password if user exists")

    def handle(self, *args, **opts):
        username = opts["username"].strip()
        role = opts["role"]
        raw = opts.get("password")

        if raw is None:
            pw1 = getpass.getpass("Password: ")
            pw2 = getpass.getpass("Password (again): ")
            if pw1 != pw2:
                raise CommandError("Passwords do not match.")
            raw = pw1

        obj = Staff.objects.filter(username__iexact=username).first()
        if obj and not opts["reset_password"]:
            raise CommandError(
                f"Staff '{username}' already exists. "
                f"Use --reset-password to update its password."
            )

        if not obj:
            obj = Staff(username=username, role=role, is_active=not opts["inactive"])

        obj.set_password(raw)
        obj.role = role
        obj.save()
        state = "inactive" if not obj.is_active else "active"
        self.stdout.write(self.style.SUCCESS(
            f"Staff saved: id={obj.id}, username={obj.username}, role={obj.role}, {state}"
        ))
