# CFT Inventory - Phase 3.6 Product History + Vendor Fair Planning

Phase 3.6 adds two workflow features to the existing Docker/Postgres application.

## Product inventory history

The Inventory page now includes a **History** button for every product. The history view combines:

- starting inventory baselines;
- Square sales and refunds;
- production completions;
- damage;
- order receipts; and
- manual inventory adjustments.

The detail view also shows current stock, target stock, quantity in production, and current need.

## Vendor fair inventory planning

Upcoming Vendor Fairs now include **Plan Inventory**. For each event you can set a target quantity for any product. The app compares that target with:

- current finished inventory; and
- quantities already in planned/active production.

It then calculates how many additional units still need to be made for the event. The vendor-fair list and Dashboard surface the resulting prep status.

Plan lines are unique per event/product. Adding the same product again updates its target rather than creating a duplicate.

## Upgrade

Keep your current `.env` and your existing Postgres Docker volume. Replace the project files with this build, then run:

```bash
docker compose up -d --build
```

Migration `006_vendor_fair_planning.sql` applies automatically. No database reset is required.

The browser app remains at `http://localhost:8000/` and API docs remain at `http://localhost:8000/docs`.

---

# CFT Inventory - Phase 3.5 Square Upload

This build adds browser-based Square itemized-sales CSV uploads to the existing CFT Inventory app. It preserves the Phase 3.4.1 product, production, filament recipe/forecasting, vendor fair, and Spoolman features.

## Square Sales

A new **Square Sales** page is available in the left navigation. Use **Upload CSV** to select an Itemized Sales CSV exported from Square.

The importer:

- stores the raw Square sale row for audit/history;
- matches Square `SKU` to the product's `Square SKU`;
- creates an inventory `sale` transaction for matched whole-unit quantities;
- treats a negative Square quantity/refund as stock being added back;
- skips the `CFT SALE` summary item;
- deduplicates raw rows and inventory changes, so overlapping exports are safe to upload repeatedly;
- lists unmatched SKU rows in the browser; and
- records an import-history summary for each CSV upload.

### Inventory cutoff

The upload dialog asks for **Apply inventory deductions for sales on/after** and defaults to today. This matters because your starting inventory was entered manually. For example, if your starting inventory reflects stock as of August 21, 2026, use `2026-08-21`. Older Square rows will still be stored, but will not reduce that starting inventory.

If you later map an unmatched SKU to a product, you can upload the same CSV again. The raw Square row remains deduplicated, while a missing inventory transaction can still be created if the sale is on/after the chosen cutoff.

### Required Square columns

The uploader is designed for Square's itemized-sales export and requires at least:

- `Date`
- `Item`
- `Qty`

It also uses `Time`, `SKU`, `Transaction ID`, `Payment ID`, `Gross Sales`, `Discounts`, `Net Sales`, `Tax`, `Location`, `Channel`, `Category`, `Price Point Name`, and `Modifiers Applied` when present.

## Upgrade

Keep your existing `.env` and Postgres Docker volume, replace the project files with this build, then run:

```bash
docker compose up -d --build
```

Migration `005_square_upload.sql` applies automatically. No database reset is required.

The app remains at `http://localhost:8000/` and API docs remain at `http://localhost:8000/docs`.

---

# CFT Inventory - Manual Entry Edition

Dockerized CFT inventory and production backend using PostgreSQL, FastAPI, and optional read-only Spoolman integration.

Google Sheets import has been removed. The application is now designed for direct/manual data entry through the API and, in the next phase, the browser UI.

## What changed

- Removed Google Sheets credentials and API dependencies.
- Removed Google Sheet import, migration-status, and Sheet-validation endpoints.
- Removed the Google credentials volume mount from Docker Compose.
- Added manual creation endpoints for products, inventory baselines/transactions, filament, filament baselines/transactions, production jobs, projects, and filament recipes.
- Kept the existing PostgreSQL schema, derived inventory views, automatic SQL migration runner, backups, Square tables, and optional Spoolman integration.
- Existing Docker/Postgres volumes can be reused; you do not need to delete your database.

## Upgrade from Phase 2

1. Back up your current database:

   ```bash
   docker compose --profile backup run --rm backup
   ```

2. Replace the application/project files with this package while keeping your existing `.env` and Docker `postgres_data` volume.

3. Remove these old Google settings from `.env` if present:

   ```text
   GOOGLE_SHEET_ID=...
   GOOGLE_SERVICE_ACCOUNT_FILE=...
   ```

   They are ignored if left in place, but are no longer needed.

