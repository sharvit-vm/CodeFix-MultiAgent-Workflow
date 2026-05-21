# Project Setup Guide

This guide explains how to set up and run the CodeFix Multi-Agent Workflow on a new machine. It covers local Python setup, environment variables, Neo4j, Pinecone, OpenAI, and the GitHub issue webhook flow.

## What This Project Does

The project builds a searchable knowledge base for source code and uses it to investigate GitHub issues.

Main parts:

- Scans a target repository and detects supported source files.
- Extracts code structure with tree-sitter parsers.
- Uses an LLM to summarize files and folders.
- Stores graph relationships in Neo4j.
- Stores embedded source chunks in Pinecone.
- Runs a FastAPI webhook server that listens for GitHub issue events.
- When a matching GitHub issue is opened, syncs the repository and runs RCA/code-fix agents.
- Can open pull requests with generated fixes.

## Prerequisites

Install these before starting:

- Python 3.10 or newer.
- Git.
- A Neo4j database, either Neo4j Aura or a local Neo4j instance.
- A Pinecone account and index.
- An OpenAI API key.
- A GitHub personal access token if you want the agent to clone private repos, read issue comments, push branches, or open pull requests.

Recommended:

- Python 3.11.
- A dedicated virtual environment for this project.
- Render for a permanent public webhook URL, or `ngrok`/LocalTunnel only for local testing.

## 1. Get The Code

Clone the repository or unzip the project folder, then open a terminal inside the project root.

The project root is the folder that contains:

```text
requirements.txt
render.yaml
server.py
config.py
models.py
agents/
issuelayer/
parsers/
phases/
tools/
```

Example:

```bash
cd CodeFix-MultiAgent-Workflow
```

## 2. Create A Virtual Environment

### Windows PowerShell

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
```

If PowerShell blocks activation, run:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

Then activate again:

```powershell
.\.venv\Scripts\Activate.ps1
```

### macOS Or Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
```

## 3. Install Dependencies

Install all Python dependencies from `requirements.txt`:

```bash
pip install -r requirements.txt
```

If tree-sitter packages fail to install, upgrade build tools and try again:

```bash
python -m pip install --upgrade pip setuptools wheel
pip install -r requirements.txt
```

## 4. Create Your `.env` File

Copy `.env.example` to `.env`:

### Windows PowerShell

```powershell
Copy-Item .env.example .env
```

### macOS Or Linux

```bash
cp .env.example .env
```

Then edit `.env` with your real values.

Example `.env`:

```env
NEO4J_URI=neo4j+s://your-instance.databases.neo4j.io
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=your-neo4j-password

PINECONE_API_KEY=your-pinecone-api-key
PINECONE_INDEX_NAME=your-pinecone-index-name

OPENAI_API_KEY=your-openai-api-key

GITHUB_TOKEN=your-github-token
GITHUB_WEBHOOK_SECRET=your-random-webhook-secret

EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIMENSIONS=1536
MAX_CHUNK_TOKENS=800
MAX_HIERARCHY_LEVELS=8
CACHE_DIR=cache
CLONE_ROOT=clone
AUTO_INGEST_ON_WEBHOOK=true
AUTO_VECTOR_INGEST_ON_WEBHOOK=false
```

### Required Variables

These are required for the full pipeline:

- `NEO4J_URI`: Neo4j connection URI.
- `NEO4J_USERNAME`: Usually `neo4j`.
- `NEO4J_PASSWORD`: Neo4j password.
- `PINECONE_API_KEY`: Pinecone API key.
- `PINECONE_INDEX_NAME`: Pinecone index name.
- `OPENAI_API_KEY`: OpenAI API key.

These are required for GitHub webhook and PR automation:

- `GITHUB_TOKEN`: GitHub token with access to the repositories the agent will clone and update.
- `GITHUB_WEBHOOK_SECRET`: Secret used to verify GitHub webhook signatures.

Optional variables:

- `CACHE_DIR`: Local directory for scan and analysis cache. Default is `cache`.
- `CLONE_ROOT`: Local directory where webhook-triggered repos are cloned. Default is `clone`.
- `EMBEDDING_MODEL`: Default is `text-embedding-3-small`.
- `EMBEDDING_DIMENSIONS`: Default is `1536`.
- `MAX_CHUNK_TOKENS`: Default is `800`.
- `MAX_HIERARCHY_LEVELS`: Default is `8`.
- `ANTHROPIC_API_KEY`: Present in config for future/provider compatibility, but the current code path uses OpenAI.
- `AUTO_INGEST_ON_WEBHOOK`: Runs scanner, file analysis, LLM summaries, hierarchy, and Neo4j ingestion before RCA. Default is `true`.
- `AUTO_VECTOR_INGEST_ON_WEBHOOK`: Also uploads source chunks to Pinecone during webhook processing. Default is `false` because RCA currently uses Neo4j and direct file reads.

## 5. Prepare Neo4j

You can use Neo4j Aura or a local Neo4j server.

For Neo4j Aura:

1. Create an Aura database.
2. Copy the connection URI.
3. Save the username and password.
4. Put them in `.env`.

For local Neo4j:

