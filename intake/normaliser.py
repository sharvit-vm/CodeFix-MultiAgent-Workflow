"""
intake/normaliser.py

Converts a raw GitHub Issues webhook payload into a clean ErrorEvent.

Responsibilities:
- Extract repo URL and issue metadata from the payload
- Parse the issue body for tracebacks, file paths, and error types
- Fetch issue comments from the GitHub API for extra context
- Deduplicate via fingerprint
- Return a fully populated ErrorEvent ready for the queue
"""

import re
import uuid
import os
from typing import Optional
from github import Github
from .schemas import ErrorEvent, make_fingerprint


# ── Traceback extraction ───────────────────────────────────────────────────────

# Matches Python tracebacks that start with "Traceback (most recent call last):"
_TRACEBACK_RE = re.compile(
    r"(Traceback \(most recent call last\):.*?)(?=\n\n|\Z)",
    re.DOTALL,
)

# Matches the last line of a Python traceback: ErrorType: message
_EXCEPTION_LINE_RE = re.compile(
    r"^([A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)*Error|"
    r"[A-Za-z][A-Za-z0-9_]*Exception|"
    r"[A-Za-z][A-Za-z0-9_]*Warning)"
    r":\s*(.+)$",
    re.MULTILINE,
)

# Matches "File "path/to/file.py", line 42, in function_name"
_FILE_LINE_RE = re.compile(
    r'File "([^"]+)", line (\d+), in (\S+)'
)

# Matches code fences: ```...``` or indented blocks
_CODE_FENCE_RE = re.compile(r"```(?:\w+)?\n(.*?)```", re.DOTALL)


def _extract_traceback(text: str) -> str:
    """Pull the first Python traceback out of freeform text."""
    # First check inside code fences
    for fence_match in _CODE_FENCE_RE.finditer(text):
        tb = _TRACEBACK_RE.search(fence_match.group(1))
        if tb:
            return tb.group(1).strip()

    # Then check the raw text
    tb = _TRACEBACK_RE.search(text)
    return tb.group(1).strip() if tb else ""


def _extract_error_type_and_message(traceback: str, title: str) -> tuple[str, str]:
    """
    Extract error type and message from a traceback.
    Falls back to parsing the issue title if no traceback.
    """
    if traceback:
        # Last ErrorType: message line in the traceback
        matches = list(_EXCEPTION_LINE_RE.finditer(traceback))
        if matches:
            last = matches[-1]
            return last.group(1), last.group(2).strip()

    # Fall back: try to parse "ErrorType: message" from issue title
    title_match = _EXCEPTION_LINE_RE.match(title.strip())
    if title_match:
        return title_match.group(1), title_match.group(2).strip()

    # Last resort: use generic type, full title as message
    return "Error", title.strip()


def _extract_file_info(traceback: str) -> tuple[str, int, str]:
    """
    Extract the innermost (most relevant) file, line number, and
    function name from a traceback.
    Returns (file_path, line_number, function_name).
    """
    if not traceback:
        return "", 0, ""

    matches = list(_FILE_LINE_RE.finditer(traceback))
    if not matches:
        return "", 0, ""

    # The last match is the innermost frame — where the error actually occurred
    last = matches[-1]
    return last.group(1), int(last.group(2)), last.group(3)


def _fetch_issue_comments(repo_full_name: str, issue_number: int) -> list[str]:
    """
    Fetch all comments on a GitHub issue using PyGitHub.
    Returns list of comment body strings.
    Needs GITHUB_TOKEN in environment.
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


def _extract_commit_sha(body: str, comments: list[str]) -> str:
    """
    Try to find a git commit SHA mentioned anywhere in the issue.
    A SHA is a 7-40 char hex string.
    """
    sha_re = re.compile(r"\b([0-9a-f]{7,40})\b")
    for text in [body] + comments:
        m = sha_re.search(text)
        if m:
            return m.group(1)
    return ""


# ── Main normaliser ────────────────────────────────────────────────────────────

def normalise_github_issue(payload: dict) -> Optional[ErrorEvent]:
    """
    Convert a raw GitHub Issues webhook payload into an ErrorEvent.

    Returns None if the payload should be ignored (e.g. not a bug,
    issue closed, missing required fields).
    """

    action = payload.get("action", "")
    issue = payload.get("issue", {})
    repository = payload.get("repository", {})

    # ── Guard: only process opened or labelled events ──────────
    if action not in ("opened", "labeled", "reopened"):
        return None

    # ── Guard: must have repo info ──────────────────────────────
    repo_url = repository.get("clone_url") or repository.get("html_url", "")
    repo_full_name = repository.get("full_name", "")
    if not repo_url or not repo_full_name:
        return None

    # ── Guard: must be labelled "bug" (or similar) ─────────────
    labels = [lbl.get("name", "").lower() for lbl in issue.get("labels", [])]
    bug_labels = {"bug", "error", "fix", "critical", "regression", "crash"}
    if not any(lbl in bug_labels for lbl in labels):
        return None

    # ── Extract core issue info ─────────────────────────────────
    issue_number = issue.get("number", 0)
    issue_title = issue.get("title", "Unknown error")
    issue_body = issue.get("body") or ""
    issue_url = issue.get("html_url", "")

    # ── Fetch comments for extra context ───────────────────────
    comments = _fetch_issue_comments(repo_full_name, issue_number)

    # Combine body + comments into one searchable text blob
    full_text = issue_body + "\n\n" + "\n\n".join(comments)

    # ── Parse error info ────────────────────────────────────────
    traceback = _extract_traceback(full_text)
    error_type, message = _extract_error_type_and_message(traceback, issue_title)
    file_path, line_number, function_name = _extract_file_info(traceback)
    commit_sha = _extract_commit_sha(issue_body, comments)

    # ── Build the event ─────────────────────────────────────────
    event = ErrorEvent(
        id=str(uuid.uuid4()),
        fingerprint=make_fingerprint(error_type, message),
        error_type=error_type,
        message=message,
        traceback=traceback,
        file_path=file_path,
        function_name=function_name,
        line_number=line_number,
        repo_url=repo_url,
        repo_full_name=repo_full_name,
        commit_sha=commit_sha,
        branch=repository.get("default_branch", "main"),
        environment="production",
        source="github_issue",
        github_issue_number=issue_number,
        github_issue_url=issue_url,
        github_issue_body=issue_body,
        github_issue_labels=labels,
        github_issue_comments=comments,
        status="pending",
    )

    return event