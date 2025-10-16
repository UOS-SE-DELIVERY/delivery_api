from django.db import migrations, models
from django.contrib.auth.hashers import make_password

def set_unusable_passwords(apps, schema_editor):
    Staff = apps.get_model("staff", "Staff")
    for s in Staff.objects.filter(password__isnull=True):
        s.password = make_password(None)  # unusable password
        s.save(update_fields=["password"])

class Migration(migrations.Migration):

    dependencies = [
        ("staff", "0004_remove_staffshift_ck_shift_time_order_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="staff",
            name="password",
            field=models.CharField(max_length=256, null=True),
        ),
        migrations.RunPython(set_unusable_passwords, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="staff",
            name="password",
            field=models.CharField(max_length=256),
        ),
    ]
