CREATE TABLE IF NOT EXISTS app_settings (
  key text PRIMARY KEY,
  value text NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO app_settings(key,value) VALUES
  ('business_name','Create Favorite Things LLC'),
  ('currency','USD'),
  ('timezone','America/New_York'),
  ('default_spool_weight_grams','1000'),
  ('backup_retention_days','30'),
  ('backup_warning_hours','36')
ON CONFLICT (key) DO NOTHING;

CREATE TABLE IF NOT EXISTS backup_operations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  operation text NOT NULL CHECK (operation IN ('backup','restore','delete')),
  filename text,
  status text NOT NULL CHECK (status IN ('success','failed')),
  size_bytes bigint,
  message text,
  started_at timestamptz NOT NULL DEFAULT now(),
  completed_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_backup_operations_started_at
  ON backup_operations(started_at DESC);