4. You can delete your old `secrets/google-service-account.json` file. The application no longer reads it.

5. Rebuild:

   ```bash
   docker compose up -d --build
   ```

6. Open:

   ```text
   http://localhost:8000/docs
   ```

## Recommended manual setup order

### 1. Add products

Use:

```text
POST /api/products
```

Example body:

```json
{
  "product_family": "Cinderwing Dragon",
  "variant": "Long",
  "square_sku": null,
  "price": 20,
  "target_stock": 10,
  "priority": "high",
  "etsy_enabled": false,
  "notes": null
}
```

Then run `GET /api/products` and copy the product UUID when you need it for baselines, transactions, production, or recipes.

### 2. Enter starting inventory

Use:

```text
POST /api/inventory/baselines
```

Example:

```json
{
  "product_id": "PRODUCT-UUID-HERE",
  "location_name": "Office",
  "sub_location": "small clear open",
  "baseline_date": "2026-08-20",
  "quantity": 1
}
```

A baseline represents the stock you physically have when you start using the new application.

### 3. Record future inventory changes

Use:

```text
POST /api/inventory/transactions
```

Examples of `event_type`:

```text
production_completed
order_received
sale
damaged
adjustment
```

Use a positive or negative quantity according to its stock effect. For example, a sale of one item should use `quantity: -1`.

### 4. Add filament catalog entries

Use:

```text
POST /api/filaments
```

Example:

```json
{
  "brand": "Generic",
  "material_line": "PLA",
  "color": "black",
  "minimum_spools": 2,
  "minimum_grams": 0,
  "spoolman_filament_id": null,
  "notes": null
}
```

If Spoolman is enabled, `spoolman_filament_id` can be used to map this business/planning entry to the corresponding Spoolman filament.

### 5. Enter filament starting stock if not using Spoolman as the physical source

Use:

```text
POST /api/filament/baselines
```

Then future changes can be recorded through:

```text
POST /api/filament/transactions
```

### 6. Add product filament recipes

Use:

```text
POST /api/recipes
```

This links products to filament and optionally records grams required per finished unit.

### 7. Add production jobs

Use:

```text
POST /api/production
```

Planned/active jobs automatically feed the `in_production` and `need` calculations shown by `GET /api/inventory`.

### 8. Add backlog/WIP projects

Use:

```text
POST /api/projects
```

Both backlog and WIP use the same project table; change the status between `backlog`, `active`, `paused`, `repair`, `completed`, or `cancelled`.

## Useful read endpoints

```text
GET /health
GET /api/products
GET /api/inventory
GET /api/inventory/transactions
GET /api/filaments
GET /api/filament
GET /api/filament/transactions
GET /api/recipes
GET /api/production
GET /api/projects
GET /api/metrics
GET /api/square/unmatched
GET /api/spoolman/health
GET /api/spoolman/spools
```

## Spoolman

Spoolman remains optional and read-only in this edition.

Set in `.env`:

```env
SPOOLMAN_ENABLED=true
SPOOLMAN_URL=http://your-spoolman-host:8000
```

Then test:

```text
GET /api/spoolman/health
GET /api/spoolman/spools
```

The next UI phase can expose the manual endpoints as ordinary forms so `/docs` will no longer be needed for day-to-day use.

## Phase 3 browser interface

Phase 3 adds a browser UI at:

```text
http://localhost:8000/
```

The FastAPI developer interface remains available at `http://localhost:8000/docs`.

The UI currently includes:

- Dashboard with inventory value, shortages, active production and filament purchase alerts
- Inventory list, product creation, starting stock entry and stock adjustments
- Production queue with planned/active jobs and one-click completion into inventory
- Filament catalog, starting quantities and filament event logging
- WIP/backlog project creation and basic status transitions
- Optional read-only Spoolman connection status and spool inspection

### Upgrade from the manual-entry edition

Keep your existing `.env` and Docker `postgres_data` volume. Replace the application/project files, then run:

```bash
docker compose up -d --build
```

No database reset is required. Phase 3 uses the existing schema and data.

## Phase 3.2: product management and vendor fairs

Phase 3.2 adds:

