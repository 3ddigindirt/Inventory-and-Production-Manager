ALTER TABLE products ADD COLUMN IF NOT EXISTS print_time_minutes numeric(10,2) CHECK (print_time_minutes IS NULL OR print_time_minutes >= 0);
ALTER TABLE products ADD COLUMN IF NOT EXISTS batch_size integer NOT NULL DEFAULT 1 CHECK (batch_size > 0);

CREATE TABLE IF NOT EXISTS production_capacity_groups (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name text NOT NULL UNIQUE,
  printer_count integer NOT NULL DEFAULT 1 CHECK (printer_count > 0),
  hours_per_printer_day numeric(6,2) NOT NULL DEFAULT 16 CHECK (hours_per_printer_day >= 0 AND hours_per_printer_day <= 24),
  efficiency_percent numeric(5,2) NOT NULL DEFAULT 85 CHECK (efficiency_percent > 0 AND efficiency_percent <= 100),
  active boolean NOT NULL DEFAULT true,
  notes text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO production_capacity_groups(name,printer_count,hours_per_printer_day,efficiency_percent,notes)
VALUES ('General printers',1,16,85,'Default manual capacity group; edit to match your available fleet.')
ON CONFLICT(name) DO NOTHING;