1. Start Neo4j.
2. Confirm the browser works at `http://localhost:7474`.
3. Use a URI like:

```env
NEO4J_URI=bolt://localhost:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=your-local-password
```

You can verify the connection with:

```bash
python config.py
```

If it succeeds, you should see messages confirming Neo4j and Pinecone connectivity.

## 6. Prepare Pinecone

Create a Pinecone index that matches your embedding model.

For the default model:

```env
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIMENSIONS=1536
```

Your Pinecone index dimension should be `1536`.

Put the index name in `.env`:

```env
PINECONE_INDEX_NAME=your-index-name
```

## 7. Run A Quick Smoke Check

From the project root, run:

```bash
python -m phases.scanner .
```

This checks that Python imports work and the scanner can inspect the repository.

Expected result:

- It prints discovered files.
- It creates cache files under `CACHE_DIR`.
- It should not require Neo4j, Pinecone, or OpenAI for this scanner-only step.

## 8. Deploy On Render

Render is the recommended deployment target for the webhook server because it gives the app a permanent public HTTPS URL. That means you do not need ngrok or LocalTunnel for normal use.

This repository includes `render.yaml`, so Render can read the build and start commands automatically.

Render service settings:

```text
Build Command:
pip install -r requirements.txt

Start Command:
uvicorn server:app --host 0.0.0.0 --port $PORT

Health Check Path:
/health
```

The `$PORT` part is important. Render assigns the port at runtime, so do not use `python server.py` as the Render start command.

Deploy steps:

1. Push this repository to GitHub.
2. Open Render and create a new Web Service.
3. Connect the GitHub repository.
4. Let Render use the `render.yaml` configuration.
5. Add the secret environment variables when Render asks for them.
6. Deploy the service.

Set these secret values in Render:

```env
NEO4J_URI=...
NEO4J_PASSWORD=...
PINECONE_API_KEY=...
PINECONE_INDEX_NAME=...
OPENAI_API_KEY=...
GITHUB_TOKEN=...
GITHUB_WEBHOOK_SECRET=...
```

The Render config also pins:

```env
PYTHON_VERSION=3.11.11
```

`AUTO_INGEST_ON_WEBHOOK` is enabled in `render.yaml`, so a fresh Render service can build the Neo4j graph for the target repository before RCA. `AUTO_VECTOR_INGEST_ON_WEBHOOK` is disabled by default to keep webhook runs faster.

After deployment, Render gives you a public URL like:

```text
https://codefix-multiagent-workflow.onrender.com
```

Check the deployed server:

```text
https://codefix-multiagent-workflow.onrender.com/health
https://codefix-multiagent-workflow.onrender.com/queue
```

Your GitHub webhook Payload URL will be:

```text
https://codefix-multiagent-workflow.onrender.com/webhook/github
```

Important Render notes:

- Free web services can sleep after inactivity, so the first webhook after idle time may be slower.
- Render's filesystem is ephemeral, so `cache/` and `clone/` can disappear after restarts or redeploys.
- This is okay for normal operation because the app can clone repositories again when a webhook arrives.
- For production, use a paid Render instance if you need the webhook worker to stay warm.

## 9. Run Locally For Testing

The normal project workflow starts from a GitHub issue. You do not need to tell users to run the ingestion phases manually during regular use.

For local testing, start the FastAPI server:

```bash
python server.py
```

Or run it directly with Uvicorn:

```bash
uvicorn server:app --host 0.0.0.0 --port 8000 --reload
```

Open these endpoints to confirm the server is running:

```text
http://localhost:8000/health
http://localhost:8000/queue
```

Expected health response:

```json
{
  "status": "ok",
  "queue_stats": {}
}
```

When a valid GitHub issue webhook arrives, the server:

1. Verifies the webhook signature if `GITHUB_WEBHOOK_SECRET` is set.
2. Normalizes the GitHub issue into an `ErrorEvent`.
3. Queues the event.
4. Clones or updates the target repository under `CLONE_ROOT`.
5. Computes a stable `knowledge_id` for the repository.
6. Runs the RCA agent.
7. Runs the code-fix agent.
8. Opens a pull request when the fix succeeds.

## 10. Connect GitHub Webhooks

If the server is deployed on Render, use the Render URL:

```text
https://your-render-service.onrender.com/webhook/github
```

If the server runs locally, expose it with a tunnel.

Example with ngrok:

```bash
ngrok http 8000
```

Copy the public HTTPS URL and create a GitHub webhook:

- Payload URL: `https://your-tunnel-url/webhook/github`
- Content type: `application/json`
- Secret: same value as `GITHUB_WEBHOOK_SECRET`
- Events: Issues

The server currently processes GitHub issue events when:

- The event type is `issues`.
- The action is `opened`.
- The issue has at least one matching label:
  - `bug`
  - `error`
  - `fix`
  - `critical`
  - `regression`
  - `crash`
  - `incident`

The issue body should include either:

- A Python-style traceback, or
- Incident-style fields such as `Incident ID`, `Priority`, `Configuration Item`, `Status`, and `Resolution`.

## 11. Test With A GitHub Issue

Create a new issue in the target repository with one of the supported labels, such as `bug`.

