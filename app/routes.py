import hashlib
from typing import Dict, List, Optional, Tuple
import urllib.parse

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse

from app.config import Settings
from app.keygen import generate_key
from app.repository import KeyCollisionError, URLRepository
from app.schemas import LocationStatsItem, ShortenRequest, ShortenResponse, URLStatsResponse
from app.validator import URLValidationError, URLValidator

router = APIRouter()
settings = Settings()

MAX_KEY_COLLISION_RETRIES = 5


def get_repo(request: Request) -> URLRepository:
    """Retrieve the URLRepository instance from application state."""
    repo: URLRepository = request.app.state.repo
    if not getattr(request.app.state, "repo_initialized", False):
        repo.init_db()
        request.app.state.repo_initialized = True
    return repo


def get_validator(request: Request) -> URLValidator:
    """Retrieve the URLValidator instance from application state."""
    return request.app.state.validator


def _extract_host_port(parsed: urllib.parse.ParseResult) -> Optional[Tuple[str, int]]:
    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https") or not parsed.hostname:
        return None
    try:
        port = parsed.port
    except ValueError:
        return None
    effective_port = port if port is not None else (443 if scheme == "https" else 80)
    return (parsed.hostname.lower(), effective_port)


def _is_self_referential_short_url(url: str, request: Request, base_url: str) -> bool:
    parsed_target = urllib.parse.urlparse(url.strip())
    if not parsed_target.path.startswith("/shorten_url"):
        return False

    target_host_port = _extract_host_port(parsed_target)
    if target_host_port is None:
        return False

    request_host_port = _extract_host_port(urllib.parse.urlparse(str(request.url)))
    base_host_port = _extract_host_port(urllib.parse.urlparse(base_url))
    return target_host_port in {request_host_port, base_host_port}


def _extract_client_ip(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        first_ip = forwarded_for.split(",")[0].strip()
        if first_ip:
            return first_ip
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


@router.post(
    "/shorten_url/",
    response_model=ShortenResponse,
    status_code=status.HTTP_201_CREATED,
)
@router.post(
    "/shorten_url",
    response_model=ShortenResponse,
    status_code=status.HTTP_201_CREATED,
    include_in_schema=False,
)
async def shorten_url(
    payload: ShortenRequest,
    request: Request,
    repo: URLRepository = Depends(get_repo),
    validator: URLValidator = Depends(get_validator),
) -> ShortenResponse:
    """Validate and shorten a target URL, returning a unique key and short URL."""
    app_settings: Settings = getattr(request.app.state, "settings", settings)

    if _is_self_referential_short_url(payload.url, request, app_settings.base_url):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot shorten a URL pointing to this shortener service",
        )

    try:
        normalized_url = await validator.validate(payload.url)
    except URLValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=exc.message,
        ) from exc

    record = None
    for _ in range(MAX_KEY_COLLISION_RETRIES):
        key = generate_key(app_settings.key_length)
        try:
            record = repo.create_short_url(
                key=key,
                original_url=normalized_url,
                location=payload.location,
                campaign=payload.campaign,
            )
            break
        except KeyCollisionError:
            continue

    if record is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate a unique short URL key",
        )

    short_url = f"{str(request.base_url).rstrip('/')}/shorten_url/{record['key']}"
    return ShortenResponse(
        key=record["key"],
        short_url=short_url,
        original_url=record["original_url"],
        location=record["location"],
        campaign=record["campaign"],
        created_at=record["created_at"],
    )


@router.get("/shorten_url/{key}")
def redirect_short_url(
    key: str,
    request: Request,
    repo: URLRepository = Depends(get_repo),
) -> RedirectResponse:
    """Redirect a short key to its original URL while recording BI visit telemetry."""
    record = repo.get_short_url(key)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Short URL key not found",
        )

    client_ip = _extract_client_ip(request)
    user_agent = request.headers.get("user-agent")
    referrer = request.query_params.get("ref") or request.headers.get("referer")
    visitor_hash = hashlib.sha256(
        f"{client_ip}:{user_agent or ''}".encode("utf-8")
    ).hexdigest()[:16]

    repo.record_visit(
        key,
        visitor_hash=visitor_hash,
        referrer=referrer,
        user_agent=user_agent,
    )

    return RedirectResponse(
        url=record["original_url"],
        status_code=status.HTTP_302_FOUND,
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
    )


@router.get("/shorten_url/{key}/stats", response_model=URLStatsResponse)
def get_url_stats(
    key: str,
    repo: URLRepository = Depends(get_repo),
) -> URLStatsResponse:
    """Return aggregated BI visit statistics for a short URL key."""
    stats = repo.get_url_stats(key)
    if stats is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Short URL key not found",
        )
    return stats


@router.get("/analytics/locations", response_model=List[LocationStatsItem])
def get_location_analytics(
    repo: URLRepository = Depends(get_repo),
) -> List[LocationStatsItem]:
    """Return aggregated link and visit leaderboard grouped by location and campaign."""
    return repo.get_location_leaderboard()


@router.get("/health")
def health_check() -> Dict[str, str]:
    """Health check endpoint."""
    return {"status": "ok"}
