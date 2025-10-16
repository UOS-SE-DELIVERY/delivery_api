# apps/staff/migrations/0003_align_models.py
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone
from django.contrib.postgres.fields import ArrayField

def fill_zero_shift_minutes(apps, schema_editor):
    schema_editor.execute("UPDATE staff_shifts SET work_minutes = 0 WHERE work_minutes IS NULL;")

class Migration(migrations.Migration):
    dependencies = [
        ("staff", "0002_shift_triggers"),
        ("auth", "0012_alter_user_first_name_max_length"),  # Django 기본 auth 최신 의존성(버전에 맞춰 조정 가능)
    ]

    operations = [
        # Staff: 컬럼명 정렬
        migrations.RenameField(
            model_name="staff",
            old_name="name",
            new_name="display_name",
        ),
        migrations.RenameField(
            model_name="staff",
            old_name="active",
            new_name="is_active",
        ),
        # Staff: 추가 컬럼들
        migrations.AddField(
            model_name="staff",
            name="user",
            field=models.OneToOneField(
                null=True, blank=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="staff",
                to="auth.user",
            ),
        ),
        migrations.AddField(
            model_name="staff",
            name="phone",
            field=models.CharField(max_length=20, blank=True, default=""),
        ),
        migrations.AddField(
            model_name="staff",
            name="meta",
            field=models.JSONField(default=dict, blank=True),
        ),
        migrations.AddField(
            model_name="staff",
            name="created_at",
            field=models.DateTimeField(auto_now_add=True, default=django.utils.timezone.now),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="staff",
            name="updated_at",
            field=models.DateTimeField(auto_now=True, default=django.utils.timezone.now),
            preserve_default=False,
        ),
        migrations.AddIndex(
            model_name="staff",
            index=models.Index(fields=["role", "is_active"], name="idx_staff_role_active"),
        ),

        # StaffShift: 필드/타임스탬프 보강
        migrations.AddField(
            model_name="staffshift",
            name="created_at",
            field=models.DateTimeField(auto_now_add=True, default=django.utils.timezone.now),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="staffshift",
            name="updated_at",
            field=models.DateTimeField(auto_now=True, default=django.utils.timezone.now),
            preserve_default=False,
        ),
        migrations.AlterField(
            model_name="staffshift",
            name="work_minutes",
            field=models.IntegerField(null=True, blank=True, editable=False),
        ),
        migrations.RunPython(fill_zero_shift_minutes, migrations.RunPython.noop),

        # StaffDailyHours: shift_ids/타임스탬프 추가
        migrations.AddField(
            model_name="staffdailyhours",
            name="shift_ids",
            field=ArrayField(base_field=models.BigIntegerField(), default=list, blank=True),
        ),
        migrations.AddField(
            model_name="staffdailyhours",
            name="created_at",
            field=models.DateTimeField(auto_now_add=True, default=django.utils.timezone.now),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="staffdailyhours",
            name="updated_at",
            field=models.DateTimeField(auto_now=True, default=django.utils.timezone.now),
            preserve_default=False,
        ),
    ]
