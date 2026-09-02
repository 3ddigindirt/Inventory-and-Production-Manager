ALTER TABLE filaments
  ADD COLUMN IF NOT EXISTS nominal_spool_weight_grams numeric(12,2) NOT NULL DEFAULT 1000;

CREATE INDEX IF NOT EXISTS idx_recipe_product ON product_filament_recipes(product_id);
CREATE INDEX IF NOT EXISTS idx_recipe_filament ON product_filament_recipes(filament_id);

-- Production completion transactions created from Phase 3.4 onward carry
-- production job UUID in source_reference, allowing a deleted completed job
-- to roll its automatically-added inventory back out safely.
