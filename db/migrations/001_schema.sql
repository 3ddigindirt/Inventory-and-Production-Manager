CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TYPE priority_level AS ENUM ('low','medium','high');
CREATE TYPE inventory_event_type AS ENUM ('production_started','production_completed','order_received','sale','damaged','adjustment');
CREATE TYPE project_status AS ENUM ('backlog','active','paused','repair','completed','cancelled');

CREATE TABLE products (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  product_family text NOT NULL,
  variant text NOT NULL,
  square_sku text UNIQUE,
  price numeric(10,2),
  target_stock integer NOT NULL DEFAULT 0 CHECK (target_stock >= 0),
  priority priority_level NOT NULL DEFAULT 'low',
  etsy_enabled boolean NOT NULL DEFAULT false,
  notes text,
  active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(product_family, variant)
);

CREATE TABLE inventory_locations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  location_name text NOT NULL,
  sub_location text NOT NULL DEFAULT '',
  UNIQUE(location_name, sub_location)
);

CREATE TABLE inventory_baselines (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  product_id uuid NOT NULL REFERENCES products(id) ON DELETE CASCADE,
  location_id uuid REFERENCES inventory_locations(id),
  baseline_date date NOT NULL,
  quantity integer NOT NULL,
  source_row integer,
  UNIQUE(product_id, location_id, baseline_date)
);

CREATE TABLE inventory_transactions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  product_id uuid NOT NULL REFERENCES products(id),
  location_id uuid REFERENCES inventory_locations(id),
  transaction_at timestamptz NOT NULL DEFAULT now(),
  event_type inventory_event_type NOT NULL,
  quantity integer NOT NULL,
  price_per numeric(10,2),
  source text,
  source_reference text,
  notes text,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_inventory_tx_product ON inventory_transactions(product_id);
CREATE INDEX idx_inventory_tx_time ON inventory_transactions(transaction_at);

CREATE TABLE production_jobs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  product_id uuid NOT NULL REFERENCES products(id),
  quantity_planned integer NOT NULL CHECK (quantity_planned > 0),
  quantity_completed integer NOT NULL DEFAULT 0 CHECK (quantity_completed >= 0),
  status text NOT NULL DEFAULT 'planned' CHECK (status IN ('planned','active','completed','cancelled')),
  priority priority_level NOT NULL DEFAULT 'medium',
  started_at timestamptz,
  completed_at timestamptz,
  notes text,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE filaments (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  brand text NOT NULL,
  material_line text NOT NULL,
  color text NOT NULL,
  minimum_grams numeric(12,2) NOT NULL DEFAULT 0,
  minimum_spools numeric(8,2) NOT NULL DEFAULT 0,
  spoolman_filament_id integer UNIQUE,
  notes text,
  active boolean NOT NULL DEFAULT true,
  UNIQUE(brand, material_line, color)
);

CREATE TABLE filament_baselines (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  filament_id uuid NOT NULL REFERENCES filaments(id) ON DELETE CASCADE,
  baseline_date date NOT NULL,
  sealed_spools numeric(8,2) NOT NULL DEFAULT 0,
  open_spools numeric(8,2) NOT NULL DEFAULT 0,
  UNIQUE(filament_id, baseline_date)
);

CREATE TABLE product_filament_recipes (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  product_id uuid NOT NULL REFERENCES products(id) ON DELETE CASCADE,
  filament_id uuid NOT NULL REFERENCES filaments(id),
  sequence integer NOT NULL DEFAULT 1,
  grams_per_unit numeric(10,2),
  notes text,
  UNIQUE(product_id, filament_id)
);

CREATE TABLE projects (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name text NOT NULL,
  project_type text,
  designer_source text,
  status project_status NOT NULL DEFAULT 'backlog',
  priority priority_level NOT NULL DEFAULT 'medium',
  started_date date,
  progress_percent numeric(5,2) CHECK (progress_percent BETWEEN 0 AND 100),
  physical_location text,
  next_step text,
  reason text,
  notes text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE square_transactions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  transaction_id text,
  payment_id text,
  transaction_at timestamptz,
  sku text,
  item_name text,
  quantity numeric(10,2),
  gross_sales numeric(12,2),
  discounts numeric(12,2),
  net_sales numeric(12,2),
  tax numeric(12,2),
  channel text,
  location text,
  raw_data jsonb NOT NULL DEFAULT '{}'::jsonb,
  imported_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_square_transaction ON square_transactions(transaction_id, payment_id, sku) NULLS NOT DISTINCT;

CREATE TABLE integrations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  integration_type text NOT NULL UNIQUE,
  enabled boolean NOT NULL DEFAULT false,
  base_url text,
  settings jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE VIEW product_stock AS
SELECT p.id AS product_id, p.product_family, p.variant, p.price, p.target_stock, p.priority,
       COALESCE(b.qty,0) + COALESCE(t.qty,0) AS system_stock
FROM products p
LEFT JOIN (SELECT product_id, SUM(quantity)::int qty FROM inventory_baselines GROUP BY product_id) b ON b.product_id=p.id
LEFT JOIN (SELECT product_id, SUM(quantity)::int qty FROM inventory_transactions WHERE event_type <> 'production_started' GROUP BY product_id) t ON t.product_id=p.id;

CREATE VIEW product_restock_status AS
SELECT s.*,
       COALESCE(j.in_production,0) AS in_production,
       GREATEST(s.target_stock - s.system_stock - COALESCE(j.in_production,0),0) AS need
FROM product_stock s
LEFT JOIN (
  SELECT product_id, SUM(GREATEST(quantity_planned-quantity_completed,0))::int AS in_production
  FROM production_jobs WHERE status IN ('planned','active') GROUP BY product_id
) j ON j.product_id=s.product_id;

CREATE VIEW unmatched_square_sales AS
SELECT s.* FROM square_transactions s LEFT JOIN products p ON p.square_sku=s.sku WHERE p.id IS NULL;
