"""FastAPI application entry point."""

import structlog
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import get_settings
from app.database import init_db

# Configure structured logging
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger()
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events."""
    # Startup
    logger.info("Starting PlanWrite v2", version="2.0.0")

    # Ensure storage directories exist
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    (settings.storage_dir / "exports").mkdir(exist_ok=True)

    # Initialize database
    await init_db()
    logger.info("Database initialized")

    yield

    # Shutdown
    logger.info("Shutting down PlanWrite v2")


# Create FastAPI app
app = FastAPI(
    title="PlanWrite v2",
    description="Automated content creation system",
    version="2.0.0",
    lifespan=lifespan,
)

# Mount static files
app.mount(
    "/static",
    StaticFiles(directory=settings.static_dir),
    name="static",
)

# Setup templates
templates = Jinja2Templates(directory=settings.templates_dir)


# Import and include routers
from app.api import articles, generate, offers, events, odds, admin

app.include_router(articles.router, prefix="/api/articles", tags=["articles"])
app.include_router(offers.router, prefix="/api/offers", tags=["offers"])
app.include_router(generate.router, prefix="/api/generate", tags=["generate"])
app.include_router(events.router)  # Already has /api/events prefix
app.include_router(odds.router)  # Already has /api/odds prefix
app.include_router(admin.router)


# Page routes
@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Dashboard - list of articles."""
    return templates.TemplateResponse(
        "dashboard.html",
        {"request": request, "title": "Dashboard"},
    )


@app.get("/articles/new", response_class=HTMLResponse)
async def new_article(request: Request):
    """New article wizard."""
    return templates.TemplateResponse(
        "article/new.html",
        {"request": request, "title": "New Article"},
    )


@app.get("/articles/{article_id}", response_class=HTMLResponse)
async def view_article(request: Request, article_id: int):
    """View/edit article."""
    return templates.TemplateResponse(
        "article/edit.html",
        {"request": request, "title": "Edit Article", "article_id": article_id},
    )


@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    """Admin tools."""
    return templates.TemplateResponse(
        "admin/index.html",
        {"request": request, "title": "Admin"},
    )


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "version": "2.0.0"}
