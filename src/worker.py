import os
import json
import time
from datetime import datetime, timezone

import boto3
import requests
from sqlalchemy import create_engine, text


DATABASE_URL = os.getenv("DATABASE_URL")
SQS_QUEUE_URL = os.getenv("SQS_QUEUE_URL")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")

FAIL_THRESHOLD = int(os.getenv("FAIL_THRESHOLD", "2"))
SUCCESS_THRESHOLD = int(os.getenv("SUCCESS_THRESHOLD", "2"))

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set")
if not SQS_QUEUE_URL:
    raise RuntimeError("SQS_QUEUE_URL is not set")

engine = create_engine(DATABASE_URL)

# AWS ONLY (no LocalStack)
sqs = boto3.client("sqs", region_name=AWS_REGION)


def post_webhook(webhook_url: str, payload: dict):
    """
    Slack Incoming Webhook expects: {"text": "..."}
    """
    if not webhook_url:
        print("[WEBHOOK] webhook_url is empty, skipping")
        return

    try:
        text_msg = (
            f" *API Status Changed*\n"
            f"*Name:* {payload.get('name')}\n"
            f"*URL:* {payload.get('url')}\n"
            f"*Old:* {payload.get('old_status')} -> *New:* {payload.get('new_status')}\n"
            f"*Time:* {payload.get('time')}"
        )

        resp = requests.post(
            webhook_url,
            json={"text": text_msg},
            timeout=15,
        )

        print(f"[WEBHOOK] sent status={resp.status_code}")

        if resp.status_code >= 300:
            print(f"[WEBHOOK ERROR] body={resp.text}")

    except Exception as e:
        print(f"[WEBHOOK EXCEPTION] {repr(e)}")


def check_url(url: str, timeout: int, expected_status_code: int) -> bool:
    try:
        r = requests.get(url, timeout=timeout)
        return r.status_code == expected_status_code
    except Exception:
        return False


def update_state(monitor_id: int, is_up: bool):
    """
    Update DB counters and status.
    Send Slack ONLY when status changes UP<->DOWN (or UNKNOWN->UP/DOWN).
    """
    with engine.begin() as conn:
        row = conn.execute(
            text("""
                SELECT id, name, url, webhook_url, status,
                       consecutive_failures, consecutive_successes
                FROM monitors
                WHERE id = :id
            """),
            {"id": monitor_id},
        ).mappings().first()

        if not row:
            print(f"[WORKER] Monitor id={monitor_id} not found in DB")
            return

        old_status = row["status"] or "UNKNOWN"
        failures = row["consecutive_failures"] or 0
        successes = row["consecutive_successes"] or 0

        new_status = old_status

        if is_up:
            successes += 1
            failures = 0
            if old_status != "UP" and successes >= SUCCESS_THRESHOLD:
                new_status = "UP"
        else:
            failures += 1
            successes = 0
            if old_status != "DOWN" and failures >= FAIL_THRESHOLD:
                new_status = "DOWN"

        conn.execute(
            text("""
                UPDATE monitors
                SET status = :status,
                    consecutive_failures = :failures,
                    consecutive_successes = :successes
                WHERE id = :id
            """),
            {
                "id": monitor_id,
                "status": new_status,
                "failures": failures,
                "successes": successes,
            },
        )

        if new_status != old_status and new_status in ["UP", "DOWN"]:
            print(f"[STATE CHANGE] monitor={monitor_id} {old_status} -> {new_status}")

            post_webhook(
                row["webhook_url"],
                {
                    "monitor_id": row["id"],
                    "name": row["name"],
                    "url": row["url"],
                    "old_status": old_status,
                    "new_status": new_status,
                    "time": datetime.now(timezone.utc).isoformat(),
                },
            )


def process_message(body: dict):
    monitor_id = body.get("monitor_id")
    url = body.get("url")

    if monitor_id is None or not url:
        print(f"[WORKER] Invalid message body: {body}")
        return

    monitor_id = int(monitor_id)
    timeout = int(body.get("timeout", 5))
    expected = int(body.get("expected_status_code", 200))

    ok = check_url(url, timeout, expected)
    update_state(monitor_id, ok)


def main():
    print("[WORKER] Started (AWS ONLY)")
    print(f"[WORKER] AWS_REGION={AWS_REGION}")
    print(f"[WORKER] SQS_QUEUE_URL={SQS_QUEUE_URL}")

    while True:
        try:
            resp = sqs.receive_message(
                QueueUrl=SQS_QUEUE_URL,
                MaxNumberOfMessages=5,
                WaitTimeSeconds=10,
            )

            for msg in resp.get("Messages", []):
                try:
                    body = json.loads(msg["Body"])
                    process_message(body)

                    sqs.delete_message(
                        QueueUrl=SQS_QUEUE_URL,
                        ReceiptHandle=msg["ReceiptHandle"],
                    )
                except Exception as inner_e:
                    print(f"[WORKER MESSAGE ERROR] {repr(inner_e)} body={msg.get('Body')}")

        except Exception as e:
            print(f"[WORKER ERROR] {repr(e)}")
            time.sleep(2)


if __name__ == "__main__":
    main()

