import os
from sqlalchemy import create_engine, text

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set")

engine = create_engine(DATABASE_URL)

DDL = """
CREATE TABLE IF NOT EXISTS monitors (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    url TEXT NOT NULL,
    check_interval INTEGER NOT NULL DEFAULT 60,
    timeout INTEGER NOT NULL DEFAULT 5,
    expected_status_code INTEGER NOT NULL DEFAULT 200,
    webhook_url TEXT,
    enabled BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    last_checked_at TIMESTAMP,

    status TEXT NOT NULL DEFAULT 'UNKNOWN',
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    consecutive_successes INTEGER NOT NULL DEFAULT 0
);
"""

def main():
    with engine.begin() as conn:
        conn.execute(text(DDL))
    print("[DB INIT] monitors table ensured")

if __name__ == "__main__":
    main()