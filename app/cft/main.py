from contextlib import asynccontextmanager
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo
import csv
import hashlib
import io
import re
from uuid import UUID

from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from pydantic import BaseModel, Field
from psycopg.types.json import Jsonb

from .config import settings
from .db import connect
from .migrations import apply_migrations
from .spoolman import SpoolmanClient

Priority = Literal['low', 'medium', 'high']
InventoryEvent = Literal['production_started', 'production_completed', 'order_received', 'sale', 'damaged', 'adjustment']
FilamentEvent = Literal['order_placed', 'order_received', 'spool_opened', 'used', 'sealed_adjustment', 'open_adjustment']
ProjectStatus = Literal['backlog', 'active', 'paused', 'repair', 'completed', 'cancelled']
ProductionStatus = Literal['planned', 'active', 'completed', 'cancelled']


class ProductCreate(BaseModel):
    product_family: str
    variant: str
    square_sku: str | None = None
    price: Decimal | None = None
    target_stock: int = Field(default=0, ge=0)
    priority: Priority = 'low'
    etsy_enabled: bool = False
    notes: str | None = None


class InventoryBaselineCreate(BaseModel):
    product_id: UUID
    location_name: str
    sub_location: str = ''
    baseline_date: date
    quantity: int


class InventoryTransactionCreate(BaseModel):
    product_id: UUID
    event_type: InventoryEvent
    quantity: int
    transaction_at: datetime | None = None
    location_name: str | None = None
    sub_location: str = ''
    price_per: Decimal | None = None
    source: str | None = 'manual'
    notes: str | None = None


class FilamentCreate(BaseModel):
    brand: str
    material_line: str
    color: str
    minimum_spools: Decimal = Field(default=Decimal('0'), ge=0)
    minimum_grams: Decimal = Field(default=Decimal('0'), ge=0)
    spoolman_filament_id: int | None = None
    notes: str | None = None
    nominal_spool_weight_grams: Decimal = Field(default=Decimal('1000'), gt=0)


class FilamentBaselineCreate(BaseModel):
    filament_id: UUID
    baseline_date: date
    sealed_spools: Decimal = Decimal('0')
    open_spools: Decimal = Decimal('0')


class FilamentTransactionCreate(BaseModel):
    filament_id: UUID
    event_type: FilamentEvent
    quantity: Decimal
    transaction_at: datetime | None = None
    project_source: str | None = None
    notes: str | None = None


class ProductionJobCreate(BaseModel):
    product_id: UUID
    quantity_planned: int = Field(gt=0)
    quantity_completed: int = Field(default=0, ge=0)
    status: ProductionStatus = 'planned'
    priority: Priority = 'medium'
    started_at: datetime | None = None
    completed_at: datetime | None = None
    notes: str | None = None


class ProjectCreate(BaseModel):
    name: str
    project_type: str | None = None
    designer_source: str | None = None
    status: ProjectStatus = 'backlog'
    priority: Priority = 'medium'
    started_date: date | None = None
    progress_percent: Decimal | None = Field(default=None, ge=0, le=100)
    physical_location: str | None = None
    next_step: str | None = None
    reason: str | None = None
    notes: str | None = None


class RecipeCreate(BaseModel):
    product_id: UUID
    filament_id: UUID
    sequence: int = Field(default=1, ge=1)
    grams_per_unit: Decimal | None = Field(default=None, ge=0)
    notes: str | None = None


class RecipeUpdate(BaseModel):
    filament_id: UUID | None = None
    sequence: int | None = Field(default=None, ge=1)
    grams_per_unit: Decimal | None = Field(default=None, ge=0)
    notes: str | None = None


