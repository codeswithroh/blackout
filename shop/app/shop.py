"""The demo shop. v2.14.0 migrates the schema; v2.13.2 refuses to boot on a newer schema."""
import json, os, sqlite3, sys, time, uuid
from collections import deque

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

VERSION = os.environ["SHOP_VERSION"]
PAYMENT_KEY = os.environ["PAYMENT_KEY"]
PAYMENTS_URL = os.environ.get("PAYMENTS_URL", "http://payments:8101")
DB = os.environ.get("SHOP_DB", "/data/shop.db")
CODE_SCHEMA = {"v2.13.2": 13, "v2.14.0": 14}[VERSION]
CATALOG = [{"sku": "lamp", "cents": 4900}, {"sku": "desk", "cents": 21900}, {"sku": "chair", "cents": 13900}]
recent = deque(maxlen=5000)
app = FastAPI()


def log(level, msg, **fields):
    print(json.dumps({"ts": round(time.time(), 3), "level": level, "service": "shop", "version": VERSION, "msg": msg, **fields}), flush=True)


def connect():
    db = sqlite3.connect(DB, timeout=5)
    db.execute("CREATE TABLE IF NOT EXISTS schema_version (v INTEGER NOT NULL)")
    return db


def migrate():
    db = connect()
    with db:
        db.execute("CREATE TABLE IF NOT EXISTS orders (id TEXT PRIMARY KEY, sku TEXT NOT NULL, cents INTEGER NOT NULL, status TEXT NOT NULL)")
        row = db.execute("SELECT v FROM schema_version").fetchone()
        if row is None:
            db.execute("INSERT INTO schema_version VALUES (13)")
            current = 13
        else:
            current = row[0]
        if current > CODE_SCHEMA:
            log("fatal", "database schema is newer than this build", db_schema=current, code_schema=CODE_SCHEMA,
                hint="orders.tax_cents was added by v2.14.0 and is NOT NULL")
            sys.exit(1)
        if current == 13 and CODE_SCHEMA == 14:
            db.executescript("""
                CREATE TABLE orders_new (id TEXT PRIMARY KEY, sku TEXT NOT NULL, cents INTEGER NOT NULL, status TEXT NOT NULL, tax_cents INTEGER NOT NULL);
                INSERT INTO orders_new SELECT id, sku, cents, status, 0 FROM orders;
                DROP TABLE orders;
                ALTER TABLE orders_new RENAME TO orders;
                UPDATE schema_version SET v = 14;
            """)
            log("info", "migration applied", migration="0014_orders_tax_cents")
    db.close()


migrate()
log("info", "shop started", code_schema=CODE_SCHEMA)


@app.middleware("http")
async def record(request: Request, call_next):
    response = await call_next(request)
    recent.append((time.time(), request.url.path, response.status_code))
    return response


@app.get("/health")
def health():
    return {"ok": True, "version": VERSION}


@app.get("/catalog")
def catalog():
    return {"items": CATALOG}


@app.get("/metrics")
def metrics(window: int = 60):
    since = time.time() - window
    rows = [r for r in recent if r[0] >= since and r[1] == "/checkout"]
    errors = sum(1 for r in rows if r[2] >= 500)
    return {"version": VERSION, "window_s": window, "checkouts": len(rows), "errors": errors,
            "error_rate": round(errors / len(rows), 3) if rows else 0.0}


@app.post("/checkout")
def checkout(body: dict):
    sku, order_id = body.get("sku", "lamp"), uuid.uuid4().hex[:12]
    item = next((i for i in CATALOG if i["sku"] == sku), None)
    if item is None:
        return JSONResponse({"error": "unknown sku"}, status_code=404)
    db = connect()
    try:
        with db:
            if CODE_SCHEMA == 14:
                db.execute("INSERT INTO orders VALUES (?, ?, ?, 'pending', ?)", (order_id, sku, item["cents"], item["cents"] // 10))
            else:
                db.execute("INSERT INTO orders (id, sku, cents, status) VALUES (?, ?, ?, 'pending')", (order_id, sku, item["cents"]))
        r = httpx.post(f"{PAYMENTS_URL}/charge", headers={"Authorization": f"Bearer {PAYMENT_KEY}"},
                       json={"order": order_id, "cents": item["cents"]}, timeout=3)
        if r.status_code != 200:
            with db:
                db.execute("UPDATE orders SET status = 'failed' WHERE id = ?", (order_id,))
            log("error", "checkout failed", order=order_id, error="PaymentProviderError", upstream="payments",
                upstream_status=r.status_code, upstream_body=r.text[:120], at="payments_client.charge")
            return JSONResponse({"error": "checkout failed"}, status_code=500)
        with db:
            db.execute("UPDATE orders SET status = 'paid' WHERE id = ?", (order_id,))
        log("info", "checkout ok", order=order_id, sku=sku)
        return {"order": order_id, "status": "paid"}
    finally:
        db.close()
