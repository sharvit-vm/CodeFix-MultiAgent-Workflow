"""
Phase 2 — File Analysis
Runs tree-sitter parser on each file, populates FileInfo with
functions/classes/imports, caches result to disk.
"""

import json
from pathlib import Path
from typing import Optional
from tqdm import tqdm

from models import FileInfo, PipelineState
from parsers.language_detector import is_parseable
from config import CACHE_DIR


_parser_cache: dict = {}


def _get_parser(language: str):
    """Returns the right parser instance for a language. Cached as singletons."""
    if language in _parser_cache:
        return _parser_cache[language]

    parser = None

    if language == "python":
        from parsers.python_parser import PythonParser
        parser = PythonParser()

    elif language in ("typescript", "tsx"):
        from parsers.typescript_parser import TypeScriptParser
        parser = TypeScriptParser(language=language)

    elif language == "javascript":
        from parsers.typescript_parser import JavaScriptParser
        parser = JavaScriptParser()

    elif language == "go":
        from parsers.go_parser import GoParser
        parser = GoParser()

    elif language == "java":
        from parsers.java_parser import JavaParser
        parser = JavaParser()

    if parser:
        _parser_cache[language] = parser

    return parser


def _cache_dir(state: PipelineState) -> Path:
    d = Path(CACHE_DIR) / state.knowledge_id / "file_analysis"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cache_key(file_path: str) -> str:
    """Sanitize file path to use as a filename."""
    return file_path.replace("/", "__").replace("\\", "__") + ".json"


def _load_cached(cache_dir: Path, file_path: str) -> Optional[FileInfo]:
    cache_file = cache_dir / _cache_key(file_path)
    if cache_file.exists():
        with open(cache_file) as f:
            return FileInfo(**json.load(f))
    return None


def _save_cache(cache_dir: Path, file_info: FileInfo):
    cache_file = cache_dir / _cache_key(file_info.path)
    with open(cache_file, "w") as f:
        json.dump(file_info.model_dump(), f, indent=2)


def analyze_files(state: PipelineState) -> PipelineState:
    """
    For each FileInfo in state.files:
      - If parseable language: run tree-sitter, extract functions/classes/imports
      - If not parseable: store as-is (FileNode still created in Neo4j, just no symbols)
    Results are cached per file. Re-running skips already-cached files.
    """
    if state.file_analysis_complete:
        print("[FileAnalysis] Already complete, skipping.")
        return state

    cache_dir = _cache_dir(state)
    updated_files = []

    parsed_count   = 0
    skipped_count  = 0
    error_count    = 0
    cache_hit      = 0

    print(f"\n[FileAnalysis] Analyzing {len(state.files)} files...")
    for file_info in tqdm(state.files, desc="Parsing files"):
        cached = _load_cached(cache_dir, file_info.path)
        if cached:
            updated_files.append(cached)
            cache_hit += 1
            continue
        if not is_parseable(file_info.language):
            _save_cache(cache_dir, file_info)
            updated_files.append(file_info)
            skipped_count += 1
            continue
        parser = _get_parser(file_info.language)
        if parser is None:
            file_info.parse_error = f"No parser available for {file_info.language}"
            _save_cache(cache_dir, file_info)
            updated_files.append(file_info)
            error_count += 1
            continue

        try:
            file_info = parser.parse(file_info)
            for func in file_info.functions:
                func.file_path = file_info.path
            for cls in file_info.classes:
                cls.file_path = file_info.path
            if file_info.parse_error:
                error_count += 1
            else:
                parsed_count += 1
        except Exception as e:
            file_info.parse_error = str(e)
            error_count += 1
        _save_cache(cache_dir, file_info)
        updated_files.append(file_info)
    total_functions = sum(len(f.functions) for f in updated_files)
    total_classes   = sum(len(f.classes) for f in updated_files)
    print(f"\n[FileAnalysis] Results:")
    print(f"  Cache hits      : {cache_hit}")
    print(f"  Parsed          : {parsed_count}")
    print(f"  Skipped (no parser): {skipped_count}")
    print(f"  Errors          : {error_count}")
    print(f"  Total functions : {total_functions}")
    print(f"  Total classes   : {total_classes}")

    if error_count > 0:
        print(f"\n  Files with errors:")
        for f in updated_files:
            if f.parse_error:
                print(f"    {f.path}: {f.parse_error}")

    state.files = updated_files
    state.file_analysis_complete = True
    return state


if __name__ == "__main__":
    import sys
    import uuid
    from phases.scanner import scan_repo

    if len(sys.argv) < 2:
        print("Usage: python phases/file_analysis.py <repo_path>")
        sys.exit(1)
    repo_path = sys.argv[1]
    state = PipelineState(repo_path=repo_path, knowledge_id=str(uuid.uuid4())[:8])
    state = scan_repo(state)
    state = analyze_files(state)
    print("\nSample output — first 3 parseable files:")
    count = 0
    for f in state.files:
        if f.functions or f.classes:
            print(f"\n  File: {f.path} ({f.language})")
            print(f"  Functions: {[fn.name for fn in f.functions[:5]]}")
            print(f"  Classes:   {[c.name for c in f.classes]}")
            print(f"  Imports:   {len(f.imports)} imports")
            count += 1
            if count >= 3:
                break
