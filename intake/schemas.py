import hashlib
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field


class ErrorEvent(BaseModel):

    id: str
    fingerprint: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    error_type: str
    message: str
    traceback: str = ""
    file_path: str = ""
    function_name: str = ""
    line_number: int = 0

    repo_url: str
    repo_full_name: str
    commit_sha: str = ""
    branch: str = "main"

    environment: str = "production"
    frequency: int = 1
    source: str = "github_issue"

    github_issue_number: Optional[int] = None
    github_issue_url: Optional[str] = None
    github_issue_body: Optional[str] = None
    github_issue_labels: List[str] = Field(default_factory=list)
    github_issue_comments: List[str] = Field(default_factory=list)

    status: str = "pending"


def make_fingerprint(error_type: str, message: str) -> str:
    raw = f"{error_type}::{message}".lower().strip()
    return hashlib.sha256(raw.encode()).hexdigest()[:16]