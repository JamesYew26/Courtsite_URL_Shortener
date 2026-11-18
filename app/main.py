from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

from fastapi import FastAPI

from app.config import Settings
from app.repository import URLRepository
from app.routes import router
from app.validator import URLValidator


def create_app(
    repo: Optional[URLRepository] = None,
    validator: Optional[URLValidator] = None,
) -> FastAPI:
    """Create and configure the Courtsite URL Shortener FastAPI application."""
    app_settings = Settings()
    resolved_repo = repo if repo is not None else URLRepository(app_settings.database_path)
    repo_initialized = False
    if repo is not None:
        resolved_repo.init_db()
        repo_initialized = True

    resolved_validator = (
        validator
        if validator is not None
        else URLValidator(timeout=app_settings.validation_timeout)
    )

    @asynccontextmanager
    async def lifespan(fastapi_app: FastAPI) -> AsyncIterator[None]:
        if not getattr(fastapi_app.state, "repo_initialized", False):
            fastapi_app.state.repo.init_db()
            fastapi_app.state.repo_initialized = True
        yield

    fastapi_app = FastAPI(
        title="Courtsite URL Shortener",
        version="0.1.0",
        description="URL shortener service with live validation, SSRF protection, and BI analytics.",
        lifespan=lifespan,
    )
    fastapi_app.state.repo = resolved_repo
    fastapi_app.state.repo_initialized = repo_initialized
    fastapi_app.state.validator = resolved_validator
    fastapi_app.state.settings = app_settings
    fastapi_app.include_router(router)
    return fastapi_app


app = create_app()