def rows(cur):
    cols = [d.name for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def one(cur):
    row = cur.fetchone()
    if not row:
        return None
    cols = [d.name for d in cur.description]
    return dict(zip(cols, row))


def location_id(conn, name: str | None, sub_location: str = ''):
    if not name:
        return None
    cur = conn.execute(
        '''INSERT INTO inventory_locations(location_name, sub_location)
           VALUES (%s,%s)
           ON CONFLICT (location_name, sub_location)
           DO UPDATE SET location_name=EXCLUDED.location_name
           RETURNING id''',
        (name, sub_location),
    )
    return cur.fetchone()[0]


@asynccontextmanager
async def lifespan(app: FastAPI):
    apply_migrations()
    yield


app = FastAPI(title='CFT Inventory', version='1.0.0', lifespan=lifespan)


@app.get('/health')
def health():
    with connect() as conn:
        conn.execute('SELECT 1')
    return {
        'status': 'ok',
        'database': 'connected',
        'spoolman_enabled': settings.spoolman_enabled,
        'version': '1.0.0',
    }


@app.get('/api/products')
def products():
    with connect() as conn:
        return rows(conn.execute('SELECT * FROM products ORDER BY product_family, variant'))


@app.post('/api/products', status_code=201)
def create_product(data: ProductCreate):
    try:
        with connect() as conn:
            cur = conn.execute(
                '''INSERT INTO products(product_family,variant,square_sku,price,target_stock,priority,etsy_enabled,notes)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                   RETURNING *''',
                (data.product_family.strip(), data.variant.strip(), data.square_sku or None, data.price,
                 data.target_stock, data.priority, data.etsy_enabled, data.notes),
            )
            result = one(cur)
            conn.commit()
            return result
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get('/api/inventory')
def inventory():
    with connect() as conn:
        return rows(conn.execute(
            '''SELECT r.*, p.square_sku
               FROM product_restock_status r
               JOIN products p ON p.id = r.product_id
               ORDER BY r.priority DESC, r.product_family, r.variant'''
        ))


@app.post('/api/inventory/baselines', status_code=201)
def create_inventory_baseline(data: InventoryBaselineCreate):
    try:
        with connect() as conn:
            loc = location_id(conn, data.location_name, data.sub_location)
            cur = conn.execute(
                '''INSERT INTO inventory_baselines(product_id,location_id,baseline_date,quantity)
                   VALUES (%s,%s,%s,%s)
                   ON CONFLICT(product_id,location_id,baseline_date)
                   DO UPDATE SET quantity=EXCLUDED.quantity
                   RETURNING *''',
                (data.product_id, loc, data.baseline_date, data.quantity),
            )
            result = one(cur)
            conn.commit()
            return result
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get('/api/inventory/transactions')
def inventory_transactions():
    with connect() as conn:
        return rows(conn.execute(
            '''SELECT t.*,p.product_family,p.variant,l.location_name,l.sub_location
               FROM inventory_transactions t
               JOIN products p ON p.id=t.product_id
               LEFT JOIN inventory_locations l ON l.id=t.location_id
               ORDER BY t.transaction_at DESC'''))


@app.get('/api/products/{product_id}/history')
def product_history(product_id: UUID):
    with connect() as conn:
        product = one(conn.execute(
            '''SELECT p.*, r.system_stock, r.in_production, r.need
               FROM products p
               LEFT JOIN product_restock_status r ON r.product_id=p.id
               WHERE p.id=%s''',
            (product_id,),
        ))
        if not product:
            raise HTTPException(status_code=404, detail='Product not found')
        history = rows(conn.execute(
            '''SELECT * FROM (
                 SELECT b.id, b.baseline_date::timestamptz AS occurred_at,
                        'starting_inventory'::text AS event_type, b.quantity,
                        NULL::numeric AS price_per, 'baseline'::text AS source,
                        NULL::text AS source_reference,
                        l.location_name, l.sub_location,
                        'Starting inventory'::text AS notes
                 FROM inventory_baselines b
                 LEFT JOIN inventory_locations l ON l.id=b.location_id
                 WHERE b.product_id=%s
                 UNION ALL
                 SELECT t.id, t.transaction_at AS occurred_at, t.event_type::text, t.quantity,
                        t.price_per, t.source, t.source_reference,
                        l.location_name, l.sub_location, t.notes
                 FROM inventory_transactions t
                 LEFT JOIN inventory_locations l ON l.id=t.location_id
                 WHERE t.product_id=%s
               ) h
               ORDER BY occurred_at DESC, id DESC''',
            (product_id, product_id),
        ))
        return {'product': product, 'history': history}


@app.post('/api/inventory/transactions', status_code=201)
def create_inventory_transaction(data: InventoryTransactionCreate):
    try:
        with connect() as conn:
            loc = location_id(conn, data.location_name, data.sub_location)
            cur = conn.execute(
                '''INSERT INTO inventory_transactions(product_id,location_id,transaction_at,event_type,quantity,price_per,source,notes)
                   VALUES (%s,%s,COALESCE(%s,now()),%s,%s,%s,%s,%s)
                   RETURNING *''',
                (data.product_id, loc, data.transaction_at, data.event_type, data.quantity,
                 data.price_per, data.source, data.notes),
            )
            result = one(cur)
            conn.commit()
            return result
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get('/api/filament')
def filament():
    with connect() as conn:
        return rows(conn.execute('SELECT * FROM filament_stock ORDER BY brand,material_line,color'))


@app.get('/api/filaments')
def filaments():
    with connect() as conn:
        return rows(conn.execute('SELECT * FROM filaments ORDER BY brand,material_line,color'))


@app.post('/api/filaments', status_code=201)
def create_filament(data: FilamentCreate):
    try:
        with connect() as conn:
            cur = conn.execute(
                '''INSERT INTO filaments(brand,material_line,color,minimum_spools,minimum_grams,spoolman_filament_id,notes,nominal_spool_weight_grams)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                   RETURNING *''',
                (data.brand.strip(), data.material_line.strip(), data.color.strip(), data.minimum_spools,
                 data.minimum_grams, data.spoolman_filament_id, data.notes, data.nominal_spool_weight_grams),
            )
            result = one(cur)
            conn.commit()
            return result
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post('/api/filament/baselines', status_code=201)
def create_filament_baseline(data: FilamentBaselineCreate):
    try:
        with connect() as conn:
            cur = conn.execute(
                '''INSERT INTO filament_baselines(filament_id,baseline_date,sealed_spools,open_spools)
                   VALUES (%s,%s,%s,%s)
                   ON CONFLICT(filament_id,baseline_date)
                   DO UPDATE SET sealed_spools=EXCLUDED.sealed_spools,open_spools=EXCLUDED.open_spools
                   RETURNING *''',
                (data.filament_id, data.baseline_date, data.sealed_spools, data.open_spools),
            )
            result = one(cur)
            conn.commit()
            return result
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get('/api/filament/transactions')
def filament_transactions():
    with connect() as conn:
        return rows(conn.execute(
            '''SELECT t.*,f.brand,f.material_line,f.color
               FROM filament_transactions t JOIN filaments f ON f.id=t.filament_id
               ORDER BY t.transaction_at DESC'''))


@app.post('/api/filament/transactions', status_code=201)
def create_filament_transaction(data: FilamentTransactionCreate):
    try:
        with connect() as conn:
            cur = conn.execute(
                '''INSERT INTO filament_transactions(filament_id,transaction_at,event_type,quantity,project_source,notes)
                   VALUES (%s,COALESCE(%s,now()),%s,%s,%s,%s)
                   RETURNING *''',
                (data.filament_id, data.transaction_at, data.event_type, data.quantity, data.project_source, data.notes),
            )
            result = one(cur)
            conn.commit()
            return result
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get('/api/production')
def production():
    with connect() as conn:
        return rows(conn.execute(
            '''SELECT j.id,p.product_family,p.variant,j.quantity_planned,j.quantity_completed,j.status,j.priority,
                      j.started_at,j.completed_at,j.notes
               FROM production_jobs j JOIN products p ON p.id=j.product_id
               ORDER BY CASE j.status WHEN 'active' THEN 0 WHEN 'planned' THEN 1 ELSE 2 END,
                        j.priority DESC,j.started_at NULLS LAST'''))


@app.post('/api/production', status_code=201)
def create_production_job(data: ProductionJobCreate):
    try:
        with connect() as conn:
            cur = conn.execute(
                '''INSERT INTO production_jobs(product_id,quantity_planned,quantity_completed,status,priority,started_at,completed_at,notes)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                   RETURNING *''',
                (data.product_id, data.quantity_planned, data.quantity_completed, data.status, data.priority,
                 data.started_at, data.completed_at, data.notes),
            )
            result = one(cur)
            conn.commit()
            return result
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get('/api/projects')
def projects():
    with connect() as conn:
        return rows(conn.execute('SELECT * FROM projects ORDER BY status,priority DESC,name'))


@app.post('/api/projects', status_code=201)
def create_project(data: ProjectCreate):
    try:
        with connect() as conn:
            cur = conn.execute(
                '''INSERT INTO projects(name,project_type,designer_source,status,priority,started_date,progress_percent,
                                        physical_location,next_step,reason,notes,source_system)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'manual')
                   RETURNING *''',
                (data.name, data.project_type, data.designer_source, data.status, data.priority, data.started_date,
                 data.progress_percent, data.physical_location, data.next_step, data.reason, data.notes),
            )
            result = one(cur)
            conn.commit()
            return result
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get('/api/recipes')
def recipes():
    with connect() as conn:
        return rows(conn.execute(
            '''SELECT r.*,p.product_family,p.variant,f.brand,f.material_line,f.color
               FROM product_filament_recipes r
               JOIN products p ON p.id=r.product_id
               JOIN filaments f ON f.id=r.filament_id
               ORDER BY p.product_family,p.variant,r.sequence'''))


@app.post('/api/recipes', status_code=201)
def create_recipe(data: RecipeCreate):
    try:
        with connect() as conn:
            cur = conn.execute(
                '''INSERT INTO product_filament_recipes(product_id,filament_id,sequence,grams_per_unit,notes)
                   VALUES (%s,%s,%s,%s,%s)
                   ON CONFLICT(product_id,filament_id)
                   DO UPDATE SET sequence=EXCLUDED.sequence,grams_per_unit=EXCLUDED.grams_per_unit,notes=EXCLUDED.notes
                   RETURNING *''',
                (data.product_id, data.filament_id, data.sequence, data.grams_per_unit, data.notes),
            )
            result = one(cur)
            conn.commit()
            return result
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get('/api/metrics')
def metrics():
    with connect() as conn:
        return one(conn.execute('SELECT * FROM inventory_metrics')) or {}


@app.get('/api/square/unmatched')
def square_unmatched():
    with connect() as conn:
        return rows(conn.execute('SELECT * FROM unmatched_square_sales ORDER BY transaction_at DESC NULLS LAST'))


@app.get('/api/square/imports')
def square_imports():
    with connect() as conn:
        return rows(conn.execute('SELECT * FROM square_import_batches ORDER BY imported_at DESC LIMIT 50'))


@app.get('/api/square/transactions')
def square_transactions(limit: int = 100):
    limit = max(1, min(limit, 500))
    with connect() as conn:
        return rows(conn.execute(
            '''SELECT s.*, p.product_family, p.variant
               FROM square_transactions s
               LEFT JOIN products p ON p.square_sku=s.sku
               ORDER BY s.transaction_at DESC NULLS LAST, s.imported_at DESC
               LIMIT %s''', (limit,)))


def _refresh_fair_reconciliation_state(conn, fair_ids):
    """Recalculate reconciliation status/revenue after Square assignments are removed."""
    for fair_id in set(fair_ids or []):
        if not fair_id:
            continue
        summary = conn.execute(
            """SELECT COUNT(*)::int, COALESCE(SUM(s.net_sales),0)
               FROM vendor_fair_square_assignments a
               JOIN square_transactions s ON s.id=a.square_transaction_id
               WHERE a.vendor_fair_id=%s""",
            (fair_id,)
        ).fetchone()
        count, total = summary
        if count == 0:
            conn.execute(
                """UPDATE vendor_fairs
                   SET reconciliation_status='not_reconciled', reconciled_at=NULL,
                       revenue=0, updated_at=now()
                   WHERE id=%s""",
                (fair_id,)
            )
        else:
            conn.execute(
                """UPDATE vendor_fairs
                   SET revenue=%s, updated_at=now()
                   WHERE id=%s""",
                (total, fair_id)
            )


@app.get('/api/square/imports/{batch_id}/preview-delete')
def square_import_delete_preview(batch_id: UUID):
    with connect() as conn:
        batch = one(conn.execute('SELECT * FROM square_import_batches WHERE id=%s', (batch_id,)))
        if not batch:
            raise HTTPException(status_code=404, detail='Square import batch not found')
        summary = one(conn.execute(
            '''SELECT
                 COUNT(*)::int AS square_rows,
                 COALESCE(SUM(ABS(s.quantity)),0) AS units,
                 COALESCE(SUM(s.net_sales),0) AS net_sales,
                 COUNT(DISTINCT a.id)::int AS fair_assignments,
                 COUNT(DISTINCT it.id)::int AS inventory_transactions
               FROM square_transactions s
               LEFT JOIN vendor_fair_square_assignments a ON a.square_transaction_id=s.id
               LEFT JOIN inventory_transactions it
                 ON it.source='square' AND it.source_key=('square-sale:' || s.source_key)
               WHERE s.import_batch_id=%s''',
            (batch_id,)
        )) or {}
        return {'batch': batch, **summary}


@app.delete('/api/square/imports/{batch_id}')
def delete_square_import(batch_id: UUID):
    with connect() as conn:
        batch = one(conn.execute('SELECT * FROM square_import_batches WHERE id=%s FOR UPDATE', (batch_id,)))
        if not batch:
            raise HTTPException(status_code=404, detail='Square import batch not found')

        tx_rows = rows(conn.execute(
            'SELECT id, source_key FROM square_transactions WHERE import_batch_id=%s',
            (batch_id,)
        ))
        affected_fairs = [r[0] for r in conn.execute(
            '''SELECT DISTINCT a.vendor_fair_id
               FROM vendor_fair_square_assignments a
               JOIN square_transactions s ON s.id=a.square_transaction_id
               WHERE s.import_batch_id=%s''', (batch_id,)
        ).fetchall()]
        source_keys = [x['source_key'] for x in tx_rows if x.get('source_key')]

        inventory_deleted = 0
        if source_keys:
            result = conn.execute(
                '''DELETE FROM inventory_transactions
                   WHERE source='square'
                     AND source_key = ANY(%s)''',
                ([f'square-sale:{key}' for key in source_keys],)
            )
            inventory_deleted = result.rowcount or 0

        # Fair assignments are removed automatically by ON DELETE CASCADE.
        square_deleted = conn.execute(
            'DELETE FROM square_transactions WHERE import_batch_id=%s',
            (batch_id,)
        ).rowcount or 0
        conn.execute('DELETE FROM square_import_batches WHERE id=%s', (batch_id,))
        _refresh_fair_reconciliation_state(conn, affected_fairs)
        conn.commit()
        return {
            'deleted': True,
            'filename': batch['filename'],
            'square_rows_deleted': square_deleted,
            'inventory_transactions_deleted': inventory_deleted,
        }


@app.delete('/api/square/transactions/{transaction_id}')
def delete_square_transaction(transaction_id: UUID):
    with connect() as conn:
        tx = one(conn.execute(
            'SELECT id, source_key, item_name, sku FROM square_transactions WHERE id=%s FOR UPDATE',
            (transaction_id,)
        ))
        if not tx:
            raise HTTPException(status_code=404, detail='Square transaction not found')
        affected_fairs = [r[0] for r in conn.execute(
            'SELECT vendor_fair_id FROM vendor_fair_square_assignments WHERE square_transaction_id=%s',
            (transaction_id,)
        ).fetchall()]

        inventory_deleted = 0
        if tx.get('source_key'):
            inventory_deleted = conn.execute(
                '''DELETE FROM inventory_transactions
                   WHERE source='square' AND source_key=%s''',
                (f"square-sale:{tx['source_key']}",)
            ).rowcount or 0

        # Any fair assignment is removed by the FK cascade.
        conn.execute('DELETE FROM square_transactions WHERE id=%s', (transaction_id,))
        _refresh_fair_reconciliation_state(conn, affected_fairs)
        conn.commit()
        return {
            'deleted': True,
            'item_name': tx.get('item_name'),
            'sku': tx.get('sku'),
            'inventory_transactions_deleted': inventory_deleted,
        }


def _square_value(row: dict, *names: str) -> str:
    normalized = {re.sub(r'\s+', ' ', str(k or '').strip().lower()): (v or '') for k, v in row.items()}
    for name in names:
        value = normalized.get(re.sub(r'\s+', ' ', name.strip().lower()))
        if value is not None:
            return str(value).strip()
    return ''


def _square_decimal(value: str) -> Decimal:
    text = str(value or '').strip().replace('$', '').replace(',', '')
    if not text:
        return Decimal('0')
    if text.startswith('(') and text.endswith(')'):
        text = '-' + text[1:-1]
    try:
        return Decimal(text)
    except Exception:
        return Decimal('0')


def _square_timestamp(date_value: str, time_value: str) -> datetime | None:
    d = str(date_value or '').strip()
    t = str(time_value or '').strip()
    if not d:
        return None
    combined = f'{d} {t}'.strip()
    patterns = (
        '%m/%d/%y %I:%M %p', '%m/%d/%Y %I:%M %p',
        '%m/%d/%y %H:%M', '%m/%d/%Y %H:%M',
        '%m/%d/%y', '%m/%d/%Y',
        '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d',
    )
    for pattern in patterns:
        try:
            dt = datetime.strptime(combined, pattern)
            return dt.replace(tzinfo=ZoneInfo('America/New_York'))
        except ValueError:
            continue
    try:
        dt = datetime.fromisoformat(combined)
        return dt if dt.tzinfo else dt.replace(tzinfo=ZoneInfo('America/New_York'))
    except Exception:
        return None


def _square_row_key(row: dict, occurrence: int) -> str:
    fields = [
        _square_value(row, 'Date'), _square_value(row, 'Time'),
        _square_value(row, 'Transaction ID'), _square_value(row, 'Payment ID'),
        _square_value(row, 'SKU'), _square_value(row, 'Item'),
        _square_value(row, 'Qty'), _square_value(row, 'Gross Sales'),
        _square_value(row, 'Net Sales'), _square_value(row, 'Event Type'),
        _square_value(row, 'Modifiers Applied'),
    ]
    raw = '\x1f'.join(fields)
    digest = hashlib.sha256(raw.encode('utf-8')).hexdigest()[:32]
    return f'square:{digest}:{occurrence}'


@app.post('/api/square/upload')
async def square_upload(
    file: UploadFile = File(...),
    apply_inventory_from: date | None = Form(None),
):
    filename = file.filename or 'square-export.csv'
    if not filename.lower().endswith('.csv'):
        raise HTTPException(status_code=400, detail='Please upload a Square CSV export.')
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail='The uploaded CSV is empty.')
    try:
        text = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        try:
            text = raw.decode('cp1252')
        except UnicodeDecodeError as exc:
            raise HTTPException(status_code=400, detail=f'Unable to read CSV encoding: {exc}')

    reader = csv.DictReader(io.StringIO(text))
    headers = [str(x or '').strip() for x in (reader.fieldnames or [])]
    required = {'date', 'item', 'qty'}
    normalized_headers = {h.lower() for h in headers}
    missing = sorted(required - normalized_headers)
    if missing:
        raise HTTPException(status_code=400, detail=f'CSV does not look like a Square itemized-sales export. Missing: {", ".join(missing)}')

    counts = {
        'rows_seen': 0, 'rows_imported': 0, 'rows_duplicate': 0,
        'rows_matched': 0, 'rows_unmatched': 0,
        'inventory_transactions_created': 0, 'rows_skipped': 0,
    }
    fingerprint_counts: dict[str, int] = {}

    with connect() as conn:
        batch_id = conn.execute(
            '''INSERT INTO square_import_batches(filename,apply_inventory_from)
               VALUES (%s,%s) RETURNING id''', (filename, apply_inventory_from)
        ).fetchone()[0]

        for row in reader:
            counts['rows_seen'] += 1
            item_name = _square_value(row, 'Item')
            qty = _square_decimal(_square_value(row, 'Qty'))
            if not item_name or qty == 0 or item_name.strip().upper() == 'CFT SALE':
                counts['rows_skipped'] += 1
                continue

            fingerprint = _square_row_key(row, 0).rsplit(':', 1)[0]
            fingerprint_counts[fingerprint] = fingerprint_counts.get(fingerprint, 0) + 1
            source_key = f'{fingerprint}:{fingerprint_counts[fingerprint]}'

            transaction_at = _square_timestamp(_square_value(row, 'Date'), _square_value(row, 'Time'))
            sku = _square_value(row, 'SKU') or None
            transaction_id = _square_value(row, 'Transaction ID') or None
            payment_id = _square_value(row, 'Payment ID') or None
            gross_sales = _square_decimal(_square_value(row, 'Gross Sales'))
            discounts = _square_decimal(_square_value(row, 'Discounts'))
            net_sales = _square_decimal(_square_value(row, 'Net Sales'))
            tax = _square_decimal(_square_value(row, 'Tax'))

            inserted = conn.execute(
                '''INSERT INTO square_transactions(
                     transaction_id,payment_id,transaction_at,sku,item_name,quantity,
                     gross_sales,discounts,net_sales,tax,channel,location,raw_data,
                     source_key,import_batch_id,category,price_point_name,modifiers_applied
                   ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT(source_key) DO NOTHING
                   RETURNING id''',
                (
                    transaction_id, payment_id, transaction_at, sku, item_name, qty,
                    gross_sales, discounts, net_sales, tax,
                    _square_value(row, 'Channel') or None,
                    _square_value(row, 'Location') or None,
                    Jsonb(row), source_key, batch_id,
                    _square_value(row, 'Category') or None,
                    _square_value(row, 'Price Point Name') or None,
                    _square_value(row, 'Modifiers Applied') or None,
                )
            ).fetchone()
            if not inserted:
                counts['rows_duplicate'] += 1
            else:
                counts['rows_imported'] += 1

            # Even duplicate raw rows continue through matching. This allows a
            # later re-upload with an earlier inventory cutoff to create a
            # previously omitted stock transaction without duplicating it.
            product = None
            if sku:
                product = conn.execute('SELECT id FROM products WHERE square_sku=%s', (sku,)).fetchone()
            if product:
                counts['rows_matched'] += 1
                integral_qty = qty.to_integral_value()
                can_apply = qty == integral_qty and transaction_at is not None
                if apply_inventory_from is not None and transaction_at is not None:
                    can_apply = can_apply and transaction_at.date() >= apply_inventory_from
                if can_apply:
                    stock_delta = -int(integral_qty)
                    if stock_delta != 0:
                        result = conn.execute(
                            '''INSERT INTO inventory_transactions(
                                 product_id,transaction_at,event_type,quantity,price_per,source,
                                 source_reference,source_key,notes
                               ) VALUES (%s,%s,'sale',%s,%s,'square',%s,%s,%s)
                               ON CONFLICT(source_key) DO NOTHING RETURNING id''',
                            (
                                product[0], transaction_at, stock_delta,
                                (net_sales / qty if qty != 0 else None),
                                transaction_id or payment_id,
                                f'square-sale:{source_key}',
                                f'Square CSV: {item_name}',
                            )
                        ).fetchone()
                        if result:
                            counts['inventory_transactions_created'] += 1
            else:
                counts['rows_unmatched'] += 1

        conn.execute(
            '''UPDATE square_import_batches SET
                 rows_seen=%s,rows_imported=%s,rows_duplicate=%s,rows_matched=%s,
                 rows_unmatched=%s,inventory_transactions_created=%s,rows_skipped=%s
               WHERE id=%s''',
            (
                counts['rows_seen'], counts['rows_imported'], counts['rows_duplicate'],
                counts['rows_matched'], counts['rows_unmatched'],
                counts['inventory_transactions_created'], counts['rows_skipped'], batch_id,
            )
        )
        conn.commit()

    return {
        'batch_id': batch_id,
        'filename': filename,
        'apply_inventory_from': apply_inventory_from,
        **counts,
    }


