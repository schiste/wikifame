from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, timedelta
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from wikifame.models import AttributionResult, utcnow
from wikifame.runtime import Runtime, build_runtime


def _isoformat(value: Any) -> str:
    return value.replace(tzinfo=UTC).isoformat().replace("+00:00", "Z")


def _etag(payload: dict[str, Any]) -> str:
    """
    A validator derived from the response itself.

    Hashing the payload rather than assembling a tag out of chosen fields means the tag
    cannot drift from what it claims to describe: anything that changes the body changes
    the tag, including the algorithm version, which is what a policy change moves and
    what nothing in the URL records.
    """
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return '"' + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32] + '"'


def _if_none_match(header: str | None, etag: str) -> bool:
    if not header:
        return False
    for candidate in header.split(","):
        candidate = candidate.strip()
        if candidate == "*":
            return True
        if candidate.startswith("W/"):
            candidate = candidate[2:].strip()
        if candidate == etag:
            return True
    return False


def _attribution_fields(result: AttributionResult, opted_out: bool) -> dict[str, Any]:
    """The part of a ready answer an opt-out changes.

    Opting a page out drops the names and keeps the count, so the sentence becomes
    "written by 47 people" rather than disappearing: the count is not a name, and losing
    it would hide that the article has a history at all. Both endpoints go through here
    so that neither can become a way around the list.

    Nothing is deleted. The stored row keeps its contributors — they are public page
    history — and the opt-out governs what is presented, which is what makes adding or
    removing an entry take effect without recomputing anything.
    """
    contributors: list[dict[str, Any]] = [] if opted_out else list(result.contributors)
    return {
        "contributors": contributors,
        "distinct_contributors": result.distinct_contributors,
        "other_contributors": max(0, result.distinct_contributors - len(contributors)),
        "opted_out": opted_out,
    }