- Edit product family/name and variant from the Inventory screen.
- Permanently delete an inventory product from the Inventory screen. Deletion also removes that product's starting-stock rows, inventory transactions, production jobs, and filament recipes. A browser confirmation is required.
- A new **Vendor Fairs** section for future and past events.
- Upcoming fairs include a countdown to the start date.
- The Dashboard shows the next vendor fair and its countdown, plus a short upcoming-event list.
- Past fairs are automatically ranked by recorded revenue.
- Vendor fairs store start/end date, location, booth fee, revenue, and notes.
- Past-event tables show revenue, booth fee, and net revenue after booth fee.
- Vendor fairs can be edited or deleted from the browser.

A new `003_vendor_fairs.sql` migration is applied automatically when the application starts. Keep your existing Postgres volume; no reset is needed.

Upgrade normally with:

```bash
docker compose up -d --build
```

## Phase 3.3 - Live Spoolman filament display

When `SPOOLMAN_ENABLED=true` and the configured Spoolman instance is reachable, the Filament page now automatically displays every spool returned by Spoolman. Each row includes a visual color swatch, vendor/name, material, remaining and initial weight, percentage remaining, location, and Spoolman spool ID. Archived spools remain visible but are visually muted.

The existing internal filament table remains below the Spoolman inventory for purchasing targets, recipes, and filament that is not managed by Spoolman. Spoolman access remains read-only.

## Phase 3.4 - Production editing and filament forecasting

Phase 3.4 adds:

- Edit and delete controls for production jobs.
- New production completions are linked to their job so deleting a completed job can also remove its automatically-created inventory addition.
- Filament recipe management in the browser (add/edit/delete recipe lines).
- Grams-per-unit recipes for multicolor products.
- Production filament forecasting across all planned and active jobs.
- Live Spoolman remaining weight is used for internal filaments mapped with `spoolman_filament_id`.
- If Spoolman is unavailable or a filament is not mapped, the forecast estimates grams from internal sealed/open spool counts.
- Each internal filament now has a nominal spool size in grams (default 1000 g) for fallback forecasting.
- Spoolman rows show both spool ID (`S#`) and filament definition ID (`F#`). Use the `F#` value when mapping an internal filament to Spoolman.

### Recipe setup example

If one Long Dragon uses 180 g of black PLA and 25 g of white PLA, create two recipe lines for that product:

- Black PLA: 180 g / unit, sequence 1
- White PLA: 25 g / unit, sequence 2

A production job for 5 units then forecasts 900 g black and 125 g white.

### Upgrade

Keep your existing `.env` and Postgres volume, then rebuild:

```bash
docker compose up -d --build
```

Migration `004_recipes_forecast.sql` is applied automatically. It does not reset existing data.

### Important note about older completed production jobs

Production jobs completed before Phase 3.4 did not store the job ID on the automatic inventory transaction. Deleting one of those older completed jobs cannot reliably identify and reverse that old stock transaction. New completions from Phase 3.4 onward are linked and can be rolled back when the job is deleted.

## Phase 3.4.1 recipe modal fix

- Recipe filament dropdown now includes connected Spoolman filament definitions as well as internal filament records.
- Choosing a Spoolman-only filament automatically creates or updates the internal mapping required by recipes and forecasting.
- Cancel and X buttons now close the modal without triggering browser required-field validation.
- Save now explicitly validates required fields before submitting.


## Phase 3.7 - Phase 3 completion

This release completes the Phase 3 browser experience with:

- Actionable dashboard alerts for stock shortages, filament shortages, unmatched Square rows, and vendor-fair preparation.
- Search, filtering, and sorting on Inventory, Production, Filament, Projects, Vendor Fairs, and Square Sales.
- Mobile-focused navigation and layouts, touch-friendly controls, responsive dialogs, and scrollable data tables.
- No database migration is required for this release. Preserve the existing `.env` and Postgres volume, then rebuild with `docker compose up -d --build`.

## Phase 4.1 - Backup, Restore & Admin

Phase 4.1 adds an Admin page for database safety and operational settings.

### What is included

- Nightly PostgreSQL custom-format backups (default 3:00 AM local time)
- Configurable backup retention (default 30 days)
- Backup health warning on the Dashboard
- **Back Up Now** from the Admin page
- Backup history with timestamp and file size
- Download and delete backup files
- Guarded database restore from a `.dump` file
- Automatic safety backup immediately before every restore
- Automatic schema migration after restoring an older database
- Application settings stored in PostgreSQL:
  - Business name
  - Currency
  - Timezone
  - Default spool weight
  - Backup retention days
  - Backup warning threshold

### Upgrade from Phase 3.7

Keep your existing `.env` and Docker `postgres_data` volume. Replace the project files with this version, then add these values to `.env` if they are not already present:

