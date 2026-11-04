"""Customer's-eye view: drives traffic from outside the shop and reports success over a rolling window."""
import json, os, random, threading, time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx

SHOP = os.environ.get("SHOP_URL", "http://shop:8100")
INTERVAL = float(os.environ.get("PROBE_INTERVAL", "0.5"))
samples = deque(maxlen=4000)


def probe(client, name, method, path, **kw):
    try:
        ok = client.request(method, f"{SHOP}{path}", timeout=2, **kw).status_code < 500
    except httpx.HTTPError:
        ok = False
    samples.append((time.time(), name, ok))


def traffic():
    with httpx.Client() as client:
        while True:
            probe(client, "checkout", "POST", "/checkout", json={"sku": random.choice(["lamp", "desk", "chair"])})
            probe(client, "catalog", "GET", "/catalog")
            time.sleep(INTERVAL)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        window = 15
        if "window=" in self.path:
            window = int(self.path.split("window=")[1])
        since = time.time() - window
        out = {"window_s": window}
        for name in ("checkout", "catalog"):
            rows = [ok for t, n, ok in list(samples) if t >= since and n == name]
            out[name] = round(sum(rows) / len(rows), 3) if rows else None
        body = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


threading.Thread(target=traffic, daemon=True).start()
ThreadingHTTPServer(("0.0.0.0", 8102), Handler).serve_forever()
