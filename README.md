# PlanWrite v2

Automated content creation system built with FastAPI + HTMX.

## Features

- **Plan-Then-Write Pipeline**: Generate outlines, then expand to full drafts
- **RAG-Powered Context**: Semantic search over your article corpus
- **Real-time Streaming**: Watch content generate live via SSE
- **Version History**: Never lose your work
- **Batch Processing**: Generate multiple articles at once
- **Compliance Validation**: Catch issues before publishing

## Quick Start

### Prerequisites

- Python 3.11+
- OpenAI API key

### Installation

```bash
# Clone the repository
git clone https://github.com/yourusername/planwrite-v2.git
cd planwrite-v2

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -e .

# Copy environment file and configure
cp .env.example .env
# Edit .env with your OpenAI API key
```

### Running the App

```bash
# Development mode with auto-reload
uvicorn app.main:app --reload

# Or use the script
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Visit http://localhost:8000

## Project Structure

```
planwrite-v2/
├── app/
│   ├── api/           # API endpoints
│   ├── models/        # SQLAlchemy models
│   ├── schemas/       # Pydantic schemas
│   ├── services/      # Business logic
│   ├── templates/     # Jinja2 + HTMX templates
│   ├── static/        # CSS/JS assets
│   ├── workers/       # Background job workers
│   ├── config.py      # Settings management
│   ├── database.py    # Database setup
│   └── main.py        # FastAPI app entry
├── data/              # Article corpus, evergreen links
├── storage/           # SQLite DB, exports
├── scripts/           # Utility scripts
├── tests/             # Test suite
└── pyproject.toml     # Project config
```

## Development

### Running Tests

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# With coverage
pytest --cov=app
```

### Linting

```bash
ruff check app/
ruff format app/
```

## Deployment

### Docker

```bash
docker build -t planwrite-v2 .
docker run -p 8000:8000 --env-file .env planwrite-v2
```

### Railway/Render

1. Connect your GitHub repository
2. Set environment variables in dashboard
3. Deploy automatically on push

## Configuration

| Variable | Description | Default |
|----------|-------------|---------|
| `OPENAI_API_KEY` | OpenAI API key | Required |
| `DATABASE_URL` | SQLite connection string | `sqlite+aiosqlite:///./storage/planwrite.db` |
| `LLM_MODEL` | Model for generation | `gpt-4o-mini` |
| `EMBED_MODEL` | Model for embeddings | `text-embedding-3-small` |
| `DEBUG` | Enable debug mode | `false` |

## License

MIT
