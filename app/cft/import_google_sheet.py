"""Import core INVENTORY and FILAMENT catalog data from the CFT Google Sheet.

Run from an environment with DATABASE_URL, GOOGLE_SHEET_ID and
GOOGLE_SERVICE_ACCOUNT_FILE set. Share the Sheet read-only with the service
account email before running.
"""
import os
from datetime import date
from decimal import Decimal
import psycopg
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

SHEET_ID = os.environ["GOOGLE_SHEET_ID"]
CREDS = os.environ["GOOGLE_SERVICE_ACCOUNT_FILE"]
DB = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")
BASELINE_DATE = date(2026, 8, 11)  # Verify against Sheet settings before production cutover.

creds = Credentials.from_service_account_file(CREDS, scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
svc = build("sheets", "v4", credentials=creds, cache_discovery=False)

def values(tab_range):
    return svc.spreadsheets().values().get(spreadsheetId=SHEET_ID, range=tab_range).execute().get("values", [])

def money(v):
    if not v: return None
    return Decimal(str(v).replace("$", "").replace(",", ""))

def integer(v, default=0):
    try: return int(float(v))
    except (ValueError, TypeError): return default

def text(row, idx):
    return str(row[idx]).strip() if idx < len(row) else ""

def import_inventory(conn):
    rows = values("INVENTORY!A1:O")
    for source_row, row in enumerate(rows[1:], start=2):
        family, variant = text(row,3), text(row,4)
        if not family or not variant: continue
        location, sublocation = text(row,0), text(row,1)
        sku = text(row,2) or None
        baseline = integer(text(row,5))
        target = integer(text(row,7))
        price = money(text(row,8))
        etsy = text(row,9).upper() == "TRUE"
        in_prod = integer(text(row,10))
        priority = (text(row,12) or "low").lower()
        if priority not in {"low","medium","high"}: priority="low"
        notes = text(row,14) or None
        product_id = conn.execute("""
          INSERT INTO products(product_family,variant,square_sku,price,target_stock,priority,etsy_enabled,notes)
          VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
          ON CONFLICT(product_family,variant) DO UPDATE SET
            square_sku=EXCLUDED.square_sku, price=EXCLUDED.price, target_stock=EXCLUDED.target_stock,
            priority=EXCLUDED.priority, etsy_enabled=EXCLUDED.etsy_enabled, notes=EXCLUDED.notes
          RETURNING id
        """, (family,variant,sku,price,target,priority,etsy,notes)).fetchone()[0]
        location_id = None
        if location:
            location_id = conn.execute("""
              INSERT INTO inventory_locations(location_name,sub_location) VALUES (%s,%s)
              ON CONFLICT(location_name,sub_location) DO UPDATE SET location_name=EXCLUDED.location_name RETURNING id
            """, (location,sublocation)).fetchone()[0]
        conn.execute("""
          INSERT INTO inventory_baselines(product_id,location_id,baseline_date,quantity,source_row)
          VALUES (%s,%s,%s,%s,%s)
          ON CONFLICT(product_id,location_id,baseline_date) DO UPDATE SET quantity=EXCLUDED.quantity,source_row=EXCLUDED.source_row
        """, (product_id,location_id,BASELINE_DATE,baseline,source_row))
        if in_prod > 0:
            exists = conn.execute("SELECT 1 FROM production_jobs WHERE product_id=%s AND status IN ('planned','active')", (product_id,)).fetchone()
            if not exists:
                conn.execute("INSERT INTO production_jobs(product_id,quantity_planned,status,priority,notes) VALUES (%s,%s,'active',%s,'Imported from INVENTORY in-production quantity')", (product_id,in_prod,priority))

def import_filaments(conn):
    rows = values("FILAMENT!A1:K")
    for row in rows[1:]:
        brand, material, color = text(row,0), text(row,1), text(row,2)
        if not brand or not material or not color: continue
        minimum = Decimal(text(row,6) or "0")
        notes = text(row,8) or None
        sealed = Decimal(text(row,9) or "0")
        opened = Decimal(text(row,10) or "0")
        filament_id = conn.execute("""
          INSERT INTO filaments(brand,material_line,color,minimum_spools,notes)
          VALUES (%s,%s,%s,%s,%s)
          ON CONFLICT(brand,material_line,color) DO UPDATE SET minimum_spools=EXCLUDED.minimum_spools,notes=EXCLUDED.notes
          RETURNING id
        """, (brand,material,color,minimum,notes)).fetchone()[0]
        conn.execute("""
          INSERT INTO filament_baselines(filament_id,baseline_date,sealed_spools,open_spools)
          VALUES (%s,%s,%s,%s)
          ON CONFLICT(filament_id,baseline_date) DO UPDATE SET sealed_spools=EXCLUDED.sealed_spools,open_spools=EXCLUDED.open_spools
        """, (filament_id,BASELINE_DATE,sealed,opened))

with psycopg.connect(DB) as conn:
    import_inventory(conn)
    import_filaments(conn)
    conn.commit()
print("Core Sheet import complete.")
