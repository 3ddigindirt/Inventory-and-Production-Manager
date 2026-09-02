CREATE TABLE IF NOT EXISTS square_import_batches (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  filename text NOT NULL,
  imported_at timestamptz NOT NULL DEFAULT now(),
  apply_inventory_from date,
  rows_seen integer NOT NULL DEFAULT 0,
  rows_imported integer NOT NULL DEFAULT 0,
  rows_duplicate integer NOT NULL DEFAULT 0,
  rows_matched integer NOT NULL DEFAULT 0,
  rows_unmatched integer NOT NULL DEFAULT 0,
  inventory_transactions_created integer NOT NULL DEFAULT 0,
  rows_skipped integer NOT NULL DEFAULT 0,
  notes text
);

ALTER TABLE square_transactions ADD COLUMN IF NOT EXISTS import_batch_id uuid REFERENCES square_import_batches(id) ON DELETE SET NULL;
ALTER TABLE square_transactions ADD COLUMN IF NOT EXISTS category text;
ALTER TABLE square_transactions ADD COLUMN IF NOT EXISTS price_point_name text;
ALTER TABLE square_transactions ADD COLUMN IF NOT EXISTS modifiers_applied text;

CREATE INDEX IF NOT EXISTS idx_square_transactions_batch ON square_transactions(import_batch_id);
CREATE INDEX IF NOT EXISTS idx_square_transactions_sku ON square_transactions(sku);
