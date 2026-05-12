"""
test_intake.py — tests for the updated intake layer.
Covers both technical (traceback) and incident (ServiceNow) styles.

Run:
    python test_intake.py
"""

import sys, os, uuid
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from intake.schemas import ErrorEvent, make_fingerprint
from intake.normaliser import (
    _extract_traceback, _extract_error_type_message,
    _extract_file_info, _is_incident_style,
    _extract_incident_fields, normalise_github_issue,
)
from intake.queue import EventQueue


# ── Payloads ───────────────────────────────────────────────────────────────────

TECH_PAYLOAD = {
    "action": "labeled",
    "issue": {
        "number": 42,
        "title": "KeyError when user has no role field",
        "html_url": "https://github.com/testuser/testrepo/issues/42",
        "labels": [{"name": "bug"}],
        "body": """App crashes on login for new users.

```
Traceback (most recent call last):
  File "app/auth/middleware.py", line 34, in check_permissions
    role = user_profile["role"]
KeyError: 'role'
```

Commit: a1b2c3d
""",
    },
    "label": {"name": "bug"},
    "repository": {
        "full_name": "testuser/testrepo",
        "clone_url": "https://github.com/testuser/testrepo.git",
        "html_url":  "https://github.com/testuser/testrepo",
        "default_branch": "main",
    },
}

INCIDENT_PAYLOAD = {
    "action": "labeled",
    "issue": {
        "number": 43,
        "title": "Property Submission Prefill Data Mismatch",
        "html_url": "https://github.com/testuser/testrepo/issues/43",
        "labels": [{"name": "incident"}],
        "body": """Incident ID: 121472
Priority: High
Configuration Item: PolicyCenter
Status: Closed
Resolution: Vendor issue — NJM sent incorrect data during 11/15/2024–12/17/2024

Property Submission Prefill Data Mismatch — values pulled in are not
for the correct property. Affects Homeowner/Dwelling submissions in CA.
""",
    },
    "label": {"name": "incident"},
    "repository": {
        "full_name": "testuser/testrepo",
        "clone_url": "https://github.com/testuser/testrepo.git",
        "html_url":  "https://github.com/testuser/testrepo",
        "default_branch": "main",
    },
}

INCIDENT_WITH_TRACE_PAYLOAD = {
    "action": "labeled",
    "issue": {
        "number": 44,
        "title": "PolicyCenter crash during prefill",
        "html_url": "https://github.com/testuser/testrepo/issues/44",
        "labels": [{"name": "incident"}],
        "body": """Incident ID: 121473
Priority: High
Configuration Item: PolicyCenter
Status: Open
Resolution: Under investigation

PolicyCenter crashes during property prefill in CA.

Stack trace from logs:
```
Traceback (most recent call last):
  File "app/prefill/handler.py", line 89, in fetch_property_data
    result = vendor_api.get(property_id)
AttributeError: 'NoneType' object has no attribute 'get'
```
""",
    },
    "label": {"name": "incident"},
    "repository": {
        "full_name": "testuser/testrepo",
        "clone_url": "https://github.com/testuser/testrepo.git",
        "default_branch": "main",
    },
}


# ── Tests ──────────────────────────────────────────────────────────────────────

def test_technical_issue():
    print("\n── Test 1: Technical issue (traceback) ─────────────────────")
    event = normalise_github_issue(TECH_PAYLOAD)

    assert event is not None
    assert event.error_type    == "KeyError"
    assert "'role'"             in event.message
    assert event.file_path     == "app/auth/middleware.py"
    assert event.line_number   == 34
    assert event.function_name == "check_permissions"
    assert event.traceback     != ""
    assert event.commit_sha    == "a1b2c3d"
    # Incident fields should be empty
    assert event.incident_id   is None
    assert event.priority      is None

    print(f"  error_type     : {event.error_type}")
    print(f"  message        : {event.message}")
    print(f"  file_path      : {event.file_path}")
    print(f"  line_number    : {event.line_number}")
    print(f"  function_name  : {event.function_name}")
    print(f"  commit_sha     : {event.commit_sha}")
    print(f"  incident_id    : {event.incident_id} (None — correct)")
    print("  PASSED")


