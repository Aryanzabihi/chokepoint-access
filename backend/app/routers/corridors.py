"""routers/corridors.py — the per-chokepoint threshold panel.

Reference information, not client-scoped: same page for every logged-in
user, no ownership filtering needed (contrast clients.py/exposures.py,
which are always current_user-scoped).
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from .. import corridor_panel
from ..deps import current_user
from ..models import User

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


@router.get("/corridors", response_class=HTMLResponse)
def corridors_page(request: Request, user: User = Depends(current_user)):
    error = None
    try:
        global_reading, panels = corridor_panel.all_panels()
    except FileNotFoundError as exc:
        global_reading, panels, error = None, [], str(exc)
    return templates.TemplateResponse(request, "corridors.html",
        {"user": user, "global_reading": global_reading, "panels": panels, "error": error})
