"""
intake/sources/github_webhook.py

FastAPI router that receives GitHub webhook events.

GitHub sends a POST request to /intake/github whenever an issue
event occurs on your configured repository.

Setup (do this once):
    1. Go to your GitHub repo → Settings → Webhooks → Add webhook
    2. Payload URL: http://your-server/intake/github
    3. Content type: application/json
    4. Secret: set GITHUB_WEBHOOK_SECRET in your .env
    5. Events: select "Issues" only (or "Let me select" → Issues)

Security:
    GitHub signs every webhook payload with HMAC-SHA256 using your
    secret. We verify this signature before processing anything.
    If GITHUB_WEBHOOK_SECRET is not set, signature checking is
    skipped — fine for local dev, never do this in production.
"""

import hashlib
import hmac
import os

from fastapi import APIRouter, Request, HTTPException, Header
from fastapi.responses import JSONResponse
from typing import Optional

from intake.normaliser import normalise_github_issue
from intake.queue import queue

router = APIRouter(prefix="/intake", tags=["intake"])

WEBHOOK_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET", "")


# ── Signature verification ─────────────────────────────────────────────────────

def _verify_signature(payload_bytes: bytes, signature_header: str) -> bool:
    """
    Verify GitHub's HMAC-SHA256 signature.
    GitHub sends: X-Hub-Signature-256: sha256=<hex_digest>
    We recompute and compare — constant-time to prevent timing attacks.
    """
    if not WEBHOOK_SECRET:
        # Skip verification in dev if no secret configured
        return True

    if not signature_header or not signature_header.startswith("sha256="):
        return False

    expected = hmac.new(
        WEBHOOK_SECRET.encode(),
        payload_bytes,
        hashlib.sha256,
    ).hexdigest()

    received = signature_header[len("sha256="):]
    return hmac.compare_digest(expected, received)


# ── Webhook route ──────────────────────────────────────────────────────────────

@router.post("/github")
async def github_webhook(
    request: Request,
    x_github_event: Optional[str] = Header(None),
    x_hub_signature_256: Optional[str] = Header(None),
    x_github_delivery: Optional[str] = Header(None),
):
    """
    Receive GitHub webhook events.

    We only care about 'issues' events. Everything else is
    acknowledged (200 OK) but not processed — GitHub expects
    a quick response or it marks the delivery as failed.
    """

    # ── Read raw body for signature verification ───────────────
    payload_bytes = await request.body()

    # ── Verify signature ───────────────────────────────────────
    if not _verify_signature(payload_bytes, x_hub_signature_256 or ""):
        raise HTTPException(status_code=401, detail="Invalid signature")

    # ── Parse JSON ─────────────────────────────────────────────
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    delivery_id = x_github_delivery or "unknown"
    event_type = x_github_event or "unknown"

    print(f"[webhook] Received {event_type} event (delivery: {delivery_id})")

    # ── Ignore non-issue events ────────────────────────────────
    # GitHub sends many event types — we only handle 'issues'
    if event_type != "issues":
        return JSONResponse(
            status_code=200,
            content={"status": "ignored", "reason": f"event type '{event_type}' not handled"},
        )

    # ── Normalise to ErrorEvent ────────────────────────────────
    event = normalise_github_issue(payload)

    if event is None:
        action = payload.get("action", "unknown")
        return JSONResponse(
            status_code=200,
            content={"status": "ignored", "reason": f"action '{action}' not applicable"},
        )

    # ── Push to queue ──────────────────────────────────────────
    was_queued = queue.push(event)

    if was_queued:
        print(f"[webhook] Queued event {event.id} for issue #{event.github_issue_number}")
        return JSONResponse(
            status_code=202,
            content={
                "status": "queued",
                "event_id": event.id,
                "fingerprint": event.fingerprint,
                "issue_number": event.github_issue_number,
                "error_type": event.error_type,
            },
        )
    else:
        print(f"[webhook] Deduplicated event for issue #{event.github_issue_number}")
        return JSONResponse(
            status_code=200,
            content={
                "status": "deduplicated",
                "fingerprint": event.fingerprint,
                "message": "Same error already in queue",
            },
        )