def create_app(runtime: Runtime | None = None) -> FastAPI:
    app_runtime = runtime or build_runtime()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        app_runtime.database.create_schema()
        yield

    app = FastAPI(
        title="WikiFame API",
        version="0.1.0",
        description="Cached WikiWho attribution for the WikiFame gadget",
        lifespan=lifespan,
    )
    app.state.runtime = app_runtime
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(app_runtime.settings.cors_origins),
        allow_origin_regex=app_runtime.settings.cors_origin_regex or None,
        allow_methods=["GET"],
        allow_headers=["Accept"],
        max_age=86400,
    )

    @app.get("/healthz", include_in_schema=False)
    def health() -> dict[str, str]:
        app_runtime.database.ping()
        return {"status": "ok"}

    @app.get("/v1/stats")
    def stats() -> dict[str, Any]:
        return {
            "status": "ok",
            "algorithm_version": app_runtime.settings.algorithm_version,
            "supported_wikis": list(app_runtime.settings.supported_wikis),
            "active_wikis": app_runtime.repository.active_wikis(),
            "cache": app_runtime.repository.stats(),
            "opted_out": app_runtime.repository.optout_counts(),
        }

    @app.get("/v1/{wiki}/pages/{page_id}", response_model=None)
    def page_attribution(
        request: Request,
        response: Response,
        wiki: str,
        page_id: int,
        revision_id: int = Query(gt=0),
    ) -> Any:
        settings = app_runtime.settings
        if not app_runtime.resolver.is_enabled(wiki, settings.supported_wikis):
            raise HTTPException(status_code=404, detail="Wiki non pris en charge")
        if page_id <= 0:
            raise HTTPException(status_code=422, detail="page_id doit être positif")

        result = app_runtime.repository.get_result(
            wiki, page_id, revision_id, settings.algorithm_version
        )
        if result is not None:
            payload = {
                "status": "ready",
                "wiki": result.wiki,
                "page_id": result.page_id,
                "revision_id": result.revision_id,
                "title": result.title,
                "algorithm_version": result.algorithm_version,
                "metric": result.metric,
                **_attribution_fields(result, app_runtime.repository.is_opted_out(wiki, page_id)),
                "count_limited": result.count_limited,
                "countable_tokens": result.countable_tokens,
                "computed_at": _isoformat(result.computed_at),
                "methodology_url": settings.methodology_url,
            }
            # Not "immutable", though the URL names an exact revision whose text will
            # never change again. What changes is the policy applied to that text, and
            # the URL says nothing about which policy produced the answer.
            headers = {
                "Cache-Control": f"public, max-age={settings.ready_cache_seconds}",
                "ETag": _etag(payload),
                "X-WikiFame-Algorithm": settings.algorithm_version,
            }
            if _if_none_match(request.headers.get("if-none-match"), headers["ETag"]):
                return Response(status_code=304, headers=headers)
            response.headers.update(headers)
            return payload

        work = app_runtime.repository.get_work(
            wiki, page_id, revision_id, settings.algorithm_version
        )
        if work is not None and work.state == "dead":
            retry_at = work.updated_at + timedelta(seconds=settings.dead_retry_seconds)
            if not work.is_permanent and retry_at <= utcnow():
                app_runtime.repository.revive(work.id, priority=100)
            else:
                return JSONResponse(
                    status_code=503,
                    headers={"Cache-Control": "no-store", "Retry-After": "3600"},
                    content={
                        "status": "unavailable",
                        "wiki": wiki,
                        "page_id": page_id,
                        "revision_id": revision_id,
                        "error_code": work.error_code,
                    },
                )

        app_runtime.repository.enqueue(
            wiki=wiki,
            page_id=page_id,
            revision_id=revision_id,
            algorithm_version=settings.algorithm_version,
            priority=100,
        )
        return JSONResponse(
            status_code=202,
            headers={"Cache-Control": "no-store", "Retry-After": "30"},
            content={
                "status": "pending",
                "wiki": wiki,
                "page_id": page_id,
                "revision_id": revision_id,
                "retry_after": 30,
            },
        )

    @app.get("/v2/{wiki}/pages/{page_id}", response_model=None)
    def page_attribution_by_freshness(
        request: Request,
        response: Response,
        wiki: str,
        page_id: int,
        revision_id: int = Query(gt=0),
    ) -> Any:
        settings = app_runtime.settings
        if not app_runtime.resolver.is_enabled(wiki, settings.supported_wikis):
            raise HTTPException(status_code=404, detail="Wiki non pris en charge")
        if page_id <= 0:
            raise HTTPException(status_code=422, detail="page_id doit être positif")

        result = app_runtime.repository.get_latest_result(wiki, page_id, settings.algorithm_version)
        if result is not None:
            now = utcnow()
            fresh_until = result.computed_at + timedelta(seconds=settings.page_freshness_seconds)
            is_fresh = fresh_until > now
            refreshing = False

            if not is_fresh:
                work = app_runtime.repository.get_work(
                    wiki, page_id, revision_id, settings.algorithm_version
                )
                if work is not None and work.state == "dead":
                    retry_at = work.updated_at + timedelta(seconds=settings.dead_retry_seconds)
                    if not work.is_permanent and retry_at <= now:
                        app_runtime.repository.revive(work.id, priority=100)

                app_runtime.repository.enqueue_if_stale(
                    wiki=wiki,
                    page_id=page_id,
                    revision_id=revision_id,
                    algorithm_version=settings.algorithm_version,
                    priority=100,
                    freshness_seconds=settings.page_freshness_seconds,
                )
                work = app_runtime.repository.get_work(
                    wiki, page_id, revision_id, settings.algorithm_version
                )
                refreshing = work is not None and work.state in {"pending", "leased"}

            payload = {
                "status": "ready",
                "wiki": result.wiki,
                "page_id": result.page_id,
                "requested_revision_id": revision_id,
                "source_revision_id": result.revision_id,
                "title": result.title,
                "algorithm_version": result.algorithm_version,
                "metric": result.metric,
                **_attribution_fields(result, app_runtime.repository.is_opted_out(wiki, page_id)),
                "count_limited": result.count_limited,
                "countable_tokens": result.countable_tokens,
                "computed_at": _isoformat(result.computed_at),
                "fresh_until": _isoformat(fresh_until),
                "is_fresh": is_fresh,
                "refreshing": refreshing,
                "methodology_url": settings.methodology_url,
            }
            headers = {
                "Cache-Control": (
                    f"public, max-age={settings.page_cache_seconds}, "
                    f"stale-while-revalidate={settings.page_stale_while_revalidate_seconds}"
                ),
                "ETag": _etag(payload),
                "X-WikiFame-Algorithm": settings.algorithm_version,
                "X-WikiFame-Source-Revision": str(result.revision_id),
            }
            # The enqueue above has already happened, so a 304 still keeps a stale page
            # moving towards being recomputed. Only the body is spared.
            if _if_none_match(request.headers.get("if-none-match"), headers["ETag"]):
                return Response(status_code=304, headers=headers)
            response.headers.update(headers)
            return payload

        work = app_runtime.repository.get_work(
            wiki, page_id, revision_id, settings.algorithm_version
        )
        if work is not None and work.state == "dead":
            retry_at = work.updated_at + timedelta(seconds=settings.dead_retry_seconds)
            if not work.is_permanent and retry_at <= utcnow():
                app_runtime.repository.revive(work.id, priority=100)
            else:
                return JSONResponse(
                    status_code=503,
                    headers={"Cache-Control": "no-store", "Retry-After": "3600"},
                    content={
                        "status": "unavailable",
                        "wiki": wiki,
                        "page_id": page_id,
                        "requested_revision_id": revision_id,
                        "error_code": work.error_code,
                    },
                )

        app_runtime.repository.enqueue_if_stale(
            wiki=wiki,
            page_id=page_id,
            revision_id=revision_id,
            algorithm_version=settings.algorithm_version,
            priority=100,
            freshness_seconds=settings.page_freshness_seconds,
        )
        return JSONResponse(
            status_code=202,
            headers={"Cache-Control": "no-store", "Retry-After": "30"},
            content={
                "status": "pending",
                "wiki": wiki,
                "page_id": page_id,
                "requested_revision_id": revision_id,
                "retry_after": 30,
            },
        )

    return app


app = create_app()
