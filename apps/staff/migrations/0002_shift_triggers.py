from django.db import migrations

SQL = r"""
-- 근무 종료 시 work_minutes 설정 (BEFORE UPDATE OF ended_at)
CREATE OR REPLACE FUNCTION staff_shifts_set_minutes() RETURNS trigger AS $$
BEGIN
  IF NEW.ended_at IS NOT NULL THEN
    NEW.work_minutes := GREATEST(0, (EXTRACT(EPOCH FROM (NEW.ended_at - NEW.started_at))/60)::INT);
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_staff_shifts_set_minutes ON staff_shifts;
CREATE TRIGGER trg_staff_shifts_set_minutes
BEFORE UPDATE OF ended_at ON staff_shifts
FOR EACH ROW
WHEN (NEW.ended_at IS NOT NULL)
EXECUTE FUNCTION staff_shifts_set_minutes();

-- 퇴근 시 일일 집계 upsert (AFTER UPDATE OF ended_at), Asia/Seoul 기준
CREATE OR REPLACE FUNCTION staff_shifts_upsert_daily_hours() RETURNS trigger AS $$
DECLARE
  s_local TIMESTAMP;
  e_local TIMESTAMP;
  day_start TIMESTAMP;
  day_end   TIMESTAMP;
  cur_date  DATE;
  chunk_min INT;
BEGIN
  IF NEW.ended_at IS NULL THEN
    RETURN NEW;
  END IF;

  s_local := NEW.started_at AT TIME ZONE 'Asia/Seoul';
  e_local := NEW.ended_at   AT TIME ZONE 'Asia/Seoul';

  IF s_local::date = e_local::date THEN
    chunk_min := GREATEST(0, (EXTRACT(EPOCH FROM (e_local - s_local))/60)::INT);
    INSERT INTO staff_daily_hours(staff_id, work_date, minutes)
    VALUES (NEW.staff_id, s_local::date, chunk_min)
    ON CONFLICT (staff_id, work_date)
    DO UPDATE SET minutes = staff_daily_hours.minutes + EXCLUDED.minutes;
    RETURN NEW;
  END IF;

  -- 첫날
  cur_date  := s_local::date;
  day_end   := (date_trunc('day', s_local) + INTERVAL '1 day');
  chunk_min := GREATEST(0, (EXTRACT(EPOCH FROM (day_end - s_local))/60)::INT);
  INSERT INTO staff_daily_hours(staff_id, work_date, minutes)
  VALUES (NEW.staff_id, cur_date, chunk_min)
  ON CONFLICT (staff_id, work_date)
  DO UPDATE SET minutes = staff_daily_hours.minutes + EXCLUDED.minutes;

  -- 중간의 꽉 찬 날(있다면)
  cur_date := cur_date + 1;
  WHILE cur_date < e_local::date LOOP
    INSERT INTO staff_daily_hours(staff_id, work_date, minutes)
    VALUES (NEW.staff_id, cur_date, 24*60)
    ON CONFLICT (staff_id, work_date)
    DO UPDATE SET minutes = staff_daily_hours.minutes + EXCLUDED.minutes;
    cur_date := cur_date + 1;
  END LOOP;

  -- 마지막 날
  day_start := date_trunc('day', e_local);
  chunk_min := GREATEST(0, (EXTRACT(EPOCH FROM (e_local - day_start))/60)::INT);
  INSERT INTO staff_daily_hours(staff_id, work_date, minutes)
  VALUES (NEW.staff_id, e_local::date, chunk_min)
  ON CONFLICT (staff_id, work_date)
  DO UPDATE SET minutes = staff_daily_hours.minutes + EXCLUDED.minutes;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_staff_shifts_upsert_daily ON staff_shifts;
CREATE TRIGGER trg_staff_shifts_upsert_daily
AFTER UPDATE OF ended_at ON staff_shifts
FOR EACH ROW
WHEN (NEW.ended_at IS NOT NULL)
EXECUTE FUNCTION staff_shifts_upsert_daily_hours();
"""

class Migration(migrations.Migration):
    dependencies = [
        ("staff", "0001_initial"),
    ]
    operations = [
        migrations.RunSQL(SQL, reverse_sql="""
            DROP TRIGGER IF EXISTS trg_staff_shifts_upsert_daily ON staff_shifts;
            DROP FUNCTION IF EXISTS staff_shifts_upsert_daily_hours();
            DROP TRIGGER IF EXISTS trg_staff_shifts_set_minutes ON staff_shifts;
            DROP FUNCTION IF EXISTS staff_shifts_set_minutes();
        """),
    ]
