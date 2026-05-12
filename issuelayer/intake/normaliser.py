"""
intake/normaliser.py

Converts a raw GitHub Issues webhook payload into a clean ErrorEvent.

A single GitHub Issue body contains both incident metadata
(ServiceNow-style fields) AND a technical traceback. Both are
extracted simultaneously from the same body.
"""

import re
import uuid
import os
from typing import Optional
from github import Github
from .schemas import ErrorEvent, make_fingerprint


# ── Regex patterns ─────────────────────────────────────────────────────────────

_TRACEBACK_RE   = re.compile(r"(Traceback \(most recent call last\):.*?)(?=\n\n|\Z)", re.DOTALL)
_EXCEPTION_RE   = re.compile(r"^([A-Za-z][A-Za-z0-9_]*(?:Error|Exception|Warning)):\s*(.+)$", re.MULTILINE)
_FILE_LINE_RE   = re.compile(r'File "([^"]+)", line (\d+), in (\S+)')
_CODE_FENCE_RE  = re.compile(r"```(?:\w+)?\n(.*?)```", re.DOTALL)
_SHA_RE         = re.compile(r"\b([0-9a-f]{7,40})\b")

_INCIDENT_PATTERNS = {
    "incident_id":         re.compile(r"(?:Incident\s*ID|ID)\s*[:\-]\s*(.+)", re.I),
    "priority":            re.compile(r"Priority\s*[:\-]\s*(.+)", re.I),
    "configuration_item":  re.compile(r"(?:Configuration\s*Item|CI)\s*[:\-]\s*(.+)", re.I),
    "incident_status":     re.compile(r"Status\s*[:\-]\s*(.+)", re.I),
    "resolution":          re.compile(r"Resolution\s*[:\-]\s*(.+)", re.I),
}


# ── Extraction helpers ─────────────────────────────────────────────────────────

def _extract_traceback(text: str) -> str:
    for fence in _CODE_FENCE_RE.finditer(text):
        m = _TRACEBACK_RE.search(fence.group(1))
        if m:
            return m.group(1).strip()
    m = _TRACEBACK_RE.search(text)
    return m.group(1).strip() if m else ""


def _extract_error_type_message(tb: str, title: str) -> tuple[str, str]:
    if tb:
        matches = list(_EXCEPTION_RE.finditer(tb))
        if matches:
            last = matches[-1]
            return last.group(1), last.group(2).strip()
    m = _EXCEPTION_RE.match(title.strip())
    if m:
        return m.group(1), m.group(2).strip()
    return "Error", title.strip()


def _extract_file_info(tb: str) -> tuple[str, int, str]:
    if not tb:
        return "", 0, ""
    matches = list(_FILE_LINE_RE.finditer(tb))
    if not matches:
        return "", 0, ""
    last = matches[-1]
    return last.group(1), int(last.group(2)), last.group(3)


def _is_incident_style(body: str) -> bool:
    markers = ["incident id", "configuration item", "priority:", "resolution:", "closure code", "open date"]
    body_lower = body.lower()
    return sum(1 for m in markers if m in body_lower) >= 2


def _extract_incident_fields(text: str) -> dict:
    result = {"description": text[:1000].strip()}
    for field, pattern in _INCIDENT_PATTERNS.items():
        m = pattern.search(text)
        if m:
            value = m.group(1).strip().strip("*").strip()
            if value:
                result[field] = value
    return result


def _fetch_comments(repo_full_name: str, issue_number: int) -> list[str]:
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        return []
    try:
        g = Github(token)
        repo = g.get_repo(repo_full_name)
        issue = repo.get_issue(number=issue_number)
        return [c.body for c in issue.get_comments()]
    except Exception as e:
        print(f"[normaliser] Could not fetch comments: {e}")
        return []


# ── Main normaliser ────────────────────────────────────────────────────────────

def normalise_github_issue(payload: dict) -> Optional[ErrorEvent]:
    """
    Convert GitHub Issues webhook payload → ErrorEvent.
    Returns None if the event should be ignored.
    """
    action     = payload.get("action", "")
    issue      = payload.get("issue", {})
    repository = payload.get("repository", {})

    if action not in ("opened", "labeled", "reopened"):
        return None

    repo_url       = repository.get("clone_url") or repository.get("html_url", "")
    repo_full_name = repository.get("full_name", "")
    if not repo_url or not repo_full_name:
        return None

    labels     = [lbl.get("name", "").lower() for lbl in issue.get("labels", [])]
    bug_labels = {"bug", "error", "fix", "critical", "regression", "crash", "incident"}
    if not any(lbl in bug_labels for lbl in labels):
        return None

    issue_number = issue.get("number", 0)
    issue_title  = issue.get("title", "Unknown error")
    issue_body   = issue.get("body") or ""
    issue_url    = issue.get("html_url", "")

    comments  = _fetch_comments(repo_full_name, issue_number)
    full_text = issue_body + "\n\n" + "\n\n".join(comments)

    # Extract technical fields
    tb                             = _extract_traceback(full_text)
    error_type, message            = _extract_error_type_message(tb, issue_title)
    file_path, line_number, fn     = _extract_file_info(tb)
    sha_m                          = _SHA_RE.search(issue_body)
    commit_sha                     = sha_m.group(1) if sha_m else ""

    # Extract incident fields if present
    incident_data = {}
    if _is_incident_style(full_text):
        incident_data = _extract_incident_fields(full_text)
        if not tb:
            error_type = "Incident"
            message    = incident_data.get("description", issue_title)[:300]

    return ErrorEvent(
        id                    = str(uuid.uuid4()),
        fingerprint           = make_fingerprint(error_type, message),
        error_type            = error_type,
        message               = message,
        traceback             = tb,
        file_path             = file_path,
        function_name         = fn,
        line_number           = line_number,
        incident_id           = incident_data.get("incident_id"),
        description           = incident_data.get("description"),
        priority              = incident_data.get("priority"),
        configuration_item    = incident_data.get("configuration_item"),
        incident_status       = incident_data.get("incident_status"),
        resolution            = incident_data.get("resolution"),
        repo_url              = repo_url,
        repo_full_name        = repo_full_name,
        commit_sha            = commit_sha,
        branch                = repository.get("default_branch", "main"),
        environment           = "production",
        source                = "github_issue",
        github_issue_number   = issue_number,
        github_issue_url      = issue_url,
        github_issue_body     = issue_body,
        github_issue_labels   = labels,
        github_issue_comments = comments,
        status                = "pending",
    )