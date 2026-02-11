"""FastAPI application entry point."""

import secrets
import time
import logging
import structlog
from contextlib import asynccontextmanager
from urllib.parse import quote

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import get_settings
from app.database import init_db

# Ensure structlog has a sink in container/runtime logs.
logging.basicConfig(level=logging.INFO, format="%(message)s")

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
    title="TopStoriesGenerator",
    description="Better Collective internal content tool",
    version="2.0.0",
    lifespan=lifespan,
)


def _is_public_path(path: str) -> bool:
    if path in {"/health", "/login"}:
        return True
    if path.startswith("/static"):
        return True
    return False


def _authenticate_user(username: str, password: str) -> bool:
    """Validate credentials against configured users."""
    user = username.strip()
    if not user:
        return False

    users = settings.auth_users
    # Constant-time username/password checks.
    for candidate_user, candidate_password in users.items():
        username_ok = secrets.compare_digest(user, str(candidate_user).strip())
        password_ok = secrets.compare_digest(password, str(candidate_password))
        if username_ok and password_ok:
            return True
    return False


class AuthenticationRequiredMiddleware(BaseHTTPMiddleware):
    """Gate app/API routes behind a simple session login."""

    async def dispatch(self, request: Request, call_next):
        started = time.perf_counter()
        if not settings.auth_enabled:
            response = await call_next(request)
            if request.url.path.startswith("/api/"):
                elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
                logger.info(
                    "api_request",
                    username="anonymous",
                    method=request.method,
                    path=request.url.path,
                    status_code=response.status_code,
                    duration_ms=elapsed_ms,
                )
            return response

        path = request.url.path
        if _is_public_path(path):
            return await call_next(request)

        session = request.scope.get("session")
        is_authenticated = bool(session.get("authenticated")) if isinstance(session, dict) else False
        if is_authenticated:
            response = await call_next(request)
            if path.startswith("/api/"):
                elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
                logger.info(
                    "api_request",
                    username=session.get("username", "unknown"),
                    method=request.method,
                    path=path,
                    status_code=response.status_code,
                    duration_ms=elapsed_ms,
                )
            return response

        if path.startswith("/api/"):
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            logger.info(
                "api_request_blocked",
                username="anonymous",
                method=request.method,
                path=path,
                status_code=401,
                duration_ms=elapsed_ms,
            )
            return JSONResponse({"detail": "Authentication required"}, status_code=401)

        next_path = request.url.path
        if request.url.query:
            next_path = f"{next_path}?{request.url.query}"
        login_url = f"/login?next={quote(next_path, safe='/?=&')}"
        return RedirectResponse(url=login_url, status_code=302)


# Add auth middleware first, then session middleware so session data
# is available when auth checks run.
app.add_middleware(AuthenticationRequiredMiddleware)

# Session middleware for cookie-based auth.
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.auth_session_secret or settings.secret_key,
    same_site="lax",
    https_only=not settings.debug,
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


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, next: str = "/articles/new"):
    """Render login page."""
    if not settings.auth_enabled:
        return RedirectResponse(url="/articles/new", status_code=302)
    if request.session.get("authenticated"):
        destination = next if next.startswith("/") else "/articles/new"
        return RedirectResponse(url=destination, status_code=302)
    return templates.TemplateResponse(
        "auth/login.html",
        {"request": request, "title": "Login", "next": next, "error": ""},
    )


@app.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form("/articles/new"),
):
    """Validate credentials and create session."""
    if not settings.auth_enabled:
        return RedirectResponse(url="/articles/new", status_code=303)

    if _authenticate_user(username, password):
        request.session.clear()
        request.session["authenticated"] = True
        request.session["username"] = username.strip()
        logger.info("login_success", username=username.strip())
        destination = next if next and next.startswith("/") else "/articles/new"
        return RedirectResponse(url=destination, status_code=303)

    logger.info("login_failed", username=username.strip())
    return templates.TemplateResponse(
        "auth/login.html",
        {
            "request": request,
            "title": "Login",
            "next": next if next.startswith("/") else "/articles/new",
            "error": "Invalid username or password.",
        },
        status_code=401,
    )


@app.post("/logout")
async def logout(request: Request):
    """Clear auth session."""
    username = request.session.get("username") if isinstance(request.session, dict) else ""
    request.session.clear()
    logger.info("logout", username=username or "unknown")
    return RedirectResponse(url="/login", status_code=303)


# Page routes
@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Internal tool entrypoint."""
    return RedirectResponse(url="/articles/new")


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
