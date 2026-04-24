import os
import json
import threading
from contextlib import asynccontextmanager
from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from typing import Optional

load_dotenv()

# ── Inline queue (no import needed) ───────────────────────────────────────────
QUEUE_FILE = os.getenv("QUEUE_FILE", "data/event_queue.json")
_lock = threading.Lock()

os.makedirs("data", exist_ok=True)
if not os.path.exists(QUEUE_FILE):
    with open(QUEUE_FILE, "w") as f:
        json.dump([], f)


def _read():
    try:
        with open(QUEUE_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return []


def _write(events):
    with open(QUEUE_FILE, "w") as f:
        json.dump(events, f, indent=2, default=str)


def queue_stats():
    events = _read()
    counts = {"pending": 0, "running": 0, "done": 0, "failed": 0, "total": 0}
    for e in events:
        s = e.get("status", "pending")
        counts[s] = counts.get(s, 0) + 1
        counts["total"] += 1
    return counts


# ── FastAPI app ────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    print("=" * 50)
    print("Code Fixer Agent — starting up")
    print(f"Queue file : {QUEUE_FILE}")
    print(f"Queue stats: {queue_stats()}")
    print("=" * 50)
    yield
    print("Shutting down.")


app = FastAPI(
    title="Code Fixer Agent",
    description="Multi-agent code fixing pipeline triggered by GitHub Issues",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Routes ─────────────────────────────────────────────────────────────────────
@app.get("/")
def root():
    return {
        "status": "ok",
        "service": "code-fixer-agent",
        "endpoints": {
            "webhook": "POST /intake/github",
            "queue":   "GET  /queue",
            "stats":   "GET  /queue/stats",
            "health":  "GET  /health",
        },
    }


@app.get("/health")
def health():
    return {"status": "ok", "queue": queue_stats()}


@app.get("/queue/stats")
def get_stats():
    return queue_stats()


@app.get("/queue")
def get_queue():
    return _read()


@app.get("/queue/{event_id}")
def get_event(event_id: str):
    for e in _read():
        if e["id"] == event_id:
            return e
    raise HTTPException(status_code=404, detail="Event not found")


@app.delete("/queue/{event_id}")
def delete_event(event_id: str):
    with _lock:
        events = [e for e in _read() if e["id"] != event_id]
        _write(events)
    return {"status": "deleted"}


@app.delete("/queue")
def clear_all():
    with _lock:
        _write([])
    return {"status": "cleared"}


# ── GitHub webhook ─────────────────────────────────────────────────────────────
import hashlib
import hmac
import uuid
import re
from datetime import datetime

WEBHOOK_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET", "")


def verify_signature(payload_bytes: bytes, sig_header: str) -> bool:
    if not WEBHOOK_SECRET:
        return True
    if not sig_header or not sig_header.startswith("sha256="):
        return False
    expected = hmac.new(
        WEBHOOK_SECRET.encode(), payload_bytes, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, sig_header[7:])


def extract_traceback(text: str) -> str:
    match = re.search(
        r"(Traceback \(most recent call last\):.*?)(?=\n\n|\Z)",
        text, re.DOTALL
    )
    return match.group(1).strip() if match else ""


def extract_error_type_message(tb: str, title: str):
    pattern = re.compile(
        r"^([A-Za-z][A-Za-z0-9_]*(?:Error|Exception|Warning)):\s*(.+)$",
        re.MULTILINE
    )
    matches = list(pattern.finditer(tb))
    if matches:
        m = matches[-1]
        return m.group(1), m.group(2).strip()
    m = pattern.match(title.strip())
    if m:
        return m.group(1), m.group(2).strip()
    return "Error", title.strip()


def extract_file_info(tb: str):
    matches = list(re.finditer(r'File "([^"]+)", line (\d+), in (\S+)', tb))
    if not matches:
        return "", 0, ""
    last = matches[-1]
    return last.group(1), int(last.group(2)), last.group(3)


def make_fingerprint(error_type: str, message: str) -> str:
    raw = f"{error_type}::{message}".lower().strip()
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def push_event(event: dict) -> bool:
    with _lock:
        events = _read()
        active = {e["fingerprint"] for e in events if e["status"] in ("pending", "running")}
        if event["fingerprint"] in active:
            print(f"[queue] Deduplicated: {event['fingerprint']} already active")
            return False
        events.append(event)
        _write(events)
        print(f"[queue] Pushed event {event['id']} ({event['error_type']})")
        return True


@app.post("/intake/github")
async def github_webhook(
    request: Request,
    x_github_event: Optional[str] = Header(None),
    x_hub_signature_256: Optional[str] = Header(None),
    x_github_delivery: Optional[str] = Header(None),
):
    payload_bytes = await request.body()

    if not verify_signature(payload_bytes, x_hub_signature_256 or ""):
        raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    event_type = x_github_event or "unknown"
    print(f"[webhook] Received {event_type} event (delivery: {x_github_delivery})")

    if event_type != "issues":
        return JSONResponse({"status": "ignored", "reason": f"{event_type} not handled"})

    action = payload.get("action", "")
    if action not in ("opened", "labeled", "reopened"):
        return JSONResponse({"status": "ignored", "reason": f"action {action} not applicable"})

    issue = payload.get("issue", {})
    repository = payload.get("repository", {})

    labels = [l.get("name", "").lower() for l in issue.get("labels", [])]
    bug_labels = {"bug", "error", "fix", "critical", "regression", "crash"}
    if not any(l in bug_labels for l in labels):
        return JSONResponse({"status": "ignored", "reason": "no bug label"})

    repo_url = repository.get("clone_url") or repository.get("html_url", "")
    repo_full_name = repository.get("full_name", "")
    if not repo_url:
        return JSONResponse({"status": "ignored", "reason": "missing repo url"})

    issue_number = issue.get("number", 0)
    issue_title  = issue.get("title", "Unknown error")
    issue_body   = issue.get("body") or ""
    issue_url    = issue.get("html_url", "")

    tb = extract_traceback(issue_body)
    error_type, message = extract_error_type_message(tb, issue_title)
    file_path, line_number, function_name = extract_file_info(tb)

    sha_match = re.search(r"\b([0-9a-f]{7,40})\b", issue_body)
    commit_sha = sha_match.group(1) if sha_match else ""

    event = {
        "id":                    str(uuid.uuid4()),
        "fingerprint":           make_fingerprint(error_type, message),
        "timestamp":             datetime.utcnow().isoformat(),
        "error_type":            error_type,
        "message":               message,
        "traceback":             tb,
        "file_path":             file_path,
        "function_name":         function_name,
        "line_number":           line_number,
        "repo_url":              repo_url,
        "repo_full_name":        repo_full_name,
        "commit_sha":            commit_sha,
        "branch":                repository.get("default_branch", "main"),
        "environment":           "production",
        "source":                "github_issue",
        "github_issue_number":   issue_number,
        "github_issue_url":      issue_url,
        "github_issue_body":     issue_body,
        "github_issue_labels":   labels,
        "status":                "pending",
    }

    queued = push_event(event)

    if queued:
        print(f"[webhook] Queued event for issue #{issue_number}")
        return JSONResponse(status_code=202, content={
            "status":       "queued",
            "event_id":     event["id"],
            "fingerprint":  event["fingerprint"],
            "issue_number": issue_number,
            "error_type":   error_type,
        })
    else:
        return JSONResponse({"status": "deduplicated", "fingerprint": event["fingerprint"]})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8765, reload=True)