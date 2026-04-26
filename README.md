# Multi-Agent CodeFixer Ingestion Pipeline

This repository contains the data ingestion pipeline that powers the KB Search, RCA Agent, and Code Fix Workflow by converting raw code into queryable Neo4j graph nodes and Pinecone vectors.

## Implementation Progress

- [x] **Phase 0: Foundation**
  - Configured database connections and environment logic in `config.py`.
  - Defined rigid, cross-phase Pydantic data schemas in `models.py`.
  - Built language mapping and junk-filtering logic in `parsers/language_detector.py`.
- [x] **Phase 1: Repo Scanner**
  - Built `phases/scanner.py`.
  - Recursively walks repositories, ignores garbage directories (like `__pycache__`, `venv`, `node_modules`), detects file extensions, and builds base `FileInfo` objects.
- [x] **Phase 2: Tree-Sitter File Analysis**
  - Built `phases/file_analysis.py` along with AST parsers (e.g., `parsers/python_parser.py`).
  - Safely slices valid files to extract precise line-bound functions, classes, and import dependencies across multiple languages simultaneously.
  - Aggregates and physically caches structural AST data locally for fast retrieval.
- [x] **Phase 3: LLM Summaries**
  - Extracting the AST context and querying Large Language Models to generate exact, token-efficient summaries/purposes of every file's logic without loading heavy source code.
- [x] **Phase 4: Hierarchy Building**
  - Grouping files bottom-up into nested folder nodes (Level 1 to Level 8) and aggregating directory-level context. 
- [ ] **Phase 5: Neo4j Ingestion**
  - Executing Cypher queries to create massive structural relationships (`[:CALLS]`, `[:IMPORTS]`, `[:BELONGS_TO]`) for exact Agent blast-radius detection.
- [ ] **Phase 6: Pinecone Ingestion**
  - Generating semantic AI embeddings of chunks and safely upserting vectorized knowledge scoped safely via `knowledge_id`.
