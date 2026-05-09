"""
intake/schemas.py

ErrorEvent — the single normalised contract that flows through
the entire system from intake → code fix agent.

A single GitHub Issue contains both pieces of information together:

  - Incident metadata: ID, priority, configuration item, status, resolution
    (written in ServiceNow style in the issue body)

  - Technical log: traceback, file path, line number, function name
    (pasted as a stack trace in the same issue body)

The normaliser extracts both from the one issue body simultaneously.
If a traceback is present → file_path, line_number, function_name are populated.
If no traceback → those fields are empty and the code fix agent
will infer the location from incident_id, description, and resolution.
"""

import hashlib
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field


class ErrorEvent(BaseModel):

    # ── Identity ───────────────────────────────────────────────
    id: str                          # uuid — unique per event
    fingerprint: str                 # sha256(error_type::message)[:16]
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    # ── Technical error fields ─────────────────────────────────
    # Extracted from traceback when present.
    # Left empty when issue is incident-style (no traceback).
    error_type: str                  # KeyError, TypeError, or "Incident"
    message: str                     # exception message or incident title
    traceback: str = ""              # full stack trace if available
    file_path: str = ""              # innermost file from traceback
    function_name: str = ""          # innermost function from traceback
    line_number: int = 0             # innermost line from traceback

    # ── Incident fields ────────────────────────────────────────
    # Populated when the issue body contains ServiceNow-style data.
    # All optional — not every issue will have these.
    incident_id: Optional[str] = None          # e.g. "121472"
    description: Optional[str] = None          # full incident description
    priority: Optional[str] = None             # High / Medium / Low
    configuration_item: Optional[str] = None   # e.g. "PolicyCenter"
    incident_status: Optional[str] = None      # Open / Closed / In Progress
    resolution: Optional[str] = None           # how it was resolved

    # ── Repo info ──────────────────────────────────────────────
    repo_url: str
    repo_full_name: str
    commit_sha: str = ""
    branch: str = "main"

    # ── Context ────────────────────────────────────────────────
    environment: str = "production"
    source: str = "github_issue"

    # ── GitHub issue fields ────────────────────────────────────
    github_issue_number: Optional[int] = None
    github_issue_url: Optional[str] = None
    github_issue_body: Optional[str] = None
    github_issue_labels: List[str] = Field(default_factory=list)
    github_issue_comments: List[str] = Field(default_factory=list)

    # ── Pipeline state ─────────────────────────────────────────
    status: str = "pending"          # pending → running → done | failed


def make_fingerprint(error_type: str, message: str) -> str:
    """
    Stable deduplication key from error_type + message.
    Same bug reported twice = same fingerprint = one job in queue.
    """
    raw = f"{error_type}::{message}".lower().strip()
    return hashlib.sha256(raw.encode()).hexdigest()[:16]