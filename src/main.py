import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from src.api.routes import router, STATIC_DIR
from src.services.queue_manager import queue_worker

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Arranca el worker de la cola al iniciar el servidor
    app.state.worker = asyncio.create_task(queue_worker())
    yield
    app.state.worker.cancel()

app = FastAPI(title="Manga Translator NAS", lifespan=lifespan)

STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Incluir las rutas
app.include_router(router)