```env
POSTGRES_HOST=db
TZ=America/New_York
BACKUP_CRON="0 3 * * *"
BACKUP_RETENTION_DAYS=30
BACKUP_DIR=/backups
```

Then rebuild:

```bash
docker compose down
docker compose up -d --build
```

Do **not** run `docker compose down -v`; `-v` deletes the PostgreSQL volume.

After startup, open the new **Admin** page. Migration `007_admin_backup.sql` applies automatically.

### Backup files

Backups are stored in the project's `./backups` directory on the Docker host. Because this is a bind mount, the backup files remain available even if the app container is rebuilt.

For stronger disaster recovery, periodically copy the `backups` directory to another computer, NAS, or cloud-storage location. A backup stored only on the same physical disk as PostgreSQL does not protect against disk failure.

### Restore safety

Restore requires selecting a `.dump` file and typing `RESTORE` exactly. Immediately before restoring, CFT Inventory creates a `pre_restore_*.dump` safety snapshot. After a successful restore, database migrations are automatically reapplied so an older backup can be opened by the current application version.

## Phase 4.3 - Chronological Fair Planning + Square Reconciliation

Phase 4.3 adds:
- optional selling start/end times on vendor fairs
- Square reconciliation preview based on each event's selling window
- confirmation that links existing Square rows to a fair without creating a second inventory deduction
- manual assignment of nearby Square rows that fall outside the configured event window
- planned vs sold, returned quantity, and sell-through reporting
- chronological reservation of current stock + in-production units across upcoming fairs
- combined upcoming production requirements

### Upgrade
Keep your existing `.env`, `backups/`, and Postgres volume, then rebuild:

```bash
docker compose down
docker compose up -d --build
```

Migration `009_chronological_fair_planning.sql` is applied automatically.

For best Square matching, edit each fair and enter its actual selling start/end time. If times are omitted, the full event date(s) are used. Reconciliation never creates inventory transactions; the Square CSV import remains the only Square-driven inventory deduction.

## Square import cleanup patch

The Square Sales page now supports cleanup of mistaken CSV imports.

- **Delete upload** removes only Square rows first introduced by that import batch.
- Inventory transactions created by those rows are deleted at the same time, restoring stock automatically.
- Vendor-fair assignments attached to deleted Square rows are removed by cascade.
- Individual Square rows can also be deleted from **Recent Square rows**.
- A preview is shown before deleting an entire batch so you can see how many Square rows and inventory transactions will be removed.
- Migration `010_square_cleanup_and_dedupe.sql` converts the Square and inventory `source_key` indexes to full unique indexes so `ON CONFLICT(source_key)` works reliably.

Before deleting a large mistaken import, create a database backup from **Admin → Back Up Now**.

## Phase 4.3 - Chronological fair planning

Phase 4.3 expands the Vendor Fairs planner so future events are evaluated in date order instead of independently.

Each fair now has an **Inventory planning mode**:

- **Conservative** - assumes 100% of the planned event inventory sells. This is the safest setting and the default.
- **Historical** - uses the aggregate sell-through from previously reconciled fairs of the same event type. Only Square sales that match planned products are used. If there is no usable history yet, the planner falls back to 100% sell-through.
- **Custom** - uses a sell-through percentage entered for that event.

The chronological plan starts with current stock plus quantities already in production. For each upcoming fair it calculates:

- inventory available before the event,
- the event target,
- expected sales,
- expected returned/unsold inventory,
- units still needing production,
- projected inventory carried into the next fair.

The Vendor Fairs page also includes an **Upcoming production requirements** table with the next deadline, the units required in the next 30 days, and total requirements across all currently scheduled fairs.

Square reconciliation continues to be the source of truth after an event. Once sales are imported and the fair is reconciled, those actual sales are already reflected in current inventory and become eligible historical data for future fair forecasts.

### Upgrade

Keep your existing `.env`, `backups/` directory, and PostgreSQL Docker volume. Then rebuild normally:

```bash
docker compose up -d --build
```

Migration `011_phase43_sellthrough_planning.sql` adds the planning-mode fields automatically. No database reset is required.

## Phase 4.4 - Production Planning
Products now support a manual print profile: print time per batch and units per batch. The Production page combines chronological fair shortages with those profiles to calculate required batches, estimated printer-hours, deadlines, and a Print Next queue. Manual capacity groups model available printer-hours without connecting to printers. Edit a product to set its print profile, then edit the default General printers capacity group to match the fleet you want available for planning.
