from django.db import migrations, models

def backfill_usernames(apps, schema_editor):
    Staff = apps.get_model("staff", "Staff")
    for s in Staff.objects.filter(username__isnull=True):
        s.username = f"staff{s.pk}"
        s.save(update_fields=["username"])

class Migration(migrations.Migration):
    dependencies = [
        ("staff", "0005_password"),
    ]
    operations = [
        migrations.AddField(
            model_name="staff",
            name="username",
            field=models.CharField(max_length=30, null=True),
        ),

        migrations.RunPython(backfill_usernames, migrations.RunPython.noop),

        migrations.RunSQL(
            "DROP INDEX IF EXISTS public.staff_username_9bca0107_like; "
            "DROP INDEX IF EXISTS public.staff_username_9bca0107;",
            reverse_sql=migrations.RunSQL.noop,
        ),

        migrations.AlterField(
            model_name="staff",
            name="username",
            field=models.CharField(max_length=30, unique=True),
        ),
    ]
