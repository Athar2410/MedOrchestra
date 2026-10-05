import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import router
from app.config import Settings, get_settings
from app.graph.builder import build_graph
from app.runs import RunManager
from app.services.drug_graph import get_drug_graph


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Load the DDInter graph (~2s) before the first case instead of during it.
    await asyncio.to_thread(get_drug_graph)
    yield


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(level=logging.INFO)

    app = FastAPI(title="MedOrchestra API", version="0.2.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.runs = RunManager(build_graph(), settings)
    app.include_router(router)
    return app


app = create_app()