@app.get('/api/spoolman/health')
async def spoolman_health():
    if not settings.spoolman_enabled:
        return {'enabled': False}
    try:
        return {'enabled': True, **await SpoolmanClient().health()}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f'Spoolman connection failed: {exc}')


@app.get('/api/spoolman/spools')
async def spoolman_spools():
    if not settings.spoolman_enabled:
        raise HTTPException(status_code=409, detail='Spoolman integration is disabled')
    return await SpoolmanClient().spools()


@app.get('/api/spoolman/inventory')
async def spoolman_inventory():
    if not settings.spoolman_enabled:
        raise HTTPException(status_code=409, detail='Spoolman integration is disabled')
    try:
        return await SpoolmanClient().inventory()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f'Spoolman connection failed: {exc}')

# --- Phase 3 browser UI + update actions ---
from pathlib import Path
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

STATIC_DIR = Path(__file__).resolve().parent / 'static'
app.mount('/static', StaticFiles(directory=STATIC_DIR), name='static')


class ProductUpdate(BaseModel):
    product_family: str | None = None
    variant: str | None = None
    square_sku: str | None = None
    price: Decimal | None = None
    target_stock: int | None = Field(default=None, ge=0)
    priority: Priority | None = None
    etsy_enabled: bool | None = None
    notes: str | None = None
    active: bool | None = None
    print_time_minutes: Decimal | None = Field(default=None, ge=0)
    batch_size: int | None = Field(default=None, gt=0)


