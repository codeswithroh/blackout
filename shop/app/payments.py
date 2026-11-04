"""Mock payment provider. Keys can be expired mid-drill through the admin route."""
import os

from fastapi import FastAPI, Header, HTTPException

KEYS = {os.environ["PAYMENT_KEY_PRIMARY"], os.environ["PAYMENT_KEY_SECONDARY"]}
ADMIN = os.environ["PAYMENTS_ADMIN_TOKEN"]
expired = set()
app = FastAPI()


def admin(token):
    if token != ADMIN:
        raise HTTPException(403, "admin token required")


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/charge")
def charge(body: dict, authorization: str = Header(default="")):
    key = authorization.removeprefix("Bearer ")
    if key not in KEYS:
        raise HTTPException(401, "invalid api key")
    if key in expired:
        raise HTTPException(401, "api key expired")
    return {"charged": body.get("cents"), "order": body.get("order")}


@app.post("/admin/expire")
def expire(body: dict, x_admin_token: str = Header(default="")):
    admin(x_admin_token)
    expired.add(body["key"])
    return {"expired": len(expired)}


@app.post("/admin/reset")
def reset(x_admin_token: str = Header(default="")):
    admin(x_admin_token)
    expired.clear()
    return {"expired": 0}
