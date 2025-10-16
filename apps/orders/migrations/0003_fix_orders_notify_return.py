# apps/orders/migrations/0003_fix_orders_notify_return.py
from django.db import migrations

SQL = r"""
CREATE OR REPLACE FUNCTION public.orders_notify() RETURNS trigger AS $$
DECLARE
  rec RECORD;
  ready BOOLEAN := FALSE;
  payload JSON;
BEGIN
  IF TG_OP = 'INSERT' THEN
    rec := NEW;
  ELSIF TG_OP = 'UPDATE' THEN
    rec := NEW;
  ELSE
    RETURN NEW;
  END IF;

  -- ready: meta.staff_ops[*].action == 'mark_ready'
  IF rec.meta IS NOT NULL THEN
    SELECT EXISTS (
      SELECT 1
      FROM jsonb_array_elements(COALESCE((rec.meta::jsonb)->'staff_ops','[]'::jsonb)) AS op
      WHERE op->>'action' = 'mark_ready'
    ) INTO ready;
  END IF;

  payload := json_build_object(
    'event', CASE WHEN TG_OP='INSERT' THEN 'order_created' ELSE 'order_updated' END,
    'order_id', rec.id,
    'id', rec.id,
    'status', rec.status,
    'ready', ready,
    'ordered_at', rec.ordered_at
  );
  PERFORM pg_notify('orders_events', payload::text);

  -- 중요: AFTER 트리거는 반환값이 사용되지 않으므로 NULL 반환으로 종결
  RETURN NULL;
END;
$$ LANGUAGE plpgsql;
"""

class Migration(migrations.Migration):
    dependencies = [("orders", "0002_orders_notify_trigger")]
    operations = [
        migrations.RunSQL(SQL, reverse_sql=migrations.RunSQL.noop),
    ]
