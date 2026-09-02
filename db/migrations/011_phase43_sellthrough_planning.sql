ALTER TABLE vendor_fairs
  ADD COLUMN IF NOT EXISTS planning_mode text NOT NULL DEFAULT 'conservative'
    CHECK (planning_mode IN ('conservative','historical','custom'));

ALTER TABLE vendor_fairs
  ADD COLUMN IF NOT EXISTS custom_sell_through_percent numeric(5,2)
    CHECK (custom_sell_through_percent IS NULL OR (custom_sell_through_percent >= 0 AND custom_sell_through_percent <= 100));

CREATE INDEX IF NOT EXISTS idx_vendor_fairs_planning_date
  ON vendor_fairs(start_date, end_date);
