ALTER TABLE employee_master RENAME COLUMN home_address TO current_address;
ALTER TABLE employee_master ADD COLUMN permanent_address VARCHAR(150);
