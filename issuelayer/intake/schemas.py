"""
intake/schemas.py

ErrorEvent — the normalised contract that flows from intake
through to the code fix agent.

A single GitHub Issue contains both:
  - Incident metadata: ID, priority, configuration item, status, resolution
  - Technical log: traceback with file path, line number, function name

Both are extracted simultaneously from the same issue body.
"""

import hashlib
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field


class ErrorEvent(BaseModel):

    # ── Identity ───────────────────────────────────────────────
    id: str
    fingerprint: str                 # sha256(error_type::message)[:16]
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    # ── Technical fields (from traceback) ─────────────────────
    error_type: str                  # KeyError, AttributeError, or "Incident"
    message: str                     # exception message or incident title
    traceback: str = ""
    file_path: str = ""              # innermost file from traceback
    function_name: str = ""          # innermost function from traceback
    line_number: int = 0

    # ── Incident fields (from ServiceNow-style issue body) ─────
    incident_id: Optional[str] = None
    description: Optional[str] = None
    priority: Optional[str] = None             # High / Medium / Low
    configuration_item: Optional[str] = None   # e.g. PolicyCenter
    incident_status: Optional[str] = None      # Open / Closed
    resolution: Optional[str] = None

    # ── Repo info ──────────────────────────────────────────────
    repo_url: str
    repo_full_name: str
    commit_sha: str = ""
    branch: str = "main"

    # ── Context ────────────────────────────────────────────────
    environment: str = "production"
    frequency: int = 1
    source: str = "github_issue"

    # ── GitHub fields ──────────────────────────────────────────
    github_issue_number: Optional[int] = None
    github_issue_url: Optional[str] = None
    github_issue_body: Optional[str] = None
    github_issue_labels: List[str] = Field(default_factory=list)
    github_issue_comments: List[str] = Field(default_factory=list)

    # ── Pipeline state ─────────────────────────────────────────
    status: str = "pending"


def make_fingerprint(error_type: str, message: str) -> str:
    raw = f"{error_type}::{message}".lower().strip()
    return hashlib.sha256(raw.encode()).hexdigest()[:16]