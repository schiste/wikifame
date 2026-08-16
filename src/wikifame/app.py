from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, timedelta
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from wikifame.models import utcnow
from wikifame.runtime import Runtime, build_runtime


def _isoformat(value: Any) -> str:
    return value.replace(tzinfo=UTC).isoformat().replace("+00:00", "Z")


def create_app(runtime: Runtime | None = None) -> FastAPI:
    app_runtime = runtime or build_runtime()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        app_runtime.database.create_schema()
        yield

    app = FastAPI(
        title="WikiFame API",
        version="0.1.0",
        description="Cached WikiWho attribution for the ContributeursHumains gadget",
        lifespan=lifespan,
    )
    app.state.runtime = app_runtime
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(app_runtime.settings.cors_origins),
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
            "cache": app_runtime.repository.stats(),
        }

    @app.get("/v1/{wiki}/pages/{page_id}", response_model=None)
    def page_attribution(
        response: Response,
        wiki: str,
        page_id: int,
        revision_id: int = Query(gt=0),
    ) -> Any:
        settings = app_runtime.settings
        if wiki not in settings.supported_wikis:
            raise HTTPException(status_code=404, detail="Wiki non pris en charge")
        if page_id <= 0:
            raise HTTPException(status_code=422, detail="page_id doit être positif")

        result = app_runtime.repository.get_result(
            wiki, page_id, revision_id, settings.algorithm_version
        )
        if result is not None:
            other_contributors = max(0, result.distinct_contributors - len(result.contributors))
            response.headers["Cache-Control"] = (
                f"public, max-age={settings.ready_cache_seconds}, immutable"
            )
            response.headers["X-WikiFame-Algorithm"] = settings.algorithm_version
            return {
                "status": "ready",
                "wiki": result.wiki,
                "page_id": result.page_id,
                "revision_id": result.revision_id,
                "title": result.title,
                "algorithm_version": result.algorithm_version,
                "metric": result.metric,
                "contributors": result.contributors,
                "distinct_contributors": result.distinct_contributors,
                "other_contributors": other_contributors,
                "count_limited": result.count_limited,
                "countable_tokens": result.countable_tokens,
                "computed_at": _isoformat(result.computed_at),
                "methodology_url": settings.methodology_url,
            }

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
        response: Response,
        wiki: str,
        page_id: int,
        revision_id: int = Query(gt=0),
    ) -> Any:
        settings = app_runtime.settings
        if wiki not in settings.supported_wikis:
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

            response.headers["Cache-Control"] = (
                f"public, max-age={settings.page_cache_seconds}, "
                f"stale-while-revalidate={settings.page_stale_while_revalidate_seconds}"
            )
            response.headers["X-WikiFame-Algorithm"] = settings.algorithm_version
            response.headers["X-WikiFame-Source-Revision"] = str(result.revision_id)
            other_contributors = max(0, result.distinct_contributors - len(result.contributors))
            return {
                "status": "ready",
                "wiki": result.wiki,
                "page_id": result.page_id,
                "requested_revision_id": revision_id,
                "source_revision_id": result.revision_id,
                "title": result.title,
                "algorithm_version": result.algorithm_version,
                "metric": result.metric,
                "contributors": result.contributors,
                "distinct_contributors": result.distinct_contributors,
                "other_contributors": other_contributors,
                "count_limited": result.count_limited,
                "countable_tokens": result.countable_tokens,
                "computed_at": _isoformat(result.computed_at),
                "fresh_until": _isoformat(fresh_until),
                "is_fresh": is_fresh,
                "refreshing": refreshing,
                "methodology_url": settings.methodology_url,
            }

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
