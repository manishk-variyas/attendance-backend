-- Migration: Update work_location enum
-- Drops HOME and REMOTE, adds REMOTE_ONSITE and REMOTE_OFFSITE
-- REMOTE → REMOTE_OFFSITE, HOME → WFH

BEGIN;

-- Drop employee_master default that references old type
ALTER TABLE employee_master ALTER COLUMN work_location_status DROP DEFAULT;

-- shifts table
ALTER TABLE shifts ALTER COLUMN work_location_status DROP DEFAULT;
ALTER TABLE shifts ALTER COLUMN work_location_status TYPE work_location_new
  USING CASE work_location_status::text
    WHEN 'HOME' THEN 'WFH'::text::work_location_new
    WHEN 'REMOTE' THEN 'REMOTE_OFFSITE'::text::work_location_new
    ELSE work_location_status::text::work_location_new
  END;
ALTER TABLE shifts ALTER COLUMN work_location_status SET DEFAULT 'OFFICE'::text::work_location_new;

-- attendance table
ALTER TABLE attendance ALTER COLUMN work_location_status DROP DEFAULT;
ALTER TABLE attendance ALTER COLUMN work_location_status TYPE work_location_new
  USING CASE work_location_status::text
    WHEN 'HOME' THEN 'WFH'::text::work_location_new
    WHEN 'REMOTE' THEN 'REMOTE_OFFSITE'::text::work_location_new
    ELSE work_location_status::text::work_location_new
  END;
ALTER TABLE attendance ALTER COLUMN work_location_status SET DEFAULT 'OFFICE'::text::work_location_new;

-- Drop old type and rename new
DROP TYPE work_location;
ALTER TYPE work_location_new RENAME TO work_location;

-- Restore employee_master default as plain text
ALTER TABLE employee_master ALTER COLUMN work_location_status SET DEFAULT 'OFFICE';

COMMIT;
