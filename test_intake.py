"""
test_intake.py

Simulates a real GitHub webhook payload hitting the intake layer.
Run this WITHOUT starting the server — it tests the normaliser
and queue directly.

Usage:
    python test_intake.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from intake.normaliser import normalise_github_issue
from intake.queue import EventQueue

# ── Simulated GitHub Issues webhook payload ────────────────────────────────────
# This is the exact shape GitHub sends for an issues.labeled event

MOCK_PAYLOAD = {
    "action": "labeled",
    "issue": {
        "number": 42,
        "title": "KeyError when user has no role field",
        "html_url": "https://github.com/testuser/testrepo/issues/42",
        "state": "open",
        "labels": [
            {"name": "bug", "color": "d73a4a"},
            {"name": "critical", "color": "e4e669"},
        ],
        "body": """## Bug Report

When a user logs in for the first time (before their profile is fully set up),
the application crashes with a KeyError.

### Steps to reproduce
1. Create a new user account
2. Attempt to log in immediately
3. Server throws a 500 error

### Stack trace

```
Traceback (most recent call last):
  File "app/auth/middleware.py", line 34, in check_permissions
    role = user_profile["role"]
KeyError: 'role'
```

### Expected behaviour
New users should default to the "viewer" role.

### Environment
- Production
- Commit: a1b2c3d
""",
    },
    "label": {"name": "bug"},
    "repository": {
        "full_name": "testuser/testrepo",
        "clone_url": "https://github.com/testuser/testrepo.git",
        "html_url": "https://github.com/testuser/testrepo",
        "default_branch": "main",
    },
    "sender": {"login": "someuser"},
}


def test_normaliser():
    print("\n── Test 1: Normaliser ──────────────────────────────────────")
    event = normalise_github_issue(MOCK_PAYLOAD)

    assert event is not None, "Normaliser returned None — should have produced an event"
    assert event.error_type == "KeyError", f"Expected KeyError, got {event.error_type}"
    assert "'role'" in event.message, f"Expected 'role' in message, got: {event.message}"
    assert "app/auth/middleware.py" in event.file_path, f"Wrong file_path: {event.file_path}"
    assert event.line_number == 34, f"Expected line 34, got {event.line_number}"
    assert event.function_name == "check_permissions", f"Wrong function: {event.function_name}"
    assert event.github_issue_number == 42
    assert event.repo_url == "https://github.com/testuser/testrepo.git"
    assert event.status == "pending"
    assert len(event.fingerprint) == 16

    print(f"  error_type     : {event.error_type}")
    print(f"  message        : {event.message}")
    print(f"  file_path      : {event.file_path}")
    print(f"  line_number    : {event.line_number}")
    print(f"  function_name  : {event.function_name}")
    print(f"  fingerprint    : {event.fingerprint}")
    print(f"  commit_sha     : {event.commit_sha}")
    print(f"  issue_number   : #{event.github_issue_number}")
    print("  PASSED")
    return event


def test_queue(event):
    print("\n── Test 2: Queue push ──────────────────────────────────────")
    q = EventQueue(queue_file="data/test_queue.json")
    q.clear()

    was_queued = q.push(event)
    assert was_queued is True, "First push should succeed"
    assert len(q.pending()) == 1

    # Push again — same fingerprint — should deduplicate
    was_queued_again = q.push(event)
    assert was_queued_again is False, "Second push should be deduplicated"
    assert len(q.pending()) == 1, "Queue should still have only 1 event"

    print(f"  Pushed event   : {event.id}")
    print(f"  Dedup works    : True")
    print(f"  Queue length   : {len(q.pending())}")
    print("  PASSED")
    return q


def test_status_update(event, q):
    print("\n── Test 3: Status update ───────────────────────────────────")
    q.update_status(event.id, "running")
    updated = q.get(event.id)
    assert updated.status == "running"

    # A "running" event should not count as pending
    assert len(q.pending()) == 0

    q.update_status(event.id, "done", extra={"fix_result": "patch applied"})
    done = q.get(event.id)
    assert done.status == "done"

    print(f"  Status flow    : pending → running → done")
    print(f"  Extra fields   : {done.model_dump().get('fix_result')}")
    print("  PASSED")


def test_ignored_payload():
    print("\n── Test 4: Ignored payloads ────────────────────────────────")

    # Closed issue — should be ignored
    closed = {**MOCK_PAYLOAD, "action": "closed"}
    assert normalise_github_issue(closed) is None
    print("  Closed issue   : ignored correctly")

    # No bug label
    no_label = {
        **MOCK_PAYLOAD,
        "issue": {**MOCK_PAYLOAD["issue"], "labels": [{"name": "enhancement"}]},
    }
    assert normalise_github_issue(no_label) is None
    print("  No bug label   : ignored correctly")

    # Missing repository
    no_repo = {**MOCK_PAYLOAD, "repository": {}}
    assert normalise_github_issue(no_repo) is None
    print("  Missing repo   : ignored correctly")

    print("  PASSED")


if __name__ == "__main__":
    print("Testing intake layer...\n")
    try:
        event = test_normaliser()
        q = test_queue(event)
        test_status_update(event, q)
        test_ignored_payload()
        print("\n── All tests passed ────────────────────────────────────────\n")
    except AssertionError as e:
        print(f"\n  FAILED: {e}\n")
        sys.exit(1)
    finally:
        # Clean up test queue
        if os.path.exists("data/test_queue.json"):
            os.remove("data/test_queue.json")