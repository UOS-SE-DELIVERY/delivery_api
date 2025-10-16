# apps/orders/migrations/0002_orders_notify_trigger.py
from django.db import migrations, connections, DEFAULT_DB_ALIAS


def forwards(apps, schema_editor):
    Order = apps.get_model("orders", "Order")
    table = Order._meta.db_table  # ex) "orders"
    with connections[DEFAULT_DB_ALIAS].cursor() as cur:
        cur.execute("""
        CREATE OR REPLACE FUNCTION orders_notify() RETURNS trigger AS $$
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
            -- 우리가 다루지 않는 연산이면 그대로 통과
            RETURN NEW;
          END IF;

          -- ready: meta.staff_ops[*].action == 'mark_ready'
          IF rec.meta IS NOT NULL THEN
            SELECT EXISTS (
              SELECT 1
              FROM jsonb_array_elements(
                    COALESCE((rec.meta::jsonb)->'staff_ops','[]'::jsonb)
                  ) AS op
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

          -- AFTER 트리거는 반환값이 사용되지 않으므로 NULL 반환으로 종결
          RETURN NULL;
        END;
        $$ LANGUAGE plpgsql;
        """)

        # 트리거 재생성(있으면 지우고 다시)
        cur.execute(f'DROP TRIGGER IF EXISTS trg_orders_notify_ins ON "{table}";')
        cur.execute(f'''
        CREATE TRIGGER trg_orders_notify_ins
        AFTER INSERT ON "{table}"
        FOR EACH ROW EXECUTE FUNCTION orders_notify();
        ''')
        cur.execute(f'DROP TRIGGER IF EXISTS trg_orders_notify_upd ON "{table}";')
        cur.execute(f'''
        CREATE TRIGGER trg_orders_notify_upd
        AFTER UPDATE OF status, meta ON "{table}"
        FOR EACH ROW EXECUTE FUNCTION orders_notify();
        ''')


def backwards(apps, schema_editor):
    Order = apps.get_model("orders", "Order")
    table = Order._meta.db_table
    with connections[DEFAULT_DB_ALIAS].cursor() as cur:
        cur.execute(f'DROP TRIGGER IF EXISTS trg_orders_notify_ins ON "{table}";')
        cur.execute(f'DROP TRIGGER IF EXISTS trg_orders_notify_upd ON "{table}";')
        cur.execute("DROP FUNCTION IF EXISTS orders_notify();")


class Migration(migrations.Migration):
    dependencies = [("orders", "0001_initial")]
    operations = [migrations.RunPython(forwards, backwards)]
