-- Square cleanup + reliable ON CONFLICT support.
-- PostgreSQL cannot infer a plain ON CONFLICT(source_key) from a partial
-- unique index, so use full unique indexes. PostgreSQL permits multiple NULLs.
DROP INDEX IF EXISTS uq_square_source_key;
CREATE UNIQUE INDEX IF NOT EXISTS uq_square_source_key
  ON square_transactions(source_key);

DROP INDEX IF EXISTS uq_inventory_tx_source_key;
CREATE UNIQUE INDEX IF NOT EXISTS uq_inventory_tx_source_key
  ON inventory_transactions(source_key);
