from django.db import migrations

SQL = """
ALTER TABLE order_dinner
  ADD CONSTRAINT fk_order_dinner_allowed_combo
  FOREIGN KEY (dinner_type_id, style_id)
  REFERENCES dinner_style_allowed(dinner_type_id, style_id)
  ON UPDATE RESTRICT ON DELETE RESTRICT;
"""

class Migration(migrations.Migration):
    dependencies = [
        ("orders", "0001_initial"),
        ("catalog", "0001_initial"),
    ]
    operations = [
        migrations.RunSQL(
            sql=SQL,
            reverse_sql="ALTER TABLE order_dinner DROP CONSTRAINT IF EXISTS fk_order_dinner_allowed_combo;",
        )
    ]
