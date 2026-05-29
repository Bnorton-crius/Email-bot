"""FastAPI dashboard for the email outreach bot."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from config import load_config
from database.tracker import (
    approve_draft,
    db_conn,
    get_all_companies,
    get_all_drafts,
    get_stats,
    get_recent_companies,
    reject_draft,
    track_click,
    track_open,
)

# 1×1 transparent GIF
_PIXEL = (
    b"\x47\x49\x46\x38\x39\x61\x01\x00\x01\x00\x80\x00\x00"
    b"\xff\xff\xff\x00\x00\x00\x21\xf9\x04\x00\x00\x00\x00\x00"
    b"\x2c\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02\x44\x01\x00\x3b"
)

cfg = load_config()

# Ensure screenshots dir exists before mounting
Path("data/screenshots").mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Email Bot Dashboard")

_templates_dir = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_templates_dir))

app.mount(
    "/screenshots",
    StaticFiles(directory="data/screenshots"),
    name="screenshots",
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _score_color(score) -> str:
    if score is None:
        return "gray"
    if score >= 70:
        return "green"
    if score >= 40:
        return "yellow"
    return "red"


def _score_label(score) -> str:
    if score is None:
        return "—"
    return str(score)


# Make helpers available in templates
templates.env.globals["score_color"] = _score_color
templates.env.globals["score_label"] = _score_label


# ── page routes ───────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    with db_conn(cfg.db_path) as conn:
        stats = get_stats(conn)
        recent = get_recent_companies(conn, limit=10)
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "stats": stats, "recent": recent},
    )


@app.get("/companies", response_class=HTMLResponse)
async def companies(request: Request, page: int = 1, per_page: int = 50):
    with db_conn(cfg.db_path) as conn:
        all_companies = get_all_companies(conn)
    total = len(all_companies)
    start = (page - 1) * per_page
    page_companies = all_companies[start : start + per_page]
    total_pages = max(1, (total + per_page - 1) // per_page)
    return templates.TemplateResponse(
        "companies.html",
        {
            "request": request,
            "companies": page_companies,
            "page": page,
            "total_pages": total_pages,
            "total": total,
        },
    )


@app.get("/emails", response_class=HTMLResponse)
async def emails(request: Request):
    with db_conn(cfg.db_path) as conn:
        drafts = get_all_drafts(conn)
    return templates.TemplateResponse(
        "emails.html",
        {"request": request, "drafts": drafts},
    )


# ── HTMX action routes ────────────────────────────────────────────────────────

@app.post("/emails/{email_id}/approve", response_class=HTMLResponse)
async def approve_email(email_id: int, request: Request):
    with db_conn(cfg.db_path) as conn:
        approve_draft(conn, email_id)
    return HTMLResponse(
        f'<div id="email-{email_id}" class="rounded-lg bg-gray-800 border border-green-700 p-4">'
        f'<span class="text-green-400 font-semibold">✓ Approved — will be sent on next send run</span>'
        f"</div>"
    )


@app.post("/emails/{email_id}/reject", response_class=HTMLResponse)
async def reject_email(email_id: int):
    with db_conn(cfg.db_path) as conn:
        reject_draft(conn, email_id)
    # Return empty string — HTMX will swap the card out with nothing
    return HTMLResponse("")


# ── tracking endpoints ────────────────────────────────────────────────────────

@app.get("/track/open/{token}")
async def tracking_open(token: str):
    try:
        with db_conn(cfg.db_path) as conn:
            track_open(conn, token)
    except Exception:
        pass
    return Response(content=_PIXEL, media_type="image/gif")


@app.get("/track/click/{token}")
async def tracking_click(token: str, url: str = ""):
    try:
        with db_conn(cfg.db_path) as conn:
            track_click(conn, token)
    except Exception:
        pass
    if url:
        return RedirectResponse(url=url, status_code=302)
    return Response("ok")
