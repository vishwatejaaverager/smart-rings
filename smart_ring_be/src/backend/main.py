
from contextlib import asynccontextmanager
from fastapi import FastAPI

from src.backend.routes import automations, devices, fire_tv, health, streams, voice
from src.vision.loop_engine import loop_engine


import os


@asynccontextmanager
async def lifespan(app: FastAPI):
    start_headless = os.getenv("START_HEADLESS_LOOP", "false").lower() in ("true", "1", "yes")
    if start_headless:
        loop_engine.start()
    yield
    if start_headless:
        loop_engine.stop()


app = FastAPI(
    title="Smart Ring AI",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(fire_tv.router)
app.include_router(health.router, prefix="/api/v1")
app.include_router(devices.router, prefix="/api/v1")
app.include_router(streams.router, prefix="/api/v1")
app.include_router(automations.router, prefix="/api/v1")
app.include_router(voice.router, prefix="/api/v1")


@app.get("/")
async def root():
    return {
        "name": "Smart Ring AI",
        "status": "running",
        "docs": "/docs",
        "loop_engine": loop_engine.get_status(),
    }

