DO $$ BEGIN
  CREATE TYPE filament_event_type AS ENUM ('order_placed','order_received','spool_opened','used','sealed_adjustment','open_adjustment');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

ALTER TABLE inventory_transactions ADD COLUMN IF NOT EXISTS source_key text;
CREATE UNIQUE INDEX IF NOT EXISTS uq_inventory_tx_source_key ON inventory_transactions(source_key) WHERE source_key IS NOT NULL;

ALTER TABLE production_jobs ADD COLUMN IF NOT EXISTS source_key text;
CREATE UNIQUE INDEX IF NOT EXISTS uq_production_jobs_source_key ON production_jobs(source_key) WHERE source_key IS NOT NULL;

ALTER TABLE projects ADD COLUMN IF NOT EXISTS source_system text;
ALTER TABLE projects ADD COLUMN IF NOT EXISTS source_key text;
CREATE UNIQUE INDEX IF NOT EXISTS uq_projects_source_key ON projects(source_key) WHERE source_key IS NOT NULL;

ALTER TABLE square_transactions ADD COLUMN IF NOT EXISTS source_key text;
CREATE UNIQUE INDEX IF NOT EXISTS uq_square_source_key ON square_transactions(source_key) WHERE source_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS filament_transactions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  filament_id uuid NOT NULL REFERENCES filaments(id),
  transaction_at timestamptz NOT NULL DEFAULT now(),
  event_type filament_event_type NOT NULL,
  quantity numeric(10,3) NOT NULL,
  project_source text,
  notes text,
  source_key text UNIQUE,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_filament_tx_filament ON filament_transactions(filament_id);
CREATE INDEX IF NOT EXISTS idx_filament_tx_time ON filament_transactions(transaction_at);

CREATE TABLE IF NOT EXISTS migration_runs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  source text NOT NULL,
  status text NOT NULL CHECK (status IN ('running','completed','failed')),
  started_at timestamptz NOT NULL DEFAULT now(),
  completed_at timestamptz,
  rows_seen integer NOT NULL DEFAULT 0,
  rows_imported integer NOT NULL DEFAULT 0,
  rows_skipped integer NOT NULL DEFAULT 0,
  errors integer NOT NULL DEFAULT 0,
  details jsonb NOT NULL DEFAULT '{}'::jsonb
);

DROP VIEW IF EXISTS filament_stock;
CREATE VIEW filament_stock AS
WITH b AS (
  SELECT filament_id, SUM(sealed_spools) sealed_baseline, SUM(open_spools) open_baseline
  FROM filament_baselines GROUP BY filament_id
), t AS (
  SELECT filament_id,
    COALESCE(SUM(quantity) FILTER (WHERE event_type='order_placed'),0) ordered,
    COALESCE(SUM(quantity) FILTER (WHERE event_type='order_received'),0) received,
    COALESCE(SUM(quantity) FILTER (WHERE event_type='spool_opened'),0) opened,
    COALESCE(SUM(quantity) FILTER (WHERE event_type='used'),0) used,
    COALESCE(SUM(quantity) FILTER (WHERE event_type='sealed_adjustment'),0) sealed_adjustment,
    COALESCE(SUM(quantity) FILTER (WHERE event_type='open_adjustment'),0) open_adjustment
  FROM filament_transactions GROUP BY filament_id
)
SELECT f.id AS filament_id, f.brand, f.material_line, f.color, f.minimum_spools,
  GREATEST(COALESCE(b.sealed_baseline,0)+COALESCE(t.received,0)-COALESCE(t.opened,0)+COALESCE(t.sealed_adjustment,0),0) AS sealed,
  GREATEST(COALESCE(b.open_baseline,0)+COALESCE(t.opened,0)-COALESCE(t.used,0)+COALESCE(t.open_adjustment,0),0) AS open,
  GREATEST(COALESCE(t.ordered,0)-COALESCE(t.received,0),0) AS on_order,
  GREATEST(f.minimum_spools - (
    GREATEST(COALESCE(b.sealed_baseline,0)+COALESCE(t.received,0)-COALESCE(t.opened,0)+COALESCE(t.sealed_adjustment,0),0)
    + GREATEST(COALESCE(b.open_baseline,0)+COALESCE(t.opened,0)-COALESCE(t.used,0)+COALESCE(t.open_adjustment,0),0)
    + GREATEST(COALESCE(t.ordered,0)-COALESCE(t.received,0),0)
  ),0) AS need_to_buy
FROM filaments f
LEFT JOIN b ON b.filament_id=f.id
LEFT JOIN t ON t.filament_id=f.id;

CREATE OR REPLACE VIEW inventory_metrics AS
SELECT
  COALESCE(SUM(system_stock),0)::int AS units_in_stock,
  COALESCE(SUM(system_stock * COALESCE(price,0)),0)::numeric(14,2) AS retail_inventory_value,
  COUNT(*) FILTER (WHERE need > 0)::int AS products_below_target,
  COUNT(*) FILTER (WHERE need = 0)::int AS products_at_or_above_target,
  CASE WHEN COUNT(*)=0 THEN 0 ELSE ROUND((COUNT(*) FILTER (WHERE need=0))::numeric / COUNT(*) * 100,2) END AS percent_at_or_above_target
FROM product_restock_status;
-- The starter's transaction/payment/SKU uniqueness can collapse legitimate repeated line items.
-- Phase 2 deduplicates Square rows by deterministic source_key instead.
DROP INDEX IF EXISTS uq_square_transaction;