def test_incident_style():
    print("\n── Test 2: Incident-style issue (ServiceNow) ───────────────")
    event = normalise_github_issue(INCIDENT_PAYLOAD)

    assert event is not None
    assert event.error_type         == "Incident"
    assert event.incident_id        == "121472"
    assert event.priority           == "High"
    assert event.configuration_item == "PolicyCenter"
    assert event.incident_status    == "Closed"
    assert event.resolution         is not None
    assert "NJM" in event.resolution
    # No traceback — technical fields empty
    assert event.file_path    == ""
    assert event.line_number  == 0
    assert event.traceback    == ""

    print(f"  error_type          : {event.error_type}")
    print(f"  incident_id         : {event.incident_id}")
    print(f"  priority            : {event.priority}")
    print(f"  configuration_item  : {event.configuration_item}")
    print(f"  incident_status     : {event.incident_status}")
    print(f"  resolution          : {event.resolution[:60]}...")
    print(f"  file_path           : '{event.file_path}' (empty — correct)")
    print("  PASSED")


def test_incident_with_traceback():
    print("\n── Test 3: Incident + traceback (both styles present) ──────")
    event = normalise_github_issue(INCIDENT_WITH_TRACE_PAYLOAD)

    assert event is not None
    # Has both incident AND technical fields
    assert event.incident_id        == "121473"
    assert event.configuration_item == "PolicyCenter"
    assert event.priority           == "High"
    assert event.error_type         == "AttributeError"
    assert event.file_path          == "app/prefill/handler.py"
    assert event.line_number        == 89
    assert event.function_name      == "fetch_property_data"
    assert event.traceback          != ""

    print(f"  incident_id    : {event.incident_id}")
    print(f"  error_type     : {event.error_type}")
    print(f"  file_path      : {event.file_path}")
    print(f"  line_number    : {event.line_number}")
    print(f"  function_name  : {event.function_name}")
    print("  PASSED")


def test_fingerprint():
    print("\n── Test 4: Fingerprint deduplication ───────────────────────")
    fp1 = make_fingerprint("KeyError", "'role'")
    fp2 = make_fingerprint("KeyError", "'role'")
    fp3 = make_fingerprint("KeyError", "'name'")

    assert fp1 == fp2, "Same error = same fingerprint"
    assert fp1 != fp3, "Different error = different fingerprint"
    assert len(fp1) == 16

    print(f"  Same error     : {fp1} == {fp2} ✓")
    print(f"  Diff error     : {fp1} != {fp3} ✓")
    print("  PASSED")


def test_queue_dedup():
    print("\n── Test 5: Queue deduplication ─────────────────────────────")
    q = EventQueue(queue_file="data/test_q.json")
    q.clear()

    event = normalise_github_issue(TECH_PAYLOAD)
    assert event is not None

    first  = q.push(event)
    second = q.push(event)  # same fingerprint

    assert first  is True
    assert second is False
    assert len(q.pending()) == 1

    print(f"  First push     : {first}  (queued)")
    print(f"  Second push    : {second} (deduplicated)")
    print(f"  Queue length   : {len(q.pending())}")
    print("  PASSED")
    q.clear()


def test_ignored_payloads():
    print("\n── Test 6: Ignored payloads ────────────────────────────────")

    # Wrong action
    assert normalise_github_issue({**TECH_PAYLOAD, "action": "closed"}) is None
    print("  Wrong action   : ignored ✓")

    # No bug label
    no_label = {**TECH_PAYLOAD,
                "issue": {**TECH_PAYLOAD["issue"], "labels": [{"name": "enhancement"}]}}
    assert normalise_github_issue(no_label) is None
    print("  No bug label   : ignored ✓")

    # Missing repo
    assert normalise_github_issue({**TECH_PAYLOAD, "repository": {}}) is None
    print("  Missing repo   : ignored ✓")

    print("  PASSED")


# ── Run ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    os.makedirs("data", exist_ok=True)
    print("Testing intake layer (v2)...\n")
    passed = failed = 0

    for test in [
        test_technical_issue,
        test_incident_style,
        test_incident_with_traceback,
        test_fingerprint,
        test_queue_dedup,
        test_ignored_payloads,
    ]:
        try:
            test()
            passed += 1
        except AssertionError as e:
            print(f"\n  FAILED: {e}\n")
            failed += 1

    print(f"\n── {passed} passed, {failed} failed ──────────────────────────────\n")
    if os.path.exists("data/test_q.json"):
        os.remove("data/test_q.json")
    sys.exit(1 if failed else 0)