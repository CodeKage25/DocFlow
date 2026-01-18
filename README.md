# DocFlow - Document Processing Platform

AI-powered document extraction platform for high-volume financial documents with human review capabilities.

## Features

- LLM-powered field extraction (Mistral AI)
- Idempotent processing
- Field preservation for manual corrections
- Dual output: Parquet + JSON
- DAG-based workflow orchestration
- Priority-based review queue with SLA tracking
- Real-time metrics and alerting

## Quick Start

```bash
# Clone and install
cd DocFlow
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Configure
cp .env.example .env
# Edit .env with your MISTRAL_API_KEY

# Run backend
uvicorn src.main:app --reload --port 8000

# Run frontend (new terminal)
cd ui && npm install && npm run dev
```

## Project Structure

```
DocFlow/
├── docs/                    # Design documentation
├── configs/                 # Configuration schemas
├── src/
│   ├── main.py              # API server
│   ├── extraction_module.py # Extraction logic
│   ├── workflow_executor.py # Workflow engine
│   ├── review_queue.py      # Review system
│   ├── monitoring.py        # Metrics
│   └── database.py          # PostgreSQL
├── ui/                      # React dashboard
├── tests/                   # Test suites
└── sample_data/             # Sample invoices
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/documents/upload` | POST | Upload document |
| `/api/v1/documents` | GET | List documents |
| `/api/v1/review/queue` | GET | Review queue |
| `/api/v1/review/items/{id}/submit` | POST | Submit review |
| `/api/v1/metrics` | GET | System metrics |
| `/ws/extraction/{id}` | WebSocket | Real-time updates |

## Testing

```bash
pytest tests/ -v
pytest tests/ --cov=src --cov-report=html
```

## Deployment

### Fly.io

```bash
fly auth login
fly launch --name docflow
fly secrets set MISTRAL_API_KEY=your_key DATABASE_URL=your_db_url
fly deploy
```

### Docker

```bash
docker build -t docflow .
docker run -d -p 8000:8000 -e MISTRAL_API_KEY=key docflow
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `MISTRAL_API_KEY` | Yes | Mistral AI API key |
| `DATABASE_URL` | Yes | PostgreSQL connection |
| `CONFIDENCE_THRESHOLD` | No | Review threshold (default: 0.85) |

## License

MIT
