import os
import time
import json
from datetime import datetime, timezone

import boto3
from sqlalchemy import create_engine, text


DATABASE_URL = os.getenv("DATABASE_URL")
SQS_QUEUE_URL = os.getenv("SQS_QUEUE_URL")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set")
if not SQS_QUEUE_URL:
    raise RuntimeError("SQS_QUEUE_URL is not set")

engine = create_engine(DATABASE_URL)


sqs = boto3.client("sqs", region_name=AWS_REGION)


def fetch_monitors_due_for_check():
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT id, url, timeout, expected_status_code, webhook_url, check_interval
                FROM monitors
                WHERE enabled = true
                  AND (last_checked_at IS NULL
                       OR EXTRACT(EPOCH FROM (NOW() - last_checked_at)) >= check_interval)
            """)
        ).mappings().all()
        return rows


def mark_monitor_checked(monitor_id: int):
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE monitors SET last_checked_at = NOW() WHERE id = :id"),
            {"id": monitor_id},
        )


def send_to_sqs(monitor):
    payload = {
        "monitor_id": monitor["id"],
        "url": monitor["url"],
        "timeout": monitor["timeout"],
        "expected_status_code": monitor["expected_status_code"],
        "webhook_url": monitor["webhook_url"],
        "sent_at": datetime.now(timezone.utc).isoformat(),
    }

    sqs.send_message(
        QueueUrl=SQS_QUEUE_URL,
        MessageBody=json.dumps(payload),
    )


def main():
    print("[SCHEDULER] Started (AWS ONLY)")
    print(f"[SCHEDULER] AWS_REGION={AWS_REGION}")
    print(f"[SCHEDULER] SQS_QUEUE_URL={SQS_QUEUE_URL}")

    while True:
        try:
            monitors = fetch_monitors_due_for_check()

            for m in monitors:
                send_to_sqs(m)
                mark_monitor_checked(m["id"])

            time.sleep(10)

        except Exception as e:
            print(f"[SCHEDULER ERROR] {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()
