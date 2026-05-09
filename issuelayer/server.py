"""
server.py — FastAPI entry point.

Run:
    uvicorn server:app --host 0.0.0.0 --port 8765 --reload
"""

import os
import re
import json
import uuid
import hashlib
import hmac
import threading
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

load_dotenv()

QUEUE_FILE     = os.getenv("QUEUE_FILE", "data/event_queue.json")
WEBHOOK_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET", "")
_lock          = threading.Lock()

os.makedirs("data", exist_ok=True)
if not os.path.exists(QUEUE_FILE):
    with open(QUEUE_FILE, "w") as f:
        json.dump([], f)


# ── Queue helpers ──────────────────────────────────────────────────────────────

def _read():
    try:
        with open(QUEUE_FILE) as f:
            return json.load(f)
    except Exception:
        return []


def _write(events):
    with open(QUEUE_FILE, "w") as f:
        json.dump(events, f, indent=2, default=str)


def _queue_stats():
    counts = {"pending": 0, "running": 0, "done": 0, "failed": 0, "total": 0}
    for e in _read():
        s = e.get("status", "pending")
        counts[s] = counts.get(s, 0) + 1
        counts["total"] += 1
    return counts


def _push(event: dict) -> bool:
    with _lock:
        events = _read()
        active = {e["fingerprint"] for e in events if e["status"] in ("pending", "running")}
        if event["fingerprint"] in active:
            print(f"[queue] Deduplicated: {event['fingerprint']}")
            return False
        events.append(event)
        _write(events)
        print(f"[queue] Pushed {event['id'][:8]} | {event['error_type']}")
        return True


# ── Parsing helpers ────────────────────────────────────────────────────────────

def _make_fingerprint(error_type: str, message: str) -> str:
    raw = f"{error_type}::{message}".lower().strip()
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _extract_traceback(text: str) -> str:
    # Check inside code fences first
    for fence in re.finditer(r"```(?:\w+)?\n(.*?)```", text, re.DOTALL):
        m = re.search(r"(Traceback \(most recent call last\):.*?)(?=\n\n|\Z)", fence.group(1), re.DOTALL)
        if m:
            return m.group(1).strip()
    m = re.search(r"(Traceback \(most recent call last\):.*?)(?=\n\n|\Z)", text, re.DOTALL)
    return m.group(1).strip() if m else ""


def _extract_error_type_message(tb: str, title: str):
    pattern = re.compile(r"^([A-Za-z][A-Za-z0-9_]*(?:Error|Exception|Warning)):\s*(.+)$", re.MULTILINE)
    matches = list(pattern.finditer(tb))
    if matches:
        m = matches[-1]
        return m.group(1), m.group(2).strip()
    m = pattern.match(title.strip())
    if m:
        return m.group(1), m.group(2).strip()
    return "Error", title.strip()


def _extract_file_info(tb: str):
    matches = list(re.finditer(r'File "([^"]+)", line (\d+), in (\S+)', tb))
    if not matches:
        return "", 0, ""
    last = matches[-1]
    return last.group(1), int(last.group(2)), last.group(3)


def _is_incident_style(body: str) -> bool:
    markers = ["incident id", "configuration item", "priority:", "resolution:", "closure code", "open date"]
    body_lower = body.lower()
    return sum(1 for m in markers if m in body_lower) >= 2


def _extract_incident_fields(text: str) -> dict:
    patterns = {
        "incident_id":          r"(?:Incident\s*ID|ID)\s*[:\-]\s*(.+)",
        "priority":             r"Priority\s*[:\-]\s*(.+)",
        "configuration_item":   r"(?:Configuration\s*Item|CI)\s*[:\-]\s*(.+)",
        "incident_status":      r"Status\s*[:\-]\s*(.+)",
        "resolution":           r"Resolution\s*[:\-]\s*(.+)",
    }
    result = {"description": text[:1000].strip()}
    for field, pattern in patterns.items():
        m = re.search(pattern, text, re.I)
        if m:
            value = m.group(1).strip().strip("*").strip()
            if value:
                result[field] = value
    return result


# ── Signature verification ─────────────────────────────────────────────────────

def _verify_sig(payload_bytes: bytes, sig_header: str) -> bool:
    if not WEBHOOK_SECRET:
        return True
    if not sig_header or not sig_header.startswith("sha256="):
        return False
    expected = hmac.new(WEBHOOK_SECRET.encode(), payload_bytes, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig_header[7:])


# ── App ────────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("=" * 52)
    print("Code Fixer Agent — starting up")
    print(f"Queue : {QUEUE_FILE}")
    print(f"Stats : {_queue_stats()}")
    print("=" * 52)
    yield
    print("Shutting down.")


