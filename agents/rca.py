"""
RCA Agent — Root Cause Analysis

Receives an ErrorEvent and a knowledge_id, reasons about the bug using
Neo4j and file tools, and returns a structured RCAResult.

knowledge_id is passed separately because it comes from the ingestion
pipeline, not from the GitHub issue (ErrorEvent).
"""

import json
from typing import List
from pydantic import BaseModel
from langchain_core.messages import HumanMessage
from langchain.agents import create_agent

from config import agent_llm
from issuelayer.intake.schemas import ErrorEvent
from tools.neo4j_tool import (
    get_connected_files,
    get_function_calls,
    get_file_summary,
    get_folder_context,
)
from tools.file_tool import (
    read_file,
    read_file_range,
    get_token_count,
)


class RCAResult(BaseModel):
    root_cause: str
    buggy_file: str
    buggy_function: str
    buggy_lines: List[int]
    affected_files: List[str]
    fix_suggestion: str
    confidence: str        # "high" / "medium" / "low"
    reasoning: str


tools = [
    get_connected_files,
    get_function_calls,
    get_file_summary,
    get_folder_context,
    read_file,
    read_file_range,
    get_token_count,
]

SYSTEM_PROMPT = """You are an expert software engineer performing root cause analysis on a bug.

You will be given an error event with a file path, line number, function name, error type, and traceback.

Your job is to find the root cause — not just describe the symptom.

Follow this order:
1. Read the file where the error occurred using read_file
2. Check what functions are connected using get_function_calls
3. Get connected files using get_connected_files
4. Read connected files only if they look relevant to the bug
5. Reason carefully about why the bug happens

Rules:
- Do not jump to conclusions — read the code first, then reason
- If a file looks unrelated to the bug, skip it
- Be specific about which lines are buggy
- Only mark confidence as "high" if you actually read the buggy code and understood it

When you are done reasoning, output a JSON object with exactly these fields:
{
  "root_cause": "clear explanation of why the bug happens",
  "buggy_file": "path/to/file.py",
  "buggy_function": "function_name",
  "buggy_lines": [45, 46, 47],
  "affected_files": ["other/file.py"],
  "fix_suggestion": "what needs to change (idea, not actual code)",
  "confidence": "high",
  "reasoning": "step by step reasoning you followed"
}

Output ONLY the JSON. No extra text before or after it."""


def run_rca(event: ErrorEvent, knowledge_id: str) -> RCAResult:
    """
    Main entry point. Takes an ErrorEvent and knowledge_id, returns RCAResult.

    knowledge_id is the Neo4j graph ID for this repo — it comes from the
    ingestion pipeline, not from the GitHub issue.
    """
    agent = create_agent(
        model=agent_llm,
        tools=tools,
        system_prompt=SYSTEM_PROMPT,
    )

    user_message = f"""
Error Event:
  File        : {event.file_path}
  Line        : {event.line_number}
  Function    : {event.function_name}
  Error type  : {event.error_type}
  Message     : {event.message}
  Knowledge ID: {knowledge_id}

Traceback:
{event.traceback}

Investigate this bug and return your RCAResult JSON.
"""

    try:
        result = agent.invoke({
            "messages": [HumanMessage(content=user_message)]
        })

        last_message = result["messages"][-1].content

        clean = last_message.strip()
        if clean.startswith("```"):
            clean = clean.split("```")[1]
            if clean.startswith("json"):
                clean = clean[4:]
            clean = clean.strip()

        parsed = json.loads(clean)
        return RCAResult(**parsed)

    except Exception as e:
        return RCAResult(
            root_cause=f"RCA agent failed: {str(e)}",
            buggy_file=event.file_path,
            buggy_function=event.function_name,
            buggy_lines=[event.line_number],
            affected_files=[],
            fix_suggestion="Manual investigation required",
            confidence="low",
            reasoning=f"Agent encountered an error: {str(e)}",
        )


if __name__ == "__main__":
    from issuelayer.intake.schemas import make_fingerprint
    import uuid

    test_event = ErrorEvent(
        id=str(uuid.uuid4()),
        fingerprint=make_fingerprint("AttributeError", "'NoneType' object has no attribute 'content'"),
        error_type="AttributeError",
        message="'NoneType' object has no attribute 'content'",
        traceback="Traceback (most recent call last):\n  File 'practise/day10/agents/editor.py', line 12, in edit_content\n    result = response.content\nAttributeError: 'NoneType' object has no attribute 'content'",
        file_path="practise/day10/agents/editor.py",
        function_name="edit_content",
        line_number=12,
        repo_url="https://github.com/sharvit-vm/phase3.git",
        repo_full_name="sharvit-vm/phase3",
    )

    print("Running RCA agent...")
    result = run_rca(test_event, knowledge_id="b95467ce")

    print("\nRCA Result:")
    print(f"  Root cause     : {result.root_cause}")
    print(f"  Buggy file     : {result.buggy_file}")
    print(f"  Buggy function : {result.buggy_function}")
    print(f"  Buggy lines    : {result.buggy_lines}")
    print(f"  Affected files : {result.affected_files}")
    print(f"  Fix suggestion : {result.fix_suggestion}")
    print(f"  Confidence     : {result.confidence}")
    print(f"\nReasoning:\n{result.reasoning}")