Example issue body:

```text
Traceback (most recent call last):
  File "app/main.py", line 42, in run_job
    result = service.process(payload)
  File "app/service.py", line 18, in process
    return payload["customer_id"]
KeyError: 'customer_id'
```

After the issue is opened:

1. GitHub sends the webhook to `/webhook/github`.
2. The server returns a queued response.
3. The background worker syncs the repository.
4. RCA and code-fix agents run.
5. Check progress at:

```text
http://localhost:8000/queue
```

## 12. Manual Ingestion Commands

These commands are optional. Use them when you want to debug the pipeline, preload or backfill a repository, inspect Neo4j/Pinecone data, or run a phase without opening a GitHub issue.

Full manual pipeline:

```bash
python -m phases.vector_ingest path/to/target/repo
```

This runs these phases in order:

1. `scan_repo`
2. `analyze_files`
3. `analyze_with_llm`
4. `build_hierarchy`
5. `neo4j_ingest`
6. `vector_ingest`

At the end, the command prints a `Knowledge ID`. Keep this value if you want to query or debug data for that repository.

Individual phase commands:

Scanner only:

```bash
python -m phases.scanner path/to/target/repo
```

Tree-sitter file analysis:

```bash
python -m phases.file_analysis path/to/target/repo
```

LLM summaries:

```bash
python -m phases.llm_analysis path/to/target/repo
```

Neo4j ingestion:

```bash
python -m phases.neo4j_ingest path/to/target/repo
```

Vector ingestion:

```bash
python -m phases.vector_ingest path/to/target/repo
```

## 13. GitHub Token Permissions

For public repos, a minimal token may be enough for reading issue comments.

For private repos or automatic PR creation, the token must be able to:

- Read repository contents.
- Clone the repository.
- Push branches.
- Open pull requests.
- Read issue comments.

For a classic GitHub personal access token, this usually means `repo` scope for private repositories.

For a fine-grained token, grant access to the target repository and allow contents, pull requests, and issues permissions as needed.

## 14. Supported Languages

The scanner recognizes many file types, including:

- Python
- JavaScript
- TypeScript
- Go
- Java
- Rust
- C and C++
- Ruby
- PHP
- C#
- Kotlin
- Swift
- Scala
- Shell
- YAML
- JSON
- SQL
- HTML
- CSS
- Markdown

The structural parser phase is designed for:

- Python
- JavaScript
- TypeScript
- Go
- Java

Important current note: this repository currently includes `parsers/python_parser.py`. The file-analysis phase references parser modules for TypeScript, JavaScript, Go, and Java, so those parser modules must exist before those languages can be fully parsed. Non-parseable languages are still discovered by the scanner and can be stored as files, but they will not get function/class extraction.

## 15. Cache And Generated Folders

The project writes local runtime data:

- `cache/`: scan results, file-analysis cache, and LLM summaries.
- `clone/`: repositories cloned by the webhook worker.
- `__pycache__/`: Python bytecode cache.

If results look stale, stop the server and remove the relevant cache folder for that repository knowledge ID.

Do not commit `.env`, cloned repositories, or local cache output.

## 16. Common Problems

### `ModuleNotFoundError`

Make sure the virtual environment is active and dependencies are installed:

```bash
pip install -r requirements.txt
```

Also make sure you are running commands from the project root.

### Neo4j connection fails

Check:

- `NEO4J_URI` is correct.
- Username and password are correct.
- Local Neo4j is running, or Aura allows your connection.
- You are using `neo4j+s://...` for Aura or `bolt://localhost:7687` for local Neo4j.

### Pinecone dimension mismatch

The default embedding model is `text-embedding-3-small`, which uses `1536` dimensions.

Make sure the Pinecone index dimension matches:

```env
EMBEDDING_DIMENSIONS=1536
```

### OpenAI errors

Check:

- `OPENAI_API_KEY` is set.
- The key has access to chat and embedding models.
- Billing and rate limits are healthy.

### GitHub webhook returns ignored

Check:

- The webhook event is an issue event.
- The issue action is `opened`.
- The issue has one of the supported labels.
- The issue body includes a traceback or incident-style data.

### Pull request creation fails

Check:

- `GITHUB_TOKEN` is set.
- The token can push to the repository.
- The repository URL is HTTPS.
- The branch is not protected in a way that blocks token pushes.

### Render deploy fails to bind to a port

Make sure the Render start command uses `$PORT`:

```bash
uvicorn server:app --host 0.0.0.0 --port $PORT
```

Do not use `python server.py` on Render.

## 17. Typical Developer Workflow

Use this sequence when setting up a fresh machine:

```bash
git clone <repo-url>
cd CodeFix-MultiAgent-Workflow
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Then fill in `.env`, verify services:

```bash
python config.py
python -m phases.scanner .
```

For production-like use, deploy to Render and use the Render webhook URL:

```text
https://your-render-service.onrender.com/webhook/github
```

For local testing, start the webhook server:

```bash
python server.py
```

Check:

```text
http://localhost:8000/health
```

Connect the GitHub issue webhook and open a labeled issue with a traceback. That is the normal end-to-end workflow.
