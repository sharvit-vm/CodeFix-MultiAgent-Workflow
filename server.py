"""
server.py — FastAPI webhook server
"""

import os
import hmac
import hashlib
import subprocess
import traceback
from urllib.parse import urlparse
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
from dotenv import load_dotenv

load_dotenv(override=True)

from issuelayer.intake.normaliser import normalise_github_issue
from issuelayer.intake.queue import EventQueue
from issuelayer.intake.schemas import ErrorEvent

app = FastAPI(title="CodeFix Webhook Server")
queue = EventQueue()

WEBHOOK_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET", "")
CLONE_BASE     = os.getenv("CLONE_DIR", "clone")


def _clone_or_pull(repo_url: str) -> tuple:
    """Clone repo into clone/<repo_name>/ or pull if already exists.
    Returns (repo_path, knowledge_id) — both stable per repo URL."""
    path     = urlparse(repo_url).path.rstrip("/")
    name     = path.split("/")[-1].removesuffix(".git") or "repo"
    kid      = hashlib.md5(repo_url.strip().lower().encode()).hexdigest()[:8]
    repo_path = os.path.join(CLONE_BASE, name)
    os.makedirs(CLONE_BASE, exist_ok=True)
    if os.path.isdir(os.path.join(repo_path, ".git")):
        subprocess.run(["git", "pull"], cwd=repo_path, capture_output=True)
        print(f"[clone] Pulled latest — {repo_path}")
    else:
        result = subprocess.run(["git", "clone", repo_url, repo_path], capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"git clone failed: {result.stderr.strip()}")
        print(f"[clone] Cloned {repo_url} → {repo_path}")
    return repo_path, kid


def _verify_signature(payload: bytes, sig_header: str) -> bool:
    if not WEBHOOK_SECRET:
        return True
    if not sig_header or not sig_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(
        WEBHOOK_SECRET.encode(), payload, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, sig_header)


async def _run_rca_and_fix(event: ErrorEvent):
    try:
        from agents.rca import run_rca
        from models import PipelineState
        from phases.scanner import scan_repo
        from phases.file_analysis import analyze_files
        from phases.llm_analysis import analyze_with_llm
        from phases.hierarchy import build_hierarchy
        from phases.neo4j_ingest import neo4j_ingest

        repo_path, knowledge_id = _clone_or_pull(event.repo_url)

        queue.update_status(event.id, "ingesting")
        print(f"[worker] Running ingestion for {repo_path} (id={knowledge_id})")
        state = PipelineState(repo_path=repo_path, knowledge_id=knowledge_id)
        state = scan_repo(state)
        state = analyze_files(state)
        state = analyze_with_llm(state)
        state = build_hierarchy(state)
        state = neo4j_ingest(state)
        print(f"[worker] Ingestion done — {len(state.files)} files")

        queue.update_status(event.id, "running")
        print(f"[worker] Running RCA for event {event.id} ({event.error_type})")

        rca_result = run_rca(event, knowledge_id)

        print(f"[worker] RCA done — confidence={rca_result.confidence}, file={rca_result.buggy_file}")
        print(f"[worker] Root cause: {rca_result.root_cause}")

        queue.update_status(event.id, "rca_done", extra={
            "rca_result": rca_result.model_dump()
        })

        # ── Code Fix Agent ──
        print(f"[worker] Starting code fix agent...")
        from agents.code_fix import run_code_fix
        fix_result = run_code_fix(event, rca_result, knowledge_id)

        print(f"[worker] Code fix done — success={fix_result.success}")
        if fix_result.error:
            print(f"[worker] Code fix error: {fix_result.error}")
        if fix_result.pr_url:
            print(f"[worker] PR opened: {fix_result.pr_url}")

        status = "done" if fix_result.success else "fix_failed"
        queue.update_status(event.id, status, extra={"fix_result": fix_result.model_dump()})

    except Exception as e:
        print(f"[worker] Pipeline failed for {event.id}: {e}")
        print(traceback.format_exc())
        queue.update_status(event.id, "failed", extra={"error": str(e)})


@app.post("/webhook/github")
async def github_webhook(request: Request, background_tasks: BackgroundTasks):
    payload_bytes = await request.body()
    sig = request.headers.get("X-Hub-Signature-256", "")

    if not _verify_signature(payload_bytes, sig):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    payload = await request.json()
    event_type = request.headers.get("X-GitHub-Event", "")
    action = payload.get("action", "")

    if event_type != "issues":
        return JSONResponse({"status": "ignored", "reason": f"event={event_type}"})

    # Only process when issue is first opened — ignore labeled/edited/etc
    if action != "opened":
        return JSONResponse({"status": "ignored", "reason": f"action={action}"})

    error_event = normalise_github_issue(payload)

    if error_event is None:
        return JSONResponse({"status": "ignored", "reason": "no bug label or no traceback found"})

    pushed = queue.push(error_event)
    if not pushed:
        return JSONResponse({"status": "deduplicated", "fingerprint": error_event.fingerprint})

    background_tasks.add_task(_run_rca_and_fix, error_event)

    print(f"[server] Queued {error_event.id} — {error_event.error_type}: {error_event.message[:80]}")

    return JSONResponse({
        "status":      "queued",
        "event_id":    error_event.id,
        "fingerprint": error_event.fingerprint,
        "error_type":  error_event.error_type,
    })


@app.get("/queue")
async def get_queue():
    events = queue.all_events()
    return {"stats": queue.stats(), "events": [e.model_dump() for e in events]}


@app.get("/queue/{event_id}")
async def get_event(event_id: str):
    event = queue.get(event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    return event.model_dump()


@app.get("/health")
async def health():
    return {"status": "ok", "queue_stats": queue.stats()}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)