class ProductionJobUpdate(BaseModel):
    quantity_planned: int | None = Field(default=None, gt=0)
    quantity_completed: int | None = Field(default=None, ge=0)
    status: ProductionStatus | None = None
    priority: Priority | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    notes: str | None = None


class ProjectUpdate(BaseModel):
    project_type: str | None = None
    designer_source: str | None = None
    status: ProjectStatus | None = None
    priority: Priority | None = None
    started_date: date | None = None
    progress_percent: Decimal | None = Field(default=None, ge=0, le=100)
    physical_location: str | None = None
    next_step: str | None = None
    reason: str | None = None
    notes: str | None = None


class VendorFairCreate(BaseModel):
    name: str
    start_date: date
    end_date: date | None = None
    location: str | None = None
    booth_fee: Decimal = Field(default=Decimal('0'), ge=0)
    revenue: Decimal | None = Field(default=None, ge=0)
    event_type: str | None = None
    hours_open: Decimal | None = Field(default=None, ge=0)
    attendance_estimate: int | None = Field(default=None, ge=0)
    weather_conditions: str | None = None
    booth_location_quality: str | None = None
    would_return: bool | None = None
    notes: str | None = None
    selling_start_time: time | None = None
    selling_end_time: time | None = None
    planning_mode: str = 'conservative'
    custom_sell_through_percent: Decimal | None = Field(default=None, ge=0, le=100)


class VendorFairUpdate(BaseModel):
    name: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    location: str | None = None
    booth_fee: Decimal | None = Field(default=None, ge=0)
    revenue: Decimal | None = Field(default=None, ge=0)
    event_type: str | None = None
    hours_open: Decimal | None = Field(default=None, ge=0)
    attendance_estimate: int | None = Field(default=None, ge=0)
    weather_conditions: str | None = None
    booth_location_quality: str | None = None
    would_return: bool | None = None
    notes: str | None = None
    selling_start_time: time | None = None
    selling_end_time: time | None = None
    planning_mode: str | None = None
    custom_sell_through_percent: Decimal | None = Field(default=None, ge=0, le=100)


class VendorFairPlanItem(BaseModel):
    product_id: UUID
    target_quantity: int = Field(default=0, ge=0)
    notes: str | None = None


class FilamentUpdate(BaseModel):
    brand: str | None = None
    material_line: str | None = None
    color: str | None = None
    nominal_spool_weight_grams: Decimal | None = Field(default=None, gt=0)
    minimum_spools: Decimal | None = Field(default=None, ge=0)
    minimum_grams: Decimal | None = Field(default=None, ge=0)
    spoolman_filament_id: int | None = None
    notes: str | None = None
    active: bool | None = None


@app.get('/', include_in_schema=False)
def browser_app():
    return FileResponse(STATIC_DIR / 'index.html')


@app.patch('/api/products/{product_id}')
def update_product(product_id: UUID, data: ProductUpdate):
    payload = data.model_dump(exclude_unset=True)
    if not payload:
        raise HTTPException(status_code=400, detail='No fields supplied')
    allowed = set(ProductUpdate.model_fields)
    fields = []
    values = []
    for key, value in payload.items():
        if key not in allowed:
            continue
        fields.append(f'{key}=%s')
        values.append(value)
    fields.append('updated_at=now()')
    values.append(product_id)
    with connect() as conn:
        cur = conn.execute(f"UPDATE products SET {', '.join(fields)} WHERE id=%s RETURNING *", values)
        result = one(cur)
        if not result:
            raise HTTPException(status_code=404, detail='Product not found')
        conn.commit()
        return result


@app.patch('/api/filaments/{filament_id}')
def update_filament(filament_id: UUID, data: FilamentUpdate):
    payload = data.model_dump(exclude_unset=True)
    if not payload:
        raise HTTPException(status_code=400, detail='No fields supplied')
    fields, values = [], []
    for key, value in payload.items():
        fields.append(f'{key}=%s')
        values.append(value)
    values.append(filament_id)
    with connect() as conn:
        cur = conn.execute(f"UPDATE filaments SET {', '.join(fields)} WHERE id=%s RETURNING *", values)
        result = one(cur)
        if not result:
            raise HTTPException(status_code=404, detail='Filament not found')
        conn.commit()
        return result


@app.patch('/api/production/{job_id}')
def update_production_job(job_id: UUID, data: ProductionJobUpdate):
    payload = data.model_dump(exclude_unset=True)
    if not payload:
        raise HTTPException(status_code=400, detail='No fields supplied')
    if payload.get('status') == 'active' and 'started_at' not in payload:
        payload['started_at'] = datetime.now().astimezone()
    if payload.get('status') == 'completed' and 'completed_at' not in payload:
        payload['completed_at'] = datetime.now().astimezone()
    fields, values = [], []
    for key, value in payload.items():
        fields.append(f'{key}=%s')
        values.append(value)
    values.append(job_id)
    with connect() as conn:
        cur = conn.execute(f"UPDATE production_jobs SET {', '.join(fields)} WHERE id=%s RETURNING *", values)
        result = one(cur)
        if not result:
            raise HTTPException(status_code=404, detail='Production job not found')
        conn.commit()
        return result


@app.post('/api/production/{job_id}/complete')
def complete_production_job(job_id: UUID):
    with connect() as conn:
        job = one(conn.execute('SELECT * FROM production_jobs WHERE id=%s', (job_id,)))
        if not job:
            raise HTTPException(status_code=404, detail='Production job not found')
        remaining = max(job['quantity_planned'] - job['quantity_completed'], 0)
        if remaining:
            conn.execute(
                '''INSERT INTO inventory_transactions(product_id,transaction_at,event_type,quantity,source,source_reference,notes)
                   VALUES (%s,now(),'production_completed',%s,'production',%s,'Completed from production job')''',
                (job['product_id'], remaining, str(job_id)),
            )
        cur = conn.execute(
            '''UPDATE production_jobs
               SET quantity_completed=quantity_planned,status='completed',completed_at=now()
               WHERE id=%s RETURNING *''',
            (job_id,),
        )
        result = one(cur)
        conn.commit()
        return result


@app.delete('/api/production/{job_id}', status_code=204)
def delete_production_job(job_id: UUID):
    with connect() as conn:
        job = one(conn.execute('SELECT * FROM production_jobs WHERE id=%s', (job_id,)))
        if not job:
            raise HTTPException(status_code=404, detail='Production job not found')
        if job['status'] == 'completed':
            conn.execute(
                "DELETE FROM inventory_transactions WHERE source='production' AND source_reference=%s",
                (str(job_id),),
            )
        conn.execute('DELETE FROM production_jobs WHERE id=%s', (job_id,))
        conn.commit()
    return None


@app.patch('/api/recipes/{recipe_id}')
def update_recipe(recipe_id: UUID, data: RecipeUpdate):
    payload = data.model_dump(exclude_unset=True)
    if not payload:
        raise HTTPException(status_code=400, detail='No fields supplied')
    fields, values = [], []
    for key, value in payload.items():
        fields.append(f'{key}=%s')
        values.append(value)
    values.append(recipe_id)
    try:
        with connect() as conn:
            cur = conn.execute(f"UPDATE product_filament_recipes SET {', '.join(fields)} WHERE id=%s RETURNING *", values)
            result = one(cur)
            if not result:
                raise HTTPException(status_code=404, detail='Recipe line not found')
            conn.commit()
            return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.delete('/api/recipes/{recipe_id}', status_code=204)
