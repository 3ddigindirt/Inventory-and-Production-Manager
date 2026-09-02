ALTER TABLE vendor_fairs ADD COLUMN IF NOT EXISTS selling_start_time time;
ALTER TABLE vendor_fairs ADD COLUMN IF NOT EXISTS selling_end_time time;
ALTER TABLE vendor_fairs ADD COLUMN IF NOT EXISTS reconciliation_status text NOT NULL DEFAULT 'not_reconciled'
  CHECK (reconciliation_status IN ('not_reconciled','reconciled'));
ALTER TABLE vendor_fairs ADD COLUMN IF NOT EXISTS reconciled_at timestamptz;

CREATE TABLE IF NOT EXISTS vendor_fair_square_assignments (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  vendor_fair_id uuid NOT NULL REFERENCES vendor_fairs(id) ON DELETE CASCADE,
  square_transaction_id uuid NOT NULL REFERENCES square_transactions(id) ON DELETE CASCADE,
  assignment_source text NOT NULL DEFAULT 'window' CHECK (assignment_source IN ('window','manual')),
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(square_transaction_id)
);
CREATE INDEX IF NOT EXISTS idx_fair_square_assignments_fair ON vendor_fair_square_assignments(vendor_fair_id);
