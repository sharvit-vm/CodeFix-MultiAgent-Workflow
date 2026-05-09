"""
intake/normaliser.py

Converts a raw GitHub Issues webhook payload into a clean ErrorEvent.

A single GitHub Issue body contains both:
  - Incident metadata (ServiceNow-style): ID, priority,
    configuration item, status, resolution
  - Technical log: traceback with file path, line number,
    function name

Both are extracted simultaneously from the same issue body.

Example issue body:
    Incident ID: 121472
    Priority: High
    Configuration Item: PolicyCenter
    Status: Closed
    Resolution: Vendor sent incorrect data during 11/15–12/17

    Stack trace from production logs:
    ```
    Traceback (most recent call last):
      File "app/prefill/handler.py", line 89, in fetch_property_data
        result = vendor_api.get(property_id)
    AttributeError: 'NoneType' object has no attribute 'get'
    ```

If no traceback is present → file_path / line_number / function_name
are left empty. The code fix agent will use incident_id +
description + resolution to locate the relevant code.
"""

import re
import uuid
import os
from typing import Optional
from github import Github
from .schemas import ErrorEvent, make_fingerprint


# ── Regex patterns ─────────────────────────────────────────────────────────────

# Python traceback block
_TRACEBACK_RE = re.compile(
    r"(Traceback \(most recent call last\):.*?)(?=\n\n|\Z)",
    re.DOTALL,
)

# ErrorType: message — last line of a Python traceback
_EXCEPTION_LINE_RE = re.compile(
    r"^([A-Za-z][A-Za-z0-9_]*(?:Error|Exception|Warning)):\s*(.+)$",
    re.MULTILINE,
)

# File "path", line N, in function
_FILE_LINE_RE = re.compile(
    r'File "([^"]+)", line (\d+), in (\S+)'
)

# Code fences ```...```
_CODE_FENCE_RE = re.compile(
    r"```(?:\w+)?\n(.*?)```", re.DOTALL
)

# Incident field patterns — matches "Field: value" or "**Field:** value"
_INCIDENT_FIELDS = {
    "incident_id":         re.compile(r"(?:Incident\s*ID|ID)\s*[:\-]\s*(.+)", re.I),
    "priority":            re.compile(r"Priority\s*[:\-]\s*(.+)", re.I),
    "configuration_item":  re.compile(r"(?:Configuration\s*Item|CI)\s*[:\-]\s*(.+)", re.I),
    "incident_status":     re.compile(r"Status\s*[:\-]\s*(.+)", re.I),
    "resolution":          re.compile(r"Resolution\s*[:\-]\s*(.+)", re.I),
}


# ── Technical extraction ───────────────────────────────────────────────────────

def _extract_traceback(text: str) -> str:
    """Pull the first Python traceback from freeform text."""
    # Check inside code fences first
    for fence in _CODE_FENCE_RE.finditer(text):
        tb = _TRACEBACK_RE.search(fence.group(1))
        if tb:
            return tb.group(1).strip()
    # Then raw text
    tb = _TRACEBACK_RE.search(text)
    return tb.group(1).strip() if tb else ""


def _extract_error_type_and_message(
    traceback: str, title: str
) -> tuple[str, str]:
    """
    Extract error type and message.
    Priority: traceback → issue title → fallback to generic.
    """
    if traceback:
        matches = list(_EXCEPTION_LINE_RE.finditer(traceback))
        if matches:
            last = matches[-1]
            return last.group(1), last.group(2).strip()

    title_match = _EXCEPTION_LINE_RE.match(title.strip())
    if title_match:
        return title_match.group(1), title_match.group(2).strip()

    return "Error", title.strip()


def _extract_file_info(traceback: str) -> tuple[str, int, str]:
    """
    Extract innermost file, line number, function name from traceback.
    The last File/line/in match = where the exception actually occurred.
    """
    if not traceback:
        return "", 0, ""

    matches = list(_FILE_LINE_RE.finditer(traceback))
    if not matches:
        return "", 0, ""

    last = matches[-1]
    return last.group(1), int(last.group(2)), last.group(3)


