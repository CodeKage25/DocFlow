# DocFlow - AI Document Processing Platform

Production-grade AI-powered document extraction platform for high-volume financial documents with human-in-the-loop review.

🌐 **Live Demo**: [https://docflow.fly.dev](https://docflow.fly.dev)

## Features

- **🤖 AI-Powered Extraction** - Mistral AI for text documents + Pixtral vision model for images
- **📄 Multi-Format Support** - PDFs, JPG, PNG, TIFF images
- **👀 Human-in-the-Loop** - Priority-based review queue with SLA tracking
- **🔒 Field Locking** - Preserve manual corrections across re-extractions
- **📊 Real-time Metrics** - WebSocket updates, SLA monitoring, alerting
- **☁️ Cloud Storage** - Supabase for persistent file storage
- **✅ 84+ Automated Tests** - Unit, integration, E2E coverage

## Quick Start

```bash
# Clone and install
cd DocFlow
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Configure
cp .env.example .env
# Edit .env with your API keys

# Run backend
uvicorn src.main:app --reload --port 8000

# Run frontend (new terminal)
cd ui && npm install && npm run dev
```

Open http://localhost:5173

## Project Structure

```
DocFlow/
├── src/
│   ├── main.py              # FastAPI entry point, routes, WebSocket
│   ├── extraction_module.py # LLM extraction, PDF parsing, vision
│   ├── workflow_executor.py # DAG-based workflow engine
│   ├── review_queue.py      # Priority queue, claim management, SLA
│   ├── database.py          # PostgreSQL, repositories
│   ├── storage.py           # Supabase file storage
│   └── monitoring.py        # Metrics, alerts
├── ui/                       # React TypeScript dashboard
├── tests/                    # 84+ tests
└── docs/                     # Design documentation
```

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     FRONTEND (React SPA)                        │
│   Documents │ Review Queue │ Metrics │ Document Preview         │
└─────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│                     BACKEND (FastAPI)                            │
│   main.py → extraction_module.py → review_queue.py → database.py│
└─────────────────────────────────────────────────────────────────┘
        │                                          │
        ▼                                          ▼
   ┌─────────────┐                         ┌──────────────┐
   │ Mistral AI  │                         │   Supabase   │
   │ Text+Vision │                         │ PostgreSQL+S3│
   └─────────────┘                         └──────────────┘
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/documents/upload` | POST | Upload PDF or image |
| `/api/v1/documents` | GET | List all documents |
| `/api/v1/review/queue` | GET | Get review queue |
| `/api/v1/review/queue/stats` | GET | Queue statistics |
| `/api/v1/review/items/{id}/claim` | POST | Claim for review |
| `/api/v1/review/items/{id}/submit` | POST | Submit review |
| `/api/v1/metrics` | GET | System metrics |
| `/ws/extraction/{id}` | WebSocket | Real-time updates |

## AI Models

| File Type | Model | Purpose |
|-----------|-------|---------|
| PDF | `mistral-large-latest` | Extract text, then LLM extraction |
| Images (JPG/PNG/TIFF) | `pixtral-large-latest` | Vision model for direct image extraction |

Model selection is automatic based on file content type.

## Testing

```bash
# Activate venv
source venv/bin/activate

# Run all tests
pytest tests/ -v

# Run with coverage
pytest tests/ --cov=src --cov-report=html

# E2E tests (requires running servers)
pytest tests/ui/test_e2e_playwright.py -v --headed
```

## Deployment

### Fly.io (Recommended)

```bash
fly auth login
fly secrets set MISTRAL_API_KEY=xxx DATABASE_URL=xxx SUPABASE_URL=xxx SUPABASE_KEY=xxx
fly deploy -a docflow --strategy immediate
```

### Docker

```bash
docker build -t docflow .
docker run -d -p 8000:8000 \
  -e MISTRAL_API_KEY=key \
  -e DATABASE_URL=postgres://... \
  docflow
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `MISTRAL_API_KEY` | Yes | Mistral AI API key |
| `DATABASE_URL` | Yes | PostgreSQL connection string |
| `SUPABASE_URL` | Yes | Supabase project URL |
| `SUPABASE_KEY` | Yes | Supabase service key |
| `CONFIDENCE_THRESHOLD` | No | Review threshold (default: 0.85) |
| `CLAIM_TIMEOUT_MINUTES` | No | Claim expiration (default: 120) |

## License

MIT
