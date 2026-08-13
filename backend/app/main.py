"""main.py — the FastAPI app.

Mounts the dashboard (server-rendered Jinja2, session-cookie auth) and the
versioned JSON API (session cookie or API key) as separate routers so the
auth requirement for each is explicit at the include_router call, not
buried in per-endpoint decorators.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .routers import (
    api_keys, api_v1, clients, dashboard, economic, exposures, reports, strategy_decisions,
)

APP_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))

app = FastAPI(title="Chokepoint Access — client portfolio")

# The existing static site's design system, served as-is (at /static/site.css)
# so the dashboard looks like the same product rather than a bolted-on SaaS
# shell. Mounts the whole src/ directory; only site.css is linked from
# templates, but nothing in src/ is sensitive.
app.mount("/static", StaticFiles(directory=str(APP_DIR.parent.parent / "src")),
          name="site-assets")

app.include_router(dashboard.router)
app.include_router(clients.router, prefix="/api/v1/clients", tags=["clients"])
app.include_router(exposures.router, prefix="/api/v1/exposures", tags=["exposures"])
app.include_router(reports.router, tags=["reports"])
app.include_router(economic.router, tags=["economic-scenarios"])
app.include_router(strategy_decisions.router, tags=["strategy-decisions"])
app.include_router(api_keys.router, prefix="/api/v1/api-keys", tags=["api-keys"])
app.include_router(api_v1.router, prefix="/api/v1", tags=["public-api"])


@app.get("/healthz")
def healthz():
    return {"ok": True}