def _extract_commit_sha(text: str) -> str:
    """Find a git commit SHA (7-40 hex chars) anywhere in text."""
    m = re.search(r"\b([0-9a-f]{7,40})\b", text)
    return m.group(1) if m else ""


# ── Incident-style extraction ──────────────────────────────────────────────────

def _is_incident_style(body: str) -> bool:
    """
    Returns True if the issue body looks like a ServiceNow incident.
    Heuristic: contains known incident field labels.
    """
    incident_markers = [
        "incident id", "configuration item", "priority:",
        "closure code", "resolution:", "mttr", "open date"
    ]
    body_lower = body.lower()
    return sum(1 for m in incident_markers if m in body_lower) >= 2


def _extract_incident_fields(text: str) -> dict:
    """
    Extract ServiceNow-style structured fields from freeform text.
    Returns a dict with only the fields we care about.
    """
    result = {}
    for field, pattern in _INCIDENT_FIELDS.items():
        m = pattern.search(text)
        if m:
            # Clean up the value — strip markdown, asterisks, extra whitespace
            value = m.group(1).strip().strip("*").strip()
            if value:
                result[field] = value

    # description is the full body minus the structured fields
    # — take up to 1000 chars as description
    result["description"] = text[:1000].strip()

    return result


# ── GitHub API ─────────────────────────────────────────────────────────────────

def _fetch_issue_comments(repo_full_name: str, issue_number: int) -> list[str]:
    """
    Fetch all comments on a GitHub issue.
    Returns [] if GITHUB_TOKEN not set or API call fails.
    """
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
    Convert a raw GitHub Issues webhook payload into an ErrorEvent.

    Returns None if the event should be ignored:
      - action is not opened / labeled / reopened
      - no repo info
      - no bug/incident label
    """

    action     = payload.get("action", "")
    issue      = payload.get("issue", {})
    repository = payload.get("repository", {})

    # Only process these actions
    if action not in ("opened", "labeled", "reopened"):
        return None

    # Must have repo
    repo_url       = repository.get("clone_url") or repository.get("html_url", "")
    repo_full_name = repository.get("full_name", "")
    if not repo_url or not repo_full_name:
        return None

    # Must have a relevant label
    labels     = [lbl.get("name", "").lower() for lbl in issue.get("labels", [])]
    bug_labels = {"bug", "error", "fix", "critical", "regression", "crash", "incident"}
    if not any(lbl in bug_labels for lbl in labels):
        return None

    # Core issue metadata
    issue_number = issue.get("number", 0)
    issue_title  = issue.get("title", "Unknown error")
    issue_body   = issue.get("body") or ""
    issue_url    = issue.get("html_url", "")

    # Fetch comments for extra context
    comments   = _fetch_issue_comments(repo_full_name, issue_number)
    full_text  = issue_body + "\n\n" + "\n\n".join(comments)

    # ── Extract traceback and technical fields ──────────────────
    traceback                          = _extract_traceback(full_text)
    error_type, message                = _extract_error_type_and_message(traceback, issue_title)
    file_path, line_number, func_name  = _extract_file_info(traceback)
    commit_sha                         = _extract_commit_sha(issue_body)

    # ── Extract incident fields if incident-style ───────────────
    incident_data = {}
    if _is_incident_style(full_text):
        incident_data = _extract_incident_fields(full_text)
        # If no traceback found, use "Incident" as error_type
        # so the agent knows this is ops-style, not a code exception
        if not traceback:
            error_type = "Incident"
            message    = incident_data.get("description", issue_title)[:300]

    # ── Build ErrorEvent ────────────────────────────────────────
    return ErrorEvent(
        id                    = str(uuid.uuid4()),
        fingerprint           = make_fingerprint(error_type, message),
        error_type            = error_type,
        message               = message,
        traceback             = traceback,
        file_path             = file_path,
        function_name         = func_name,
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