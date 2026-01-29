"""Article CRUD endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.article import Article, ArticleVersion
from app.schemas.article import ArticleCreate, ArticleResponse, ArticleUpdate
from app.config import get_settings

router = APIRouter()
settings = get_settings()
templates = Jinja2Templates(directory=settings.templates_dir)


@router.get("/")
async def list_articles(
    request: Request,
    db: AsyncSession = Depends(get_db),
    status: str | None = None,
    search: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    """List all articles with optional status filter.

    Returns HTML partial for HTMX requests, JSON otherwise.
    """
    query = select(Article).order_by(Article.updated_at.desc())

    if status:
        query = query.where(Article.status == status)

    if search:
        query = query.where(
            Article.title.ilike(f"%{search}%") |
            Article.keyword.ilike(f"%{search}%")
        )

    query = query.limit(limit).offset(offset)
    result = await db.execute(query)
    articles = result.scalars().all()

    # Check if request is from HTMX
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(
            "article/partials/table_rows.html",
            {"request": request, "articles": articles},
        )

    # Return JSON for API clients
    return [ArticleResponse.model_validate(a) for a in articles]


@router.post("/", response_model=ArticleResponse)
async def create_article(
    article: ArticleCreate,
    db: AsyncSession = Depends(get_db),
):
    """Create a new article."""
    db_article = Article(**article.model_dump())
    db.add(db_article)
    await db.flush()
    await db.refresh(db_article)
    return db_article


@router.get("/{article_id}")
async def get_article(
    request: Request,
    article_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Get a single article by ID.

    Returns HTML partial for HTMX requests, JSON otherwise.
    """
    result = await db.execute(select(Article).where(Article.id == article_id))
    article = result.scalar_one_or_none()

    if not article:
        raise HTTPException(status_code=404, detail="Article not found")

    # Check if request is from HTMX
    if request.headers.get("HX-Request"):
        # Convert model to dict for template
        article_dict = {
            "id": article.id,
            "title": article.title,
            "keyword": article.keyword,
            "state": article.state,
            "offer_id": article.offer_id,
            "offer_property": article.offer_property,
            "outline": article.outline or "",
            "draft": article.draft or "",
            "status": article.status,
            "compliance_score": article.compliance_score,
            "word_count": article.word_count,
        }
        return templates.TemplateResponse(
            "article/partials/edit_form.html",
            {"request": request, "article": article_dict},
        )

    return ArticleResponse.model_validate(article)


@router.put("/{article_id}", response_model=ArticleResponse)
async def update_article(
    article_id: int,
    article_update: ArticleUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update an article and create a version snapshot."""
    result = await db.execute(select(Article).where(Article.id == article_id))
    article = result.scalar_one_or_none()

    if not article:
        raise HTTPException(status_code=404, detail="Article not found")

    # Create version snapshot before updating
    version_count = await db.execute(
        select(ArticleVersion).where(ArticleVersion.article_id == article_id)
    )
    version_num = len(version_count.scalars().all()) + 1

    version = ArticleVersion(
        article_id=article_id,
        version=version_num,
        outline=article.outline,
        draft=article.draft,
    )
    db.add(version)

    # Update article
    update_data = article_update.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(article, field, value)

    await db.flush()
    await db.refresh(article)
    return article


@router.delete("/{article_id}")
async def delete_article(
    article_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Delete an article (soft delete by setting status to archived)."""
    result = await db.execute(select(Article).where(Article.id == article_id))
    article = result.scalar_one_or_none()

    if not article:
        raise HTTPException(status_code=404, detail="Article not found")

    article.status = "archived"
    await db.flush()
    return {"status": "archived", "id": article_id}


@router.get("/{article_id}/versions")
async def list_versions(
    article_id: int,
    db: AsyncSession = Depends(get_db),
):
    """List all versions of an article."""
    result = await db.execute(
        select(ArticleVersion)
        .where(ArticleVersion.article_id == article_id)
        .order_by(ArticleVersion.version.desc())
    )
    return result.scalars().all()


@router.post("/{article_id}/restore/{version}")
async def restore_version(
    article_id: int,
    version: int,
    db: AsyncSession = Depends(get_db),
):
    """Restore an article to a previous version."""
    # Get the version
    result = await db.execute(
        select(ArticleVersion).where(
            ArticleVersion.article_id == article_id,
            ArticleVersion.version == version,
        )
    )
    version_record = result.scalar_one_or_none()

    if not version_record:
        raise HTTPException(status_code=404, detail="Version not found")

    # Get the article
    result = await db.execute(select(Article).where(Article.id == article_id))
    article = result.scalar_one_or_none()

    if not article:
        raise HTTPException(status_code=404, detail="Article not found")

    # Restore
    article.outline = version_record.outline
    article.draft = version_record.draft
    await db.flush()

    return {"status": "restored", "version": version}
