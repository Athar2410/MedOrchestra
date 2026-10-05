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
from app.services.retrieval import PubMedRetriever, get_retriever

logger = logging.getLogger(__name__)


def _warm_retriever() -> None:
    retriever = get_retriever()
    if not isinstance(retriever, PubMedRetriever):
        logger.warning("PubMed retrieval not configured — diagnoses will be uncited")
        return
    try:
        retriever.warm_up()  # opens the Supabase pool, loads MedCPT query + cross encoders
        logger.info("PubMed retriever ready")
    except Exception:
        logger.exception("PubMed retriever warm-up failed; will retry on first use")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Load the DDInter graph (~2s) and the MedCPT models (~5-10s) before the first case
    # instead of during it.
    await asyncio.gather(asyncio.to_thread(get_drug_graph), asyncio.to_thread(_warm_retriever))
    yield
    retriever = get_retriever()
    if isinstance(retriever, PubMedRetriever):
        await asyncio.to_thread(retriever.close)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(level=logging.INFO)

    app = FastAPI(title="MedOrchestra API", version="0.3.0", lifespan=lifespan)
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