def delete_recipe(recipe_id: UUID):
    with connect() as conn:
        cur = conn.execute('DELETE FROM product_filament_recipes WHERE id=%s RETURNING id', (recipe_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail='Recipe line not found')
        conn.commit()
    return None


@app.get('/api/forecast/filament')
async def filament_forecast():
    """Forecast filament needed by all planned/active production jobs.

    Mapped Spoolman filaments use live remaining grams. Internal-only filaments
    use sealed/open spool counts multiplied by nominal_spool_weight_grams.
    """
    with connect() as conn:
        demand = rows(conn.execute(
            """SELECT f.id AS filament_id,f.brand,f.material_line,f.color,f.spoolman_filament_id,
                      f.nominal_spool_weight_grams,
                      COALESCE(SUM((j.quantity_planned-j.quantity_completed) * r.grams_per_unit),0)::numeric(14,2) AS required_grams,
                      COUNT(DISTINCT j.id)::int AS job_count
               FROM filaments f
               JOIN product_filament_recipes r ON r.filament_id=f.id
               JOIN production_jobs j ON j.product_id=r.product_id
               WHERE j.status IN ('planned','active') AND j.quantity_planned > j.quantity_completed
                     AND r.grams_per_unit IS NOT NULL
               GROUP BY f.id,f.brand,f.material_line,f.color,f.spoolman_filament_id,f.nominal_spool_weight_grams
               ORDER BY f.brand,f.material_line,f.color"""
        ))
        internal = {str(x['filament_id']): x for x in rows(conn.execute(
            """SELECT fs.filament_id, (fs.sealed + fs.open) AS available_spools,
                      f.nominal_spool_weight_grams
               FROM filament_stock fs JOIN filaments f ON f.id=fs.filament_id"""
        ))}

    spoolman_by_filament = {}
    spoolman_connected = False
    if settings.spoolman_enabled:
        try:
            live = await SpoolmanClient().inventory()
            spoolman_connected = True
            for spool in live:
                if spool.get('archived') or spool.get('filament_id') is None:
                    continue
                grams = spool.get('remaining_weight')
                if grams is None:
                    continue
                key = int(spool['filament_id'])
                spoolman_by_filament[key] = spoolman_by_filament.get(key, 0.0) + float(grams)
        except Exception:
            spoolman_connected = False

    result = []
    for item in demand:
        mapped = item.get('spoolman_filament_id')
        if spoolman_connected and mapped is not None:
            available = spoolman_by_filament.get(int(mapped), 0.0)
            source = 'spoolman'
        else:
            stock = internal.get(str(item['filament_id']), {})
            available = float(stock.get('available_spools') or 0) * float(stock.get('nominal_spool_weight_grams') or 1000)
            source = 'internal_estimate'
        required = float(item.get('required_grams') or 0)
        shortage = max(required - available, 0.0)
        result.append({
            **item,
            'required_grams': round(required, 2),
            'available_grams': round(available, 2),
            'after_jobs_grams': round(available - required, 2),
            'shortage_grams': round(shortage, 2),
            'availability_source': source,
            'sufficient': shortage <= 0,
        })
    return {'spoolman_connected': spoolman_connected, 'items': result}


@app.patch('/api/projects/{project_id}')
def update_project(project_id: UUID, data: ProjectUpdate):
    payload = data.model_dump(exclude_unset=True)
    if not payload:
        raise HTTPException(status_code=400, detail='No fields supplied')
    fields, values = [], []
    for key, value in payload.items():
        fields.append(f'{key}=%s')
        values.append(value)
    fields.append('updated_at=now()')
    values.append(project_id)
    with connect() as conn:
        cur = conn.execute(f"UPDATE projects SET {', '.join(fields)} WHERE id=%s RETURNING *", values)
        result = one(cur)
        if not result:
            raise HTTPException(status_code=404, detail='Project not found')
        conn.commit()
        return result


@app.delete('/api/products/{product_id}', status_code=204)
def delete_product(product_id: UUID):
    """Permanently remove a product and its inventory/production history."""
    with connect() as conn:
        exists = conn.execute('SELECT 1 FROM products WHERE id=%s', (product_id,)).fetchone()
        if not exists:
            raise HTTPException(status_code=404, detail='Product not found')
        conn.execute('DELETE FROM product_filament_recipes WHERE product_id=%s', (product_id,))
        conn.execute('DELETE FROM production_jobs WHERE product_id=%s', (product_id,))
        conn.execute('DELETE FROM inventory_transactions WHERE product_id=%s', (product_id,))
        conn.execute('DELETE FROM inventory_baselines WHERE product_id=%s', (product_id,))
        conn.execute('DELETE FROM products WHERE id=%s', (product_id,))
        conn.commit()
    return None


@app.get('/api/vendor-fairs')
def vendor_fairs():
    with connect() as conn:
        return rows(conn.execute(
            """WITH ranked AS (
                 SELECT vf.*,
                        CASE WHEN COALESCE(end_date,start_date) < CURRENT_DATE THEN 'past' ELSE 'upcoming' END AS phase,
                        (start_date - CURRENT_DATE) AS days_until,
                        CASE WHEN COALESCE(end_date,start_date) < CURRENT_DATE AND revenue IS NOT NULL
                             THEN RANK() OVER (
                                  PARTITION BY (COALESCE(end_date,start_date) < CURRENT_DATE)
                                  ORDER BY revenue DESC NULLS LAST
                             )
                        END AS revenue_rank
                 FROM vendor_fairs vf
               ), plan AS (
                 SELECT t.vendor_fair_id,
                        COUNT(*)::int AS planned_products,
                        COALESCE(SUM(t.target_quantity),0)::int AS target_units,
                        COALESCE(SUM(GREATEST(t.target_quantity - COALESCE(r.system_stock,0) - COALESCE(r.in_production,0),0)),0)::int AS shortage_units
                 FROM vendor_fair_product_targets t
                 LEFT JOIN product_restock_status r ON r.product_id=t.product_id
                 GROUP BY t.vendor_fair_id
               )
               SELECT r.*,
                      CASE WHEN r.revenue IS NOT NULL THEN r.revenue-r.booth_fee END AS net_revenue,
                      CASE WHEN r.revenue IS NOT NULL AND r.hours_open>0 THEN ROUND(r.revenue/r.hours_open,2) END AS revenue_per_hour,
                      CASE WHEN r.revenue IS NOT NULL AND r.booth_fee>0 THEN ROUND(((r.revenue-r.booth_fee)/r.booth_fee)*100,1) END AS booth_roi_percent,
                      COALESCE(p.planned_products,0) AS planned_products,
                      COALESCE(p.target_units,0) AS target_units,
                      COALESCE(p.shortage_units,0) AS shortage_units
               FROM ranked r
               LEFT JOIN plan p ON p.vendor_fair_id=r.id
               ORDER BY CASE WHEN phase='upcoming' THEN 0 ELSE 1 END,
                        CASE WHEN phase='upcoming' THEN start_date END ASC,
                        CASE WHEN phase='past' THEN revenue END DESC NULLS LAST,
                        start_date DESC"""
        ))


@app.get('/api/vendor-fairs/{fair_id}/plan')
def vendor_fair_plan(fair_id: UUID):
    with connect() as conn:
        fair = one(conn.execute('SELECT * FROM vendor_fairs WHERE id=%s', (fair_id,)))
        if not fair:
            raise HTTPException(status_code=404, detail='Vendor fair not found')
        items = rows(conn.execute(
            '''SELECT t.id,t.vendor_fair_id,t.product_id,t.target_quantity,t.notes,
                      p.product_family,p.variant,p.priority,p.square_sku,
                      COALESCE(r.system_stock,0) AS system_stock,
                      COALESCE(r.in_production,0) AS in_production,
                      GREATEST(t.target_quantity-COALESCE(r.system_stock,0)-COALESCE(r.in_production,0),0) AS shortage,
                      GREATEST(COALESCE(r.system_stock,0)+COALESCE(r.in_production,0)-t.target_quantity,0) AS surplus_after_target
               FROM vendor_fair_product_targets t
               JOIN products p ON p.id=t.product_id
               LEFT JOIN product_restock_status r ON r.product_id=t.product_id
               WHERE t.vendor_fair_id=%s
               ORDER BY CASE p.priority WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,
                        p.product_family,p.variant''',
            (fair_id,),
        ))
        return {'fair': fair, 'items': items}


@app.post('/api/vendor-fairs/{fair_id}/plan', status_code=201)
def upsert_vendor_fair_plan_item(fair_id: UUID, data: VendorFairPlanItem):
    with connect() as conn:
        if not conn.execute('SELECT 1 FROM vendor_fairs WHERE id=%s', (fair_id,)).fetchone():
            raise HTTPException(status_code=404, detail='Vendor fair not found')
        if not conn.execute('SELECT 1 FROM products WHERE id=%s', (data.product_id,)).fetchone():
            raise HTTPException(status_code=404, detail='Product not found')
        cur = conn.execute(
            '''INSERT INTO vendor_fair_product_targets(vendor_fair_id,product_id,target_quantity,notes)
               VALUES (%s,%s,%s,%s)
               ON CONFLICT(vendor_fair_id,product_id)
               DO UPDATE SET target_quantity=EXCLUDED.target_quantity,notes=EXCLUDED.notes,updated_at=now()
               RETURNING *''',
            (fair_id, data.product_id, data.target_quantity, data.notes),
        )
        result = one(cur)
        conn.commit()
        return result


@app.delete('/api/vendor-fairs/{fair_id}/plan/{product_id}', status_code=204)
def delete_vendor_fair_plan_item(fair_id: UUID, product_id: UUID):
    with connect() as conn:
        cur = conn.execute(
            'DELETE FROM vendor_fair_product_targets WHERE vendor_fair_id=%s AND product_id=%s RETURNING id',
            (fair_id, product_id),
        )
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail='Vendor fair product target not found')
        conn.commit()
    return None


