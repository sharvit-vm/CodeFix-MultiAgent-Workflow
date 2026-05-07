"""
Reads source code files from the cloned repo on disk.

Chunking logic:
- Count tokens with tiktoken
- If file <= 3000 tokens: return the whole file with a header
- If file > 3000 tokens: query Neo4j for function start/end line numbers,
  then slice the actual source file from clone/ by those line numbers —
  each chunk is one complete function, never cut mid-function.
  Falls back to RecursiveCharacterTextSplitter on class/function/blank line
  boundaries if Neo4j has no function data for that file.

Code always comes from the cloned repo on disk.
Neo4j is only used for line number boundaries — never for code content.
Every chunk has a header so the agent always knows which file and lines it is reading.
"""

import os
from langchain_core.tools import tool
from langchain_text_splitters import RecursiveCharacterTextSplitter
import tiktoken
from config import get_neo4j_driver


CLONE_DIR = os.getenv("CLONE_DIR", "clone")
TOKEN_LIMIT = 3000

enc = tiktoken.encoding_for_model("gpt-4o-mini")

fallback_splitter = RecursiveCharacterTextSplitter(
    chunk_size=12000,
    chunk_overlap=800,
    separators=["\nclass ", "\ndef ", "\n\n", "\n"]
)


def count_tokens(text: str) -> int:
    return len(enc.encode(text))

def get_full_path(file_path: str) -> str:
    return os.path.join(CLONE_DIR, file_path)

def get_function_boundaries(file_path: str, knowledge_id: str) -> list[dict]:
    """Fetches function start/end lines from Neo4j for a given file."""
    driver = get_neo4j_driver()
    with driver.session() as session:
        result = session.run("""
            MATCH (fn:FunctionNode {file_path: $path, knowledge_id: $kid})
            RETURN fn.name AS name, fn.start_line AS start, fn.end_line AS end
            ORDER BY fn.start_line
        """, {"path": file_path, "kid": knowledge_id})
        return [dict(r) for r in result]


def build_chunk_header(file_path: str, name: str, chunk_type: str, start: int, end: int) -> str:
    """
    Every chunk gets this header so the LLM always knows:
    - which file this code is from
    - which function/class it is and exactly which lines it covers
    """
    return f"# File: {file_path}\n# {chunk_type}: {name}\n# Lines: {start}-{end}\n"


@tool
def read_file(file_path: str, knowledge_id: str) -> str:
    """
    Reads the actual source code file from the cloned repo on disk.

    Steps:
    1. Opens the file from clone/<file_path>
    2. Counts tokens with tiktoken
    3. If small (<= 3000 tokens): returns the whole file with a header
    4. If large (> 3000 tokens): queries Neo4j for function start/end line numbers,
       then slices the actual source file from clone/ by those line numbers —
       each chunk is one complete function, never cut mid-function.
       Falls back to RecursiveCharacterTextSplitter on class/function/blank line
       boundaries if Neo4j has no function data for the file.

    Code always comes from the cloned repo on disk.
    Neo4j is only used for line number boundaries — never for code content.
    """
    full_path = get_full_path(file_path)
    if not os.path.exists(full_path):
        return f"[Error] File not found: {full_path}"
    with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()
    code = "".join(lines)
    if not code.strip():
        return f"[Empty file]: {file_path}"
    token_count = count_tokens(code)
    if token_count <= TOKEN_LIMIT:
        header = build_chunk_header(file_path, file_path, "File", 1, len(lines))
        return header + "\n" + code
    functions = get_function_boundaries(file_path, knowledge_id)
    if not functions:
        # No Neo4j data — fall back to character splitter with headers
        raw_chunks = fallback_splitter.split_text(code)
        chunks = []
        for i, chunk in enumerate(raw_chunks):
            header = f"# File: {file_path}\n# Chunk: {i + 1} of {len(raw_chunks)}\n"
            chunks.append(header + "\n" + chunk)
        return "\n\n---chunk---\n\n".join(chunks)
    chunks = []
    for fn in functions:
        fn_code = "".join(lines[fn["start"] - 1 : fn["end"]])
        header = build_chunk_header(file_path, fn["name"], "Function", fn["start"], fn["end"])
        chunks.append(header + "\n" + fn_code)
    return "\n\n---chunk---\n\n".join(chunks)
    
@tool
def read_file_range(file_path: str, start_line: int, end_line: int) -> str:
    """
    Reads a specific range of lines from a file in the cloned repo.
    Use this when you know exact line numbers from Neo4j and only
    need that specific function or class, not the whole file.
    """
    full_path = get_full_path(file_path)
    if not os.path.exists(full_path):
        return f"[Error] File not found: {full_path}"
    with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()
    selected = lines[start_line - 1 : end_line]
    header = build_chunk_header(file_path, "range", "Lines", start_line, end_line)
    return header + "\n" + "".join(selected)

@tool
def write_fix(file_path: str, start_line: int, end_line: int, new_code: str) -> str:
    """
    Writes a fix to the cloned repo by replacing lines start_line to end_line
    with new_code. Only the Code Fix Agent should call this.
    Always verify the fix is correct before calling this.
    """
    full_path = get_full_path(file_path)
    if not os.path.exists(full_path):
        return f"[Error] File not found: {full_path}"
    with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()
    new_lines = new_code.splitlines(keepends=True)
    lines[start_line - 1 : end_line] = new_lines
    with open(full_path, "w", encoding="utf-8") as f:
        f.writelines(lines)
    return f"Fix applied to {file_path} lines {start_line}-{end_line}"

@tool
def get_token_count(file_path: str) -> dict:
    """
    Returns token count and line count of a file.
    Use this before reading a large file to understand its size.
    """
    full_path = get_full_path(file_path)
    if not os.path.exists(full_path):
        return {"error": f"File not found: {full_path}"}
    with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
        code = f.read()
    lines = code.splitlines()
    tokens = count_tokens(code)
    return {
        "file_path":   file_path,
        "token_count": tokens,
        "line_count":  len(lines),
        "will_chunk":  tokens > TOKEN_LIMIT,
    }