import os
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, HttpUrl
from sqlalchemy import create_engine, text


DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set")

engine = create_engine(DATABASE_URL)

app = FastAPI(title="API Health Monitoring")


class MonitorCreate(BaseModel):
    name: str
    url: HttpUrl
    check_interval: int = 60
    timeout: int = 5
    expected_status_code: int = 200
    webhook_url: str | None = None
    enabled: bool = True


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/monitors")
def create_monitor(m: MonitorCreate):
    with engine.begin() as conn:
        res = conn.execute(
            text("""
                INSERT INTO monitors
                (name, url, check_interval, timeout, expected_status_code, webhook_url, enabled, created_at,
                 status, consecutive_failures, consecutive_successes)
                VALUES
                (:name, :url, :check_interval, :timeout, :expected_status_code, :webhook_url, :enabled, :created_at,
                 'UNKNOWN', 0, 0)
                RETURNING id, name, url, check_interval, timeout, expected_status_code, webhook_url, enabled, created_at, status
            """),
            {
                "name": m.name,
                "url": str(m.url),
                "check_interval": m.check_interval,
                "timeout": m.timeout,
                "expected_status_code": m.expected_status_code,
                "webhook_url": m.webhook_url,
                "enabled": m.enabled,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        ).mappings().first()

    return dict(res)


@app.get("/monitors")
def list_monitors():
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT id, name, url, check_interval, timeout, expected_status_code,
                       webhook_url, enabled, created_at, status, consecutive_failures, consecutive_successes
                FROM monitors
                ORDER BY id
            """)
        ).mappings().all()

    return [dict(r) for r in rows]


@app.get("/monitors/{monitor_id}")
def get_monitor(monitor_id: int):
    with engine.connect() as conn:
        res = conn.execute(
            text("""
                SELECT id, name, url, check_interval, timeout, expected_status_code,
                       webhook_url, enabled, created_at, status, consecutive_failures, consecutive_successes
                FROM monitors
                WHERE id = :id
            """),
            {"id": monitor_id}
        ).mappings().first()
        
        if not res:
            raise HTTPException(status_code=404, detail="Monitor not found")
    
    return dict(res)


@app.put("/monitors/{monitor_id}")
def update_monitor(monitor_id: int, m: MonitorCreate):
    with engine.begin() as conn:
        res = conn.execute(
            text("""
                UPDATE monitors
                SET name = :name, url = :url, check_interval = :check_interval,
                    timeout = :timeout, expected_status_code = :expected_status_code,
                    webhook_url = :webhook_url, enabled = :enabled
                WHERE id = :id
                RETURNING id, name, url, check_interval, timeout, expected_status_code,
                          webhook_url, enabled, created_at, status
            """),
            {
                "id": monitor_id,
                "name": m.name,
                "url": str(m.url),
                "check_interval": m.check_interval,
                "timeout": m.timeout,
                "expected_status_code": m.expected_status_code,
                "webhook_url": m.webhook_url,
                "enabled": m.enabled,
            }
        ).mappings().first()
        
        if not res:
            raise HTTPException(status_code=404, detail="Monitor not found")
    
    return dict(res)


@app.delete("/monitors/{monitor_id}")
def delete_monitor(monitor_id: int):
    with engine.begin() as conn:
        res = conn.execute(
            text("DELETE FROM monitors WHERE id = :id"),
            {"id": monitor_id},
        )
        if res.rowcount == 0:
            raise HTTPException(status_code=404, detail="Monitor not found")

    return {"message": "deleted"}