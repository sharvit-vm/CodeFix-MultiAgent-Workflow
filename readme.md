# POC Code Fixer Agent

A multi-agent system that automatically fixes bugs by listening to GitHub Issues and running a LangGraph pipeline.

## Architecture

```
GitHub Issue (labelled "bug")
        ↓
FastAPI webhook server
        ↓
Normaliser — extracts error type, traceback, file path
        ↓
Queue — deduplicates and persists events
        ↓
LangGraph agents — fetch repo, analyse, fix, PR (coming soon)
```

## Project Structure

```
app/
├── intake/
│   ├── schemas.py          # ErrorEvent model
│   ├── normaliser.py       # GitHub payload → ErrorEvent
│   ├── queue.py            # Persistent event queue
│   └── sources/
│       └── github_webhook.py  # FastAPI webhook route
├── server.py               # FastAPI entry point
├── test_intake.py          # Tests
├── requirements.txt
└── .env.example
```

## Setup

**1. Clone and create virtual environment**
```bash
git clone https://github.com/yourname/yourrepo.git
cd yourrepo
git checkout intake-layer

cd app
python -m venv venv

# Windows
.\venv\Scripts\Activate.ps1

# Mac/Linux
source venv/bin/activate
```

**2. Install dependencies**
```bash
pip install -r requirements.txt
```

**3. Configure environment**
```bash
cp .env.example .env
# Edit .env and fill in your GITHUB_TOKEN and GITHUB_WEBHOOK_SECRET
```

**4. Run tests**
```bash
python test_intake.py
```

**5. Start the server**
```bash
uvicorn server:app --host 0.0.0.0 --port 8765 --reload
```

**6. Expose with ngrok**
```bash
ngrok http 8765
```

**7. Add GitHub webhook**
- Go to your repo → Settings → Webhooks → Add webhook
- Payload URL: `https://your-ngrok-url.ngrok-free.app/intake/github`
- Content type: `application/json`
- Secret: same value as `GITHUB_WEBHOOK_SECRET` in `.env`
- Events: Issues only

## How it works

1. Open a GitHub Issue and label it `bug`
2. GitHub fires a webhook to your server
3. The normaliser extracts structured error info from the issue body
4. The event is stored in the queue with `status: pending`
5. LangGraph agents will pick it up and generate a fix (next milestone)

## Environment Variables

| Variable | Description |
|---|---|
| `GITHUB_TOKEN` | GitHub personal access token (repo + issues scope) |
| `GITHUB_WEBHOOK_SECRET` | Secret to verify webhook signatures |
| `QUEUE_FILE` | Path to the queue JSON file (default: `data/event_queue.json`) |
| `PORT` | Server port (default: 8765) |

## API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| POST | `/intake/github` | GitHub webhook receiver |
| GET | `/queue` | View all queued events |
| GET | `/queue/stats` | Queue counts by status |
| GET | `/health` | Health check |
| DELETE | `/queue/{id}` | Remove an event |
| DELETE | `/queue` | Clear all events |