@app.get('/api/vendor-fairs/analytics/summary')
def vendor_fair_analytics():
    with connect() as conn:
        summary = one(conn.execute("""SELECT COUNT(*) FILTER (WHERE COALESCE(end_date,start_date)<CURRENT_DATE)::int AS completed_events,
          COALESCE(SUM(revenue) FILTER (WHERE COALESCE(end_date,start_date)<CURRENT_DATE),0) AS gross_revenue,
          COALESCE(SUM(booth_fee) FILTER (WHERE COALESCE(end_date,start_date)<CURRENT_DATE),0) AS booth_fees,
          COALESCE(SUM(revenue-booth_fee) FILTER (WHERE COALESCE(end_date,start_date)<CURRENT_DATE AND revenue IS NOT NULL),0) AS net_revenue,
          COALESCE(AVG(revenue) FILTER (WHERE COALESCE(end_date,start_date)<CURRENT_DATE AND revenue IS NOT NULL),0) AS avg_revenue,
          COALESCE(SUM(revenue)/NULLIF(SUM(hours_open),0),0) AS revenue_per_hour
          FROM vendor_fairs"""))
        by_type = rows(conn.execute("""SELECT COALESCE(NULLIF(event_type,''),'Uncategorized') AS event_type, COUNT(*)::int AS events,
          COALESCE(SUM(revenue),0) AS revenue, COALESCE(SUM(revenue-booth_fee),0) AS net_revenue,
          COALESCE(SUM(revenue)/NULLIF(SUM(hours_open),0),0) AS revenue_per_hour
          FROM vendor_fairs WHERE COALESCE(end_date,start_date)<CURRENT_DATE GROUP BY 1 ORDER BY net_revenue DESC"""))
        by_year = rows(conn.execute("""SELECT EXTRACT(YEAR FROM start_date)::int AS year, COUNT(*)::int AS events, COALESCE(SUM(revenue),0) AS revenue,
          COALESCE(SUM(revenue-booth_fee),0) AS net_revenue FROM vendor_fairs WHERE COALESCE(end_date,start_date)<CURRENT_DATE GROUP BY 1 ORDER BY year DESC"""))
        return {'summary': summary, 'by_type': by_type, 'by_year': by_year}


def _fair_window(fair: dict):
    tz = ZoneInfo('America/New_York')
    start_t = fair.get('selling_start_time') or time(0, 0)
    end_t = fair.get('selling_end_time') or time(23, 59, 59)
    end_d = fair.get('end_date') or fair['start_date']
    return (datetime.combine(fair['start_date'], start_t, tzinfo=tz), datetime.combine(end_d, end_t, tzinfo=tz))

@app.get('/api/vendor-fairs/{fair_id}/reconciliation')
def vendor_fair_reconciliation(fair_id: UUID):
    with connect() as conn:
        fair = one(conn.execute('SELECT * FROM vendor_fairs WHERE id=%s', (fair_id,)))
        if not fair: raise HTTPException(status_code=404, detail='Vendor fair not found')
        start_dt,end_dt=_fair_window(fair)
        tx=rows(conn.execute('''SELECT s.*,p.id AS product_id,p.product_family,p.variant,a.vendor_fair_id AS assigned_fair_id,a.assignment_source FROM square_transactions s LEFT JOIN products p ON p.square_sku=s.sku LEFT JOIN vendor_fair_square_assignments a ON a.square_transaction_id=s.id WHERE a.vendor_fair_id=%s OR (s.transaction_at >= %s AND s.transaction_at <= %s) ORDER BY s.transaction_at''',(fair_id,start_dt,end_dt)))
        eligible=[x for x in tx if x.get('assigned_fair_id') in (None,fair_id)]
        sold={}; gross=Decimal('0'); net=Decimal('0'); units=Decimal('0')
        for x in eligible:
            q=Decimal(str(x.get('quantity') or 0)); units+=q; gross+=Decimal(str(x.get('gross_sales') or 0)); net+=Decimal(str(x.get('net_sales') or 0))
            if x.get('product_id'):
                k=str(x['product_id']); z=sold.setdefault(k,{'quantity':Decimal('0'),'net_sales':Decimal('0')}); z['quantity']+=q; z['net_sales']+=Decimal(str(x.get('net_sales') or 0))
        targets=rows(conn.execute('''SELECT t.product_id,t.target_quantity,p.product_family,p.variant FROM vendor_fair_product_targets t JOIN products p ON p.id=t.product_id WHERE t.vendor_fair_id=%s ORDER BY p.product_family,p.variant''',(fair_id,)))
        for t in targets:
            z=sold.get(str(t['product_id']),{}); t['sold_quantity']=z.get('quantity',Decimal('0')); t['returned_quantity']=Decimal(str(t['target_quantity']))-t['sold_quantity']; t['sell_through_percent']=(t['sold_quantity']/Decimal(str(t['target_quantity']))*100 if t['target_quantity'] else Decimal('0'))
        return {'fair':fair,'window_start':start_dt,'window_end':end_dt,'transactions':tx,'eligible_transactions':len(eligible),'units':units,'gross_sales':gross,'net_sales':net,'products':targets}

@app.post('/api/vendor-fairs/{fair_id}/reconciliation/confirm')
def confirm_vendor_fair_reconciliation(fair_id: UUID):
    with connect() as conn:
        fair=one(conn.execute('SELECT * FROM vendor_fairs WHERE id=%s',(fair_id,)))
        if not fair: raise HTTPException(404,'Vendor fair not found')
        start_dt,end_dt=_fair_window(fair)
        tx=rows(conn.execute('''SELECT s.id,a.vendor_fair_id AS assigned_fair_id FROM square_transactions s LEFT JOIN vendor_fair_square_assignments a ON a.square_transaction_id=s.id WHERE a.vendor_fair_id=%s OR (s.transaction_at BETWEEN %s AND %s)''',(fair_id,start_dt,end_dt)))
        eligible=[x for x in tx if x.get('assigned_fair_id') in (None,fair_id)]
        for x in eligible: conn.execute("INSERT INTO vendor_fair_square_assignments(vendor_fair_id,square_transaction_id,assignment_source) VALUES (%s,%s,'window') ON CONFLICT(square_transaction_id) DO NOTHING",(fair_id,x['id']))
        total=conn.execute('''SELECT COALESCE(SUM(s.net_sales),0) FROM vendor_fair_square_assignments a JOIN square_transactions s ON s.id=a.square_transaction_id WHERE a.vendor_fair_id=%s''',(fair_id,)).fetchone()[0]
        conn.execute("UPDATE vendor_fairs SET reconciliation_status='reconciled',reconciled_at=now(),revenue=%s,updated_at=now() WHERE id=%s",(total,fair_id)); conn.commit()
        return {'status':'reconciled','transactions_assigned':len(eligible),'revenue':total}

@app.delete('/api/vendor-fairs/{fair_id}/reconciliation/reset')
def reset_vendor_fair_reconciliation(fair_id: UUID):
    with connect() as conn:
        fair = one(conn.execute('SELECT * FROM vendor_fairs WHERE id=%s FOR UPDATE', (fair_id,)))
        if not fair:
            raise HTTPException(status_code=404, detail='Vendor fair not found')
        removed = conn.execute(
            'DELETE FROM vendor_fair_square_assignments WHERE vendor_fair_id=%s',
            (fair_id,)
        ).rowcount or 0
        conn.execute(
            """UPDATE vendor_fairs
               SET reconciliation_status='not_reconciled', reconciled_at=NULL,
                   revenue=0, updated_at=now()
               WHERE id=%s""",
            (fair_id,)
        )
        conn.commit()
        return {'reset': True, 'assignments_removed': removed}


@app.post('/api/vendor-fairs/{fair_id}/reconciliation/transactions/{transaction_id}')
def assign_square_to_fair(fair_id: UUID, transaction_id: UUID):
    with connect() as conn:
        if not conn.execute('SELECT 1 FROM vendor_fairs WHERE id=%s',(fair_id,)).fetchone(): raise HTTPException(404,'Vendor fair not found')
        try: conn.execute("INSERT INTO vendor_fair_square_assignments(vendor_fair_id,square_transaction_id,assignment_source) VALUES (%s,%s,'manual')",(fair_id,transaction_id)); conn.commit()
        except Exception: conn.rollback(); raise HTTPException(409,'This Square row is already assigned to a vendor fair')
        return {'assigned':True}

@app.delete('/api/vendor-fairs/{fair_id}/reconciliation/transactions/{transaction_id}', status_code=204)
def unassign_square_from_fair(fair_id: UUID, transaction_id: UUID):
    with connect() as conn: conn.execute('DELETE FROM vendor_fair_square_assignments WHERE vendor_fair_id=%s AND square_transaction_id=%s',(fair_id,transaction_id)); conn.commit()
    return None

@app.get('/api/vendor-fairs/{fair_id}/square-candidates')
def square_candidates(fair_id: UUID, hours: int = 12):
    hours=max(1,min(hours,72))
    with connect() as conn:
        fair=one(conn.execute('SELECT * FROM vendor_fairs WHERE id=%s',(fair_id,)))
        if not fair: raise HTTPException(404,'Vendor fair not found')
        start_dt,end_dt=_fair_window(fair)
        return rows(conn.execute('''SELECT s.*,p.product_family,p.variant,a.vendor_fair_id AS assigned_fair_id FROM square_transactions s LEFT JOIN products p ON p.square_sku=s.sku LEFT JOIN vendor_fair_square_assignments a ON a.square_transaction_id=s.id WHERE s.transaction_at BETWEEN %s AND %s ORDER BY s.transaction_at''',(start_dt-timedelta(hours=hours),end_dt+timedelta(hours=hours))))

def _historical_sell_through_by_type(conn):
    data = rows(conn.execute('''
        WITH targets AS (
          SELECT vf.id, COALESCE(vf.event_type,'') AS event_type, SUM(t.target_quantity)::numeric AS target_units
          FROM vendor_fairs vf
          JOIN vendor_fair_product_targets t ON t.vendor_fair_id=vf.id
          WHERE vf.reconciliation_status='reconciled'
          GROUP BY vf.id, COALESCE(vf.event_type,'')
        ), sold AS (
          SELECT a.vendor_fair_id, COALESCE(SUM(ABS(s.quantity)),0)::numeric AS sold_units
          FROM vendor_fair_square_assignments a
          JOIN square_transactions s ON s.id=a.square_transaction_id
          JOIN products p ON p.square_sku=s.sku
          JOIN vendor_fair_product_targets vt ON vt.vendor_fair_id=a.vendor_fair_id AND vt.product_id=p.id
          WHERE COALESCE(s.quantity,0) > 0
          GROUP BY a.vendor_fair_id
        )
        SELECT t.event_type, SUM(COALESCE(s.sold_units,0)) AS sold_units, SUM(t.target_units) AS target_units,
               CASE WHEN SUM(t.target_units)>0 THEN LEAST(100, GREATEST(0, SUM(COALESCE(s.sold_units,0))*100.0/SUM(t.target_units))) ELSE NULL END AS sell_through_percent,
               COUNT(*) AS event_count
        FROM targets t LEFT JOIN sold s ON s.vendor_fair_id=t.id
        GROUP BY t.event_type
    '''))
    return {str(x['event_type'] or ''): x for x in data}

