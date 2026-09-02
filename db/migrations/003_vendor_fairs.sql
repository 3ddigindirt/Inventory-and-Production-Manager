CREATE TABLE IF NOT EXISTS vendor_fairs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name text NOT NULL,
  start_date date NOT NULL,
  end_date date,
  location text,
  booth_fee numeric(10,2) NOT NULL DEFAULT 0 CHECK (booth_fee >= 0),
  revenue numeric(12,2) CHECK (revenue >= 0),
  notes text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CHECK (end_date IS NULL OR end_date >= start_date)
);

CREATE INDEX IF NOT EXISTS idx_vendor_fairs_start_date ON vendor_fairs(start_date);
