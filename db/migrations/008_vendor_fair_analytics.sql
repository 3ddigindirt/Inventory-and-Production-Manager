ALTER TABLE vendor_fairs ADD COLUMN IF NOT EXISTS event_type text;
ALTER TABLE vendor_fairs ADD COLUMN IF NOT EXISTS hours_open numeric(8,2) CHECK (hours_open IS NULL OR hours_open >= 0);
ALTER TABLE vendor_fairs ADD COLUMN IF NOT EXISTS attendance_estimate integer CHECK (attendance_estimate IS NULL OR attendance_estimate >= 0);
ALTER TABLE vendor_fairs ADD COLUMN IF NOT EXISTS weather_conditions text;
ALTER TABLE vendor_fairs ADD COLUMN IF NOT EXISTS booth_location_quality text;
ALTER TABLE vendor_fairs ADD COLUMN IF NOT EXISTS would_return boolean;