def _fair_expected_sell_through(fair, history):
    mode = fair.get('planning_mode') or 'conservative'
    if mode == 'custom':
        pct = fair.get('custom_sell_through_percent')
        return float(pct if pct is not None else 100), 'custom'
    if mode == 'historical':
        h = history.get(str(fair.get('event_type') or ''))
        if h and h.get('sell_through_percent') is not None:
            return float(h['sell_through_percent']), 'historical'
        return 100.0, 'historical_fallback'
    return 100.0, 'conservative'

@app.get('/api/vendor-fairs/planning/timeline')
def vendor_fair_timeline(days: int = 30):
    days=max(1,min(days,365))
    with connect() as conn:
        fairs=rows(conn.execute("SELECT * FROM vendor_fairs WHERE COALESCE(end_date,start_date)>=CURRENT_DATE ORDER BY start_date,end_date NULLS FIRST,name"))
        stock={str(x['product_id']):{'stock':int(x['system_stock'] or 0),'production':int(x['in_production'] or 0),'product_family':x['product_family'],'variant':x['variant']} for x in rows(conn.execute('SELECT * FROM product_restock_status'))}
        history=_historical_sell_through_by_type(conn)
        available={k:v['stock']+v['production'] for k,v in stock.items()}
        result=[]; requirements={}; requirements_window={}; next_deadline={}
        today=date.today(); window_end=today+timedelta(days=days)
        for fair in fairs:
            pct,pct_source=_fair_expected_sell_through(fair,history)
            items=rows(conn.execute('''SELECT t.product_id,t.target_quantity,p.product_family,p.variant FROM vendor_fair_product_targets t JOIN products p ON p.id=t.product_id WHERE t.vendor_fair_id=%s ORDER BY p.product_family,p.variant''',(fair['id'],)))
            shortage_total=0; target_total=0; expected_sales_total=0; expected_return_total=0
            for x in items:
                k=str(x['product_id']); before=int(available.get(k,0)); target=int(x['target_quantity'] or 0); shortage=max(target-before,0)
                expected_sales=min(target, int(round(target*pct/100.0)))
                expected_returns=max(target-expected_sales,0)
                after=max(before-target,0)+expected_returns
                x.update({'available_before':before,'reserved':min(target,before),'shortage':shortage,'expected_sell_through_percent':round(pct,2),'expected_sales':expected_sales,'expected_returns':expected_returns,'projected_after':after})
                available[k]=after; shortage_total+=shortage; target_total+=target; expected_sales_total+=expected_sales; expected_return_total+=expected_returns
                if shortage:
                    requirements[k]=requirements.get(k,0)+shortage
                    if fair['start_date'] <= window_end:
                        requirements_window[k]=requirements_window.get(k,0)+shortage
                    if k not in next_deadline:
                        next_deadline[k]=fair['start_date']
            fair['effective_sell_through_percent']=round(pct,2); fair['sell_through_source']=pct_source
            result.append({'fair':fair,'items':items,'shortage_units':shortage_total,'target_units':target_total,'expected_sales_units':expected_sales_total,'expected_return_units':expected_return_total})
        req=[]
        keys=set(requirements)|set(requirements_window)
        for k in keys:
            meta=stock.get(k,{})
            req.append({'product_id':k,'product_family':meta.get('product_family',''),'variant':meta.get('variant',''),'quantity_required':requirements.get(k,0),'quantity_required_window':requirements_window.get(k,0),'next_deadline':next_deadline.get(k)})
        req.sort(key=lambda x:((x['next_deadline'] or date.max),-x['quantity_required_window'],-x['quantity_required'],x['product_family'],x['variant']))
        return {'events':result,'requirements':req,'total_units_required':sum(x['quantity_required'] for x in req),'window_days':days,'window_units_required':sum(x['quantity_required_window'] for x in req),'historical_sell_through':[dict(v) for v in history.values()]}

class CapacityGroupCreate(BaseModel):
    name: str
    printer_count: int = Field(default=1, gt=0)
    hours_per_printer_day: Decimal = Field(default=Decimal('16'), ge=0, le=24)
    efficiency_percent: Decimal = Field(default=Decimal('85'), gt=0, le=100)
    notes: str | None = None
    active: bool = True

@app.get('/api/production/capacity-groups')
def capacity_groups():
    with connect() as conn:
        return rows(conn.execute('SELECT * FROM production_capacity_groups ORDER BY active DESC,name'))

@app.post('/api/production/capacity-groups', status_code=201)
def create_capacity_group(data: CapacityGroupCreate):
    with connect() as conn:
        result=one(conn.execute("INSERT INTO production_capacity_groups(name,printer_count,hours_per_printer_day,efficiency_percent,notes,active) VALUES (%s,%s,%s,%s,%s,%s) RETURNING *",(data.name.strip(),data.printer_count,data.hours_per_printer_day,data.efficiency_percent,data.notes,data.active)))
        conn.commit(); return result

@app.patch('/api/production/capacity-groups/{group_id}')
def update_capacity_group(group_id: UUID, data: CapacityGroupCreate):
    with connect() as conn:
        result=one(conn.execute("UPDATE production_capacity_groups SET name=%s,printer_count=%s,hours_per_printer_day=%s,efficiency_percent=%s,notes=%s,active=%s,updated_at=now() WHERE id=%s RETURNING *",(data.name.strip(),data.printer_count,data.hours_per_printer_day,data.efficiency_percent,data.notes,data.active,group_id)))
        if not result: raise HTTPException(404,'Capacity group not found')
        conn.commit(); return result

@app.delete('/api/production/capacity-groups/{group_id}', status_code=204)
def delete_capacity_group(group_id: UUID):
    with connect() as conn:
        conn.execute('DELETE FROM production_capacity_groups WHERE id=%s',(group_id,)); conn.commit()

@app.get('/api/production/planning')
def production_planning(days: int = 30):
    timeline=vendor_fair_timeline(days)
    with connect() as conn:
        products_by_id={str(x['id']):x for x in rows(conn.execute('SELECT id,product_family,variant,priority,print_time_minutes,batch_size FROM products WHERE active=true'))}
        groups=rows(conn.execute('SELECT * FROM production_capacity_groups WHERE active=true ORDER BY name'))
    daily_capacity=sum(float(g['printer_count'])*float(g['hours_per_printer_day'])*float(g['efficiency_percent'])/100 for g in groups)
    queue=[]
    for r in timeline['requirements']:
        qty=int(r['quantity_required'] or 0); p=products_by_id.get(str(r['product_id']),{})
        batch=max(1,int(p.get('batch_size') or 1)); mins=p.get('print_time_minutes')
        batches=(qty+batch-1)//batch if qty else 0
        hours=(batches*float(mins)/60) if mins is not None else None
        deadline=r.get('next_deadline'); days_left=max(0,(date.fromisoformat(str(deadline))-date.today()).days) if deadline else None
        capacity_to_deadline=(daily_capacity*days_left) if days_left is not None else None
        queue.append({**r,'priority':p.get('priority','medium'),'batch_size':batch,'print_time_minutes':mins,'batches_required':batches,'printer_hours':hours,'days_left':days_left,'capacity_hours_to_deadline':capacity_to_deadline,'at_risk': bool(hours is not None and capacity_to_deadline is not None and hours>capacity_to_deadline)})
    priority_order={'high':0,'medium':1,'low':2}
    queue.sort(key=lambda x:(x.get('next_deadline') or '9999-12-31',priority_order.get(x.get('priority'),1),-(x.get('quantity_required') or 0)))
    known_hours=sum(x['printer_hours'] or 0 for x in queue); unknown=sum(1 for x in queue if x['quantity_required'] and x['printer_hours'] is None)
    return {'queue':queue,'capacity_groups':groups,'daily_capacity_hours':daily_capacity,'known_printer_hours':known_hours,'unknown_profile_products':unknown,'window_days':days,'window_capacity_hours':daily_capacity*days}

@app.post('/api/vendor-fairs', status_code=201)
def create_vendor_fair(data: VendorFairCreate):
    if data.end_date and data.end_date < data.start_date:
        raise HTTPException(status_code=400, detail='End date cannot be before start date')
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO vendor_fairs(name,start_date,end_date,location,booth_fee,revenue,event_type,hours_open,attendance_estimate,weather_conditions,booth_location_quality,would_return,notes,selling_start_time,selling_end_time,planning_mode,custom_sell_through_percent)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
            (data.name.strip(), data.start_date, data.end_date, data.location, data.booth_fee, data.revenue, data.event_type, data.hours_open, data.attendance_estimate, data.weather_conditions, data.booth_location_quality, data.would_return, data.notes, data.selling_start_time, data.selling_end_time, data.planning_mode, data.custom_sell_through_percent),
        )
        result = one(cur)
        conn.commit()
        return result


