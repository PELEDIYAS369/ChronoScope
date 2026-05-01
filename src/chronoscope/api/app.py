"""
ChronoScope AI — FastAPI Application
The main ASGI application entry point.
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from src.chronoscope.api.routes import router
from src.chronoscope.api.dashboard_routes import dashboard_router

app = FastAPI(
    title="ChronoScope AI",
    description=(
        "Universal telemetry replay, audit, and anomaly detection platform. "
        "Real spacecraft data. Deterministic replay. "
        "Tamper-evident audit. Explainable AI."
    ),
    version="1.0.0",
    contact={
        "name": "ChronoScope AI",
        "url": "https://github.com/chronoscope-ai",
    },
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api/v1")
app.include_router(dashboard_router)


@app.get("/", tags=["Root"])
async def root():
    return {
        "product": "ChronoScope AI",
        "version": "1.0.0",
        "docs": "/docs",
        "health": "/api/v1/health",
        "dashboard": "/dashboard",
    }