app = FastAPI(title="Code Fixer Agent", version="0.2.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ── Health + queue routes ──────────────────────────────────────────────────────

@app.get("/")
def root():
    return {"status": "ok", "service": "code-fixer-agent", "version": "0.2.0",
            "endpoints": {"webhook": "POST /intake/github", "queue": "GET /queue",
                          "stats": "GET /queue/stats", "health": "GET /health"}}


@app.get("/health")
def health():
    return {"status": "ok", "queue": _queue_stats()}


@app.get("/queue/stats")
def stats():
    return _queue_stats()


@app.get("/queue")
def get_queue():
    return _read()


@app.get("/queue/{event_id}")
def get_event(event_id: str):
    for e in _read():
        if e["id"] == event_id:
            return e
    raise HTTPException(404, "Event not found")


@app.delete("/queue/{event_id}")
def delete_event(event_id: str):
    with _lock:
        _write([e for e in _read() if e["id"] != event_id])
    return {"status": "deleted"}


@app.delete("/queue")
def clear_queue():
    with _lock:
        _write([])
    return {"status": "cleared"}


# ── GitHub webhook ─────────────────────────────────────────────────────────────

@app.post("/intake/github")
async def github_webhook(
    request: Request,
    x_github_event: Optional[str] = Header(None),
    x_hub_signature_256: Optional[str] = Header(None),
    x_github_delivery: Optional[str] = Header(None),
):
    payload_bytes = await request.body()

    if not _verify_sig(payload_bytes, x_hub_signature_256 or ""):
        raise HTTPException(401, "Invalid signature")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(400, "Invalid JSON")

    event_type = x_github_event or "unknown"
    print(f"[webhook] {event_type} (delivery: {x_github_delivery})")

    if event_type != "issues":
        return JSONResponse({"status": "ignored", "reason": f"{event_type} not handled"})

    action = payload.get("action", "")
    if action not in ("opened", "labeled", "reopened"):
        return JSONResponse({"status": "ignored", "reason": f"action '{action}' skipped"})

    issue      = payload.get("issue", {})
    repository = payload.get("repository", {})

    labels     = [l.get("name", "").lower() for l in issue.get("labels", [])]
    bug_labels = {"bug", "error", "fix", "critical", "regression", "crash", "incident"}
    if not any(l in bug_labels for l in labels):
        return JSONResponse({"status": "ignored", "reason": "no relevant label"})

    repo_url       = repository.get("clone_url") or repository.get("html_url", "")
    repo_full_name = repository.get("full_name", "")
    if not repo_url:
        return JSONResponse({"status": "ignored", "reason": "missing repo url"})

    issue_number = issue.get("number", 0)
    issue_title  = issue.get("title", "Unknown error")
    issue_body   = issue.get("body") or ""
    issue_url    = issue.get("html_url", "")

    # ── Parse technical fields ──────────────────────────────────
    tb                              = _extract_traceback(issue_body)
    error_type, message             = _extract_error_type_message(tb, issue_title)
    file_path, line_number, fn_name = _extract_file_info(tb)
    sha_m                           = re.search(r"\b([0-9a-f]{7,40})\b", issue_body)
    commit_sha                      = sha_m.group(1) if sha_m else ""

    # ── Parse incident fields if present ────────────────────────
    incident_data = {}
    if _is_incident_style(issue_body):
        incident_data = _extract_incident_fields(issue_body)
        if not tb:
            error_type = "Incident"
            message    = incident_data.get("description", issue_title)[:300]

    event = {
        # Identity
        "id":                   str(uuid.uuid4()),
        "fingerprint":          _make_fingerprint(error_type, message),
        "timestamp":            datetime.utcnow().isoformat(),

        # Technical
        "error_type":           error_type,
        "message":              message,
        "traceback":            tb,
        "file_path":            file_path,
        "function_name":        fn_name,
        "line_number":          line_number,

        # Incident (only populated if incident-style)
        "incident_id":          incident_data.get("incident_id"),
        "description":          incident_data.get("description"),
        "priority":             incident_data.get("priority"),
        "configuration_item":   incident_data.get("configuration_item"),
        "incident_status":      incident_data.get("incident_status"),
        "resolution":           incident_data.get("resolution"),

        # Repo
        "repo_url":             repo_url,
        "repo_full_name":       repo_full_name,
        "commit_sha":           commit_sha,
        "branch":               repository.get("default_branch", "main"),

        # Context
        "environment":          "production",
        "source":               "github_issue",
        "github_issue_number":  issue_number,
        "github_issue_url":     issue_url,
        "github_issue_body":    issue_body,
        "github_issue_labels":  labels,

        # State
        "status":               "pending",
    }

    queued = _push(event)

    if queued:
        print(f"[webhook] Queued issue #{issue_number} | {error_type}")
        return JSONResponse(status_code=202, content={
            "status":       "queued",
            "event_id":     event["id"],
            "fingerprint":  event["fingerprint"],
            "issue_number": issue_number,
            "error_type":   error_type,
            "has_traceback": bool(tb),
            "is_incident":   bool(incident_data),
        })

    return JSONResponse({"status": "deduplicated", "fingerprint": event["fingerprint"]})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8765, reload=True)