@app.patch('/api/vendor-fairs/{fair_id}')
def update_vendor_fair(fair_id: UUID, data: VendorFairUpdate):
    payload = data.model_dump(exclude_unset=True)
    if not payload:
        raise HTTPException(status_code=400, detail='No fields supplied')
    fields, values = [], []
    for key, value in payload.items():
        fields.append(f'{key}=%s')
        values.append(value)
    fields.append('updated_at=now()')
    values.append(fair_id)
    with connect() as conn:
        cur = conn.execute(f"UPDATE vendor_fairs SET {', '.join(fields)} WHERE id=%s RETURNING *", values)
        result = one(cur)
        if not result:
            raise HTTPException(status_code=404, detail='Vendor fair not found')
        if result['end_date'] and result['end_date'] < result['start_date']:
            conn.rollback()
            raise HTTPException(status_code=400, detail='End date cannot be before start date')
        conn.commit()
        return result


@app.delete('/api/vendor-fairs/{fair_id}', status_code=204)
def delete_vendor_fair(fair_id: UUID):
    with connect() as conn:
        cur = conn.execute('DELETE FROM vendor_fairs WHERE id=%s RETURNING id', (fair_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail='Vendor fair not found')
        conn.commit()
    return None

# --- Phase 4.1 backup / restore + admin settings ---
import os
import subprocess
from pathlib import Path as _BackupPath

BACKUP_DIR = _BackupPath(os.getenv('BACKUP_DIR', '/backups'))
BACKUP_DIR.mkdir(parents=True, exist_ok=True)


class AdminSettingsUpdate(BaseModel):
    business_name: str | None = None
    currency: str | None = None
    timezone: str | None = None
    default_spool_weight_grams: int | None = Field(default=None, gt=0)
    backup_retention_days: int | None = Field(default=None, ge=1, le=3650)
    backup_warning_hours: int | None = Field(default=None, ge=1, le=720)


def _postgres_cli_env():
    env = os.environ.copy()
    env['PGPASSWORD'] = os.getenv('POSTGRES_PASSWORD', '')
    return env


def _postgres_cli_base():
    return [
        '-h', os.getenv('POSTGRES_HOST', 'db'),
        '-U', os.getenv('POSTGRES_USER', 'cft'),
        '-d', os.getenv('POSTGRES_DB', 'cft_inventory'),
    ]


def _safe_backup_path(filename: str):
    name = _BackupPath(filename).name
    if name != filename or not re.fullmatch(r'[A-Za-z0-9_.-]+\.dump', name):
        raise HTTPException(status_code=400, detail='Invalid backup filename')
    path = BACKUP_DIR / name
    if not path.exists():
        raise HTTPException(status_code=404, detail='Backup not found')
    return path


def _setting(conn, key: str, default: str):
    row = conn.execute('SELECT value FROM app_settings WHERE key=%s', (key,)).fetchone()
    return row[0] if row else default


def _cleanup_old_backups():
    try:
        with connect() as conn:
            days = int(_setting(conn, 'backup_retention_days', '30'))
    except Exception:
        days = 30
    cutoff = datetime.now().timestamp() - (days * 86400)
    for path in BACKUP_DIR.glob('cft_inventory_*.dump'):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            pass


def _create_backup(prefix: str = 'cft_inventory'):
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f'{prefix}_{stamp}.dump'
    path = BACKUP_DIR / filename
    started = datetime.now()
    cmd = ['pg_dump', *_postgres_cli_base(), '-Fc', '-f', str(path)]
    result = subprocess.run(cmd, env=_postgres_cli_env(), capture_output=True, text=True)
    if result.returncode != 0:
        with connect() as conn:
            conn.execute(
                "INSERT INTO backup_operations(operation,filename,status,message,started_at,completed_at) VALUES ('backup',%s,'failed',%s,%s,now())",
                (filename, (result.stderr or result.stdout)[-2000:], started),
            )
            conn.commit()
        raise HTTPException(status_code=500, detail=f'Backup failed: {(result.stderr or result.stdout).strip()}')
    size = path.stat().st_size
    with connect() as conn:
        conn.execute(
            "INSERT INTO backup_operations(operation,filename,status,size_bytes,message,started_at,completed_at) VALUES ('backup',%s,'success',%s,'Manual backup',%s,now())",
            (filename, size, started),
        )
        conn.commit()
    _cleanup_old_backups()
    return {'filename': filename, 'size_bytes': size, 'created_at': datetime.fromtimestamp(path.stat().st_mtime)}


@app.get('/api/admin/settings')
def admin_settings():
    defaults = {
        'business_name': 'Create Favorite Things LLC',
        'currency': 'USD',
        'timezone': 'America/New_York',
        'default_spool_weight_grams': '1000',
        'backup_retention_days': '30',
        'backup_warning_hours': '36',
    }
    with connect() as conn:
        result = defaults.copy()
        for key, value in conn.execute('SELECT key,value FROM app_settings'):
            result[key] = value
    for key in ('default_spool_weight_grams', 'backup_retention_days', 'backup_warning_hours'):
        result[key] = int(result[key])
    result['backup_cron'] = os.getenv('BACKUP_CRON', '0 3 * * *')
    result['scheduled_backups_enabled'] = True
    return result


@app.patch('/api/admin/settings')
def update_admin_settings(data: AdminSettingsUpdate):
    payload = data.model_dump(exclude_unset=True)
    if not payload:
        raise HTTPException(status_code=400, detail='No settings supplied')
    with connect() as conn:
        for key, value in payload.items():
            conn.execute(
                '''INSERT INTO app_settings(key,value,updated_at) VALUES (%s,%s,now())
                   ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value,updated_at=now()''',
                (key, str(value)),
            )
        conn.commit()
    return admin_settings()


@app.get('/api/admin/backups')
def list_backups():
    files = []
    for path in sorted(BACKUP_DIR.glob('*.dump'), key=lambda p: p.stat().st_mtime, reverse=True):
        stat = path.stat()
        files.append({
            'filename': path.name,
            'size_bytes': stat.st_size,
            'created_at': datetime.fromtimestamp(stat.st_mtime),
        })
    with connect() as conn:
        warning_hours = int(_setting(conn, 'backup_warning_hours', '36'))
        operations = rows(conn.execute('SELECT * FROM backup_operations ORDER BY started_at DESC LIMIT 50'))
    latest = files[0] if files else None
    age_hours = None
    healthy = False
    if latest:
        age_hours = max(0, (datetime.now() - latest['created_at']).total_seconds() / 3600)
        healthy = age_hours <= warning_hours
    return {
        'backups': files,
        'operations': operations,
        'latest': latest,
        'latest_age_hours': age_hours,
        'warning_hours': warning_hours,
        'healthy': healthy,
        'backup_directory': str(BACKUP_DIR),
    }


@app.post('/api/admin/backups', status_code=201)
def create_backup():
    return _create_backup()


@app.get('/api/admin/backups/{filename}/download')
def download_backup(filename: str):
    path = _safe_backup_path(filename)
    return FileResponse(path, media_type='application/octet-stream', filename=path.name)


@app.delete('/api/admin/backups/{filename}', status_code=204)
def delete_backup(filename: str):
    path = _safe_backup_path(filename)
    size = path.stat().st_size
    path.unlink()
    with connect() as conn:
        conn.execute(
            "INSERT INTO backup_operations(operation,filename,status,size_bytes,message) VALUES ('delete',%s,'success',%s,'Backup deleted')",
            (filename, size),
        )
        conn.commit()
    return None


@app.post('/api/admin/restore')
async def restore_backup(file: UploadFile = File(...), confirmation: str = Form(...)):
    if confirmation.strip() != 'RESTORE':
        raise HTTPException(status_code=400, detail='Type RESTORE exactly to confirm database replacement')
    if not (file.filename or '').lower().endswith('.dump'):
        raise HTTPException(status_code=400, detail='Restore file must be a .dump file created by this application')

    # Always make a local safety backup immediately before a restore.
    safety = _create_backup(prefix='pre_restore')
    upload_name = f'_restore_{datetime.now().strftime("%Y%m%d_%H%M%S")}.dump'
    upload_path = BACKUP_DIR / upload_name
    content = await file.read()
    upload_path.write_bytes(content)

    validate = subprocess.run(['pg_restore', '-l', str(upload_path)], env=_postgres_cli_env(), capture_output=True, text=True)
    if validate.returncode != 0:
        upload_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail='The uploaded file is not a valid PostgreSQL custom-format backup')

    started = datetime.now()
    cmd = ['pg_restore', '--clean', '--if-exists', '--no-owner', '--no-privileges', '--exit-on-error', *_postgres_cli_base(), str(upload_path)]
    result = subprocess.run(cmd, env=_postgres_cli_env(), capture_output=True, text=True)
    upload_path.unlink(missing_ok=True)
    if result.returncode != 0:
        try:
            with connect() as conn:
                conn.execute(
                    "INSERT INTO backup_operations(operation,filename,status,message,started_at,completed_at) VALUES ('restore',%s,'failed',%s,%s,now())",
                    (file.filename, (result.stderr or result.stdout)[-2000:], started),
                )
                conn.commit()
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=f'Restore failed. Safety backup: {safety["filename"]}. Error: {(result.stderr or result.stdout).strip()}')

    # Bring an older restored database forward to the schema expected by this app.
    apply_migrations()
    with connect() as conn:
        conn.execute(
            "INSERT INTO backup_operations(operation,filename,status,size_bytes,message,started_at,completed_at) VALUES ('restore',%s,'success',%s,%s,%s,now())",
            (file.filename, len(content), f'Restored successfully; safety backup {safety["filename"]}', started),
        )
        conn.commit()
    return {'status': 'restored', 'restored_from': file.filename, 'safety_backup': safety['filename']}
