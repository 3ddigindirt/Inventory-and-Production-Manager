CREATE TABLE IF NOT EXISTS vendor_fair_product_targets (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  vendor_fair_id uuid NOT NULL REFERENCES vendor_fairs(id) ON DELETE CASCADE,
  product_id uuid NOT NULL REFERENCES products(id) ON DELETE CASCADE,
  target_quantity integer NOT NULL DEFAULT 0 CHECK (target_quantity >= 0),
  notes text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(vendor_fair_id, product_id)
);

CREATE INDEX IF NOT EXISTS idx_vendor_fair_product_targets_fair
  ON vendor_fair_product_targets(vendor_fair_id);
CREATE INDEX IF NOT EXISTS idx_vendor_fair_product_targets_product
  ON vendor_fair_product_targets(product_id);
