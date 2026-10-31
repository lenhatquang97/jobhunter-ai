import secrets
from contextlib import asynccontextmanager
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import text

from .adapters import AdapterRegistry
from .bridge import OdooBridge
from .config import Settings
from .provider import ResponsesProvider
from .schemas import Archive, Principal, StrictModel, Submit
from .service import Service
from .store import Store


def create_app(settings=None, provider=None, bridge=None, adapters=None):
    settings = settings or Settings()
    service = Service(
        Store(settings.database_url, connect_args=settings.database_connect_args()),
        settings,
        provider or ResponsesProvider(settings),
        bridge or OdooBridge(settings),
        adapters if adapters is not None else AdapterRegistry.configured(settings.agent_adapters),
    )

    @asynccontextmanager
    async def lifespan(app):
        if not settings.bridge_secret.get_secret_value():
            raise RuntimeError("JOBHUNTER_BRIDGE_SECRET is required")
        await service.initialize()
        yield
        await service.close()

    app = FastAPI(
        title="Jobhunter Chat internal service",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.service = service

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return JSONResponse(status_code=422, content={"detail": "Invalid request fields"})

    def principal(
        authorization: str = Header(default=""), x_jobhunter_principal: str = Header(default="")
    ):
        expected = "Bearer " + settings.bridge_secret.get_secret_value()
        if not settings.bridge_secret.get_secret_value() or not secrets.compare_digest(
            authorization.encode(), expected.encode()
        ):
            raise HTTPException(401, "Unauthorized")
        try:
            return Principal.from_header(x_jobhunter_principal)
        except Exception:
            raise HTTPException(401, "Invalid principal") from None

    @app.get("/health")
    async def health():
        try:
            async with service.store.session() as session:
                await session.execute(text("SELECT 1"))
        except Exception:
            return JSONResponse(
                status_code=503, content={"status": "unavailable", "database": "unavailable"}
            )
        return {
            "status": "ok" if settings.openai_api_key.get_secret_value() else "degraded",
            "database": "ok",
            "provider_configured": bool(settings.openai_api_key.get_secret_value()),
            "specialist_adapters": service.adapters.status(),
        }

    @app.get("/v1/conversations")
    async def list_conversations(p=Depends(principal)):
        return await service.list_conversations(p)

    @app.post("/v1/conversations")
    async def create_conversation(body: StrictModel, p=Depends(principal)):
        return await service.create_conversation(p)

    @app.get("/v1/conversations/{cid}")
    async def get_conversation(
        cid: UUID,
        p=Depends(principal),
        x_odoo_capability: str = Header(default=""),
        x_odoo_database: str = Header(default=""),
        x_odoo_validation_run: UUID | None = Header(default=None),
    ):
        return await service.history(
            cid, p, x_odoo_capability, x_odoo_database, x_odoo_validation_run
        )

    @app.patch("/v1/conversations/{cid}")
    async def archive_conversation(cid: UUID, body: Archive, p=Depends(principal)):
        return await service.archive(cid, p, body.archived)

    @app.post("/v1/conversations/{cid}/messages")
    async def send_message(cid: UUID, body: Submit, p=Depends(principal)):
        return await service.submit(cid, p, body)

    @app.get("/v1/runs/{rid}/events")
    async def events(
        rid: UUID,
        after_seq: int = Query(default=0, ge=0, le=2147483647),
        p=Depends(principal),
        x_odoo_capability: str = Header(default=""),
        x_odoo_database: str = Header(default=""),
        x_odoo_validation_run: UUID | None = Header(default=None),
    ):
        return await service.events(
            rid, p, x_odoo_capability, x_odoo_database, after_seq, x_odoo_validation_run
        )

    @app.post("/v1/runs/{rid}/cancel")
    async def cancel(rid: UUID, p=Depends(principal)):
        return await service.cancel(rid, p)

    return app
