"""FastAPI dashboard for the email outreach bot."""

import json
import os
import threading
from collections import deque
from pathlib import Path
from typing import Callable

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from config import load_config
from database.tracker import (
    approve_draft,
    db_conn,
    get_all_companies,
    get_all_drafts,
    get_recent_companies,
    get_stats,
    get_unscored,
    get_unsent,
    get_unsent_drafts,
    mark_sent,
    reject_draft,
    save_email_draft,
    track_click,
    track_open,
    update_score,
    update_screenshot,
    upsert_company,
)
from email_bot.generator import EmailDraft, generate_email
from email_bot.sender import SmtpConfig, send_email
from scraper.company_finder import find_companies
from scraper.http import make_session
from scraper.screenshot import take_screenshot
from scraper.website_analyzer import ScoreResult, analyze_website, extract_email_from_site

# 1×1 transparent GIF
_PIXEL = (
    b"\x47\x49\x46\x38\x39\x61\x01\x00\x01\x00\x80\x00\x00"
    b"\xff\xff\xff\x00\x00\x00\x21\xf9\x04\x00\x00\x00\x00\x00"
    b"\x2c\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02\x44\x01\x00\x3b"
)

cfg = load_config()

Path("data/screenshots").mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Email Bot Dashboard")

_templates_dir = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_templates_dir))

app.mount("/screenshots", StaticFiles(directory="data/screenshots"), name="screenshots")


# ── template helpers ──────────────────────────────────────────────────────────

def _score_color(score) -> str:
    if score is None:
        return "gray"
    return "green" if score >= 70 else "yellow" if score >= 40 else "red"


def _score_label(score) -> str:
    return "—" if score is None else str(score)


templates.env.globals["score_color"] = _score_color
templates.env.globals["score_label"] = _score_label


# ── background task runner ────────────────────────────────────────────────────

_task_log: deque[str] = deque(maxlen=400)
_task_state: dict = {"status": "idle", "operation": ""}
_task_lock = threading.Lock()


def _log(msg: str) -> None:
    _task_log.append(msg)


def _start_task(name: str, fn: Callable) -> bool:
    """Launch fn() in a daemon thread. Returns False if a task is already running."""
    with _task_lock:
        if _task_state["status"] == "running":
            return False
        _task_state["status"] = "running"
        _task_state["operation"] = name
    _task_log.clear()
    _log(f"▶  {name}")

    def _wrapper():
        try:
            fn()
            with _task_lock:
                _task_state["status"] = "done"
            _log("✓  Finished")
        except Exception as exc:
            with _task_lock:
                _task_state["status"] = "error"
            _log(f"✗  {exc}")

    threading.Thread(target=_wrapper, daemon=True).start()
    return True


# ── task implementations ──────────────────────────────────────────────────────

def _do_find(industry: str, location: str, limit: int) -> None:
    _log(f"Searching: '{industry}' in {location} (limit {limit})…")
    companies = find_companies(industry, location, limit)
    _log(f"Search returned {len(companies)} results")
    new_count = 0
    with db_conn(cfg.db_path) as conn:
        for c in companies:
            rid = upsert_company(conn, c)
            if rid is not None:
                new_count += 1
                _log(f"  + {c['domain']}")
    _log(f"Added {new_count} new  ({len(companies) - new_count} duplicates skipped)")


def _do_analyze() -> None:
    session = make_session()
    with db_conn(cfg.db_path) as conn:
        companies = get_unscored(conn)
    if not companies:
        _log("No unscored companies found")
        return
    _log(f"Analysing {len(companies)} websites…")
    for i, company in enumerate(companies, 1):
        domain = company["domain"]
        _log(f"[{i}/{len(companies)}] {domain}")
        result = analyze_website(domain, session)
        email_addr = company.get("email")
        if not email_addr and result.status != "unreachable":
            email_addr = extract_email_from_site(domain, session)
        shot_path = take_screenshot(domain) if result.status != "unreachable" else None
        with db_conn(cfg.db_path) as conn:
            update_score(conn, company["id"], result)
            if email_addr and not company.get("email"):
                conn.execute("UPDATE companies SET email=? WHERE id=?",
                             (email_addr, company["id"]))
            if shot_path:
                update_screenshot(conn, company["id"], shot_path)
        status_icon = "✓" if result.status == "ok" else "✗"
        _log(f"  {status_icon} {result.total}/100  [{result.platform}]  {result.status}")


def _do_generate(min_score: int, max_score: int) -> None:
    if not cfg.anthropic_api_key:
        raise ValueError("ANTHROPIC_API_KEY is not set in .env")
    import anthropic
    client = anthropic.Anthropic(api_key=cfg.anthropic_api_key)
    with db_conn(cfg.db_path) as conn:
        companies = get_unsent(conn, min_score=min_score, max_score=max_score)
    if not companies:
        _log(f"No companies in score range {min_score}–{max_score} need an email draft")
        return
    _log(f"Generating emails for {len(companies)} companies…")
    cached_total = 0
    for company in companies:
        _log(f"  Writing email for {company['name']}…")
        bd_data = json.loads(company.get("score_breakdown") or "{}")
        score_result = ScoreResult(
            domain=company["domain"],
            total=company.get("website_score") or 0,
            breakdown=bd_data.get("breakdown", {}),
            status=company.get("status", "ok"),
            response_time_ms=bd_data.get("response_time_ms", 0),
            notes=bd_data.get("notes", []),
            platform=company.get("platform") or "Unknown",
        )
        try:
            draft = generate_email(company, score_result, client,
                                   company.get("screenshot_path"))
            cached_total += draft.cached_tokens
            with db_conn(cfg.db_path) as conn:
                save_email_draft(conn, company["id"], draft.subject, draft.body)
            _log(f"  ✓ \"{draft.subject}\"")
        except Exception as exc:
            _log(f"  ✗ {company['name']}: {exc}")
    _log(f"Done  (cached tokens: {cached_total})")


def _do_send(dry_run: bool, approved_only: bool) -> None:
    with db_conn(cfg.db_path) as conn:
        drafts = get_unsent_drafts(conn, approved_only=approved_only)
    if not drafts:
        _log("No pending drafts to send" +
             (" (try without --approved-only)" if approved_only else ""))
        return
    mode = "DRY RUN — " if dry_run else ""
    _log(f"{mode}Sending {len(drafts)} emails…")
    smtp_cfg = SmtpConfig(
        host=cfg.smtp.host, port=cfg.smtp.port,
        user=cfg.smtp.user, password=cfg.smtp.password,
        from_email=cfg.smtp.from_email, from_name=cfg.smtp.from_name,
        send_delay_seconds=cfg.smtp.send_delay_seconds,
    )
    sent = 0
    for row in drafts:
        to_email = row.get("email") or ""
        if not dry_run and not to_email:
            _log(f"  — Skipped {row['name']}  (no email address)")
            continue
        draft = EmailDraft(subject=row["subject"], body=row["body"],
                           company_id=row["company_id"])
        ok = send_email(
            draft, to_email=to_email or "(preview)",
            smtp_cfg=smtp_cfg, dry_run=dry_run,
            delay_seconds=0 if dry_run else smtp_cfg.send_delay_seconds,
            tracking_token=row.get("tracking_token"),
            tracking_url=cfg.tracking_url,
        )
        if ok:
            if not dry_run:
                with db_conn(cfg.db_path) as conn:
                    mark_sent(conn, row["id"])
            verb = "Preview" if dry_run else "Sent"
            _log(f"  ✓ {verb}: {row['name']}  →  {to_email or '(no address)'}")
            sent += 1
    verb = "Previewed" if dry_run else "Sent"
    _log(f"{verb} {sent}/{len(drafts)}")


def _do_run(industry: str, location: str, limit: int,
            max_score: int, dry_run: bool) -> None:
    _do_find(industry, location, limit)
    _do_analyze()
    _do_generate(min_score=0, max_score=max_score)
    _do_send(dry_run=dry_run, approved_only=False)


# ── page routes ───────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    with db_conn(cfg.db_path) as conn:
        stats = get_stats(conn)
        recent = get_recent_companies(conn, limit=10)
    return templates.TemplateResponse(
        "index.html", {"request": request, "stats": stats, "recent": recent}
    )


@app.get("/companies", response_class=HTMLResponse)
async def companies_page(request: Request, page: int = 1, per_page: int = 50):
    with db_conn(cfg.db_path) as conn:
        all_companies = get_all_companies(conn)
    total = len(all_companies)
    start = (page - 1) * per_page
    total_pages = max(1, (total + per_page - 1) // per_page)
    return templates.TemplateResponse(
        "companies.html",
        {"request": request, "companies": all_companies[start:start + per_page],
         "page": page, "total_pages": total_pages, "total": total},
    )


@app.get("/emails", response_class=HTMLResponse)
async def emails_page(request: Request):
    with db_conn(cfg.db_path) as conn:
        drafts = get_all_drafts(conn)
    return templates.TemplateResponse(
        "emails.html", {"request": request, "drafts": drafts}
    )


@app.get("/pipeline", response_class=HTMLResponse)
async def pipeline_page(request: Request):
    with db_conn(cfg.db_path) as conn:
        unscored_count = len(get_unscored(conn))
        stats = get_stats(conn)
    return templates.TemplateResponse(
        "pipeline.html",
        {"request": request, "unscored_count": unscored_count, "stats": stats},
    )


# ── email draft actions ───────────────────────────────────────────────────────

@app.post("/emails/{email_id}/approve", response_class=HTMLResponse)
async def approve_email(email_id: int):
    with db_conn(cfg.db_path) as conn:
        approve_draft(conn, email_id)
    return HTMLResponse(
        f'<div id="email-{email_id}" class="rounded-lg bg-gray-800 border border-green-700 p-4">'
        f'<span class="text-green-400 font-semibold">✓ Approved — ready to send</span>'
        f"</div>"
    )


@app.post("/emails/{email_id}/reject", response_class=HTMLResponse)
async def reject_email(email_id: int):
    with db_conn(cfg.db_path) as conn:
        reject_draft(conn, email_id)
    return HTMLResponse("")


# ── pipeline API endpoints ────────────────────────────────────────────────────

@app.post("/api/find", response_class=HTMLResponse)
async def api_find(
    industry: str = Form(...),
    location: str = Form(...),
    limit: int = Form(20),
):
    ok = _start_task(f"Find: {industry} in {location}",
                     lambda: _do_find(industry, location, limit))
    if not ok:
        return HTMLResponse('<p class="text-yellow-400 text-sm">⚠ Another task is running — please wait</p>')
    return HTMLResponse('<p class="text-green-400 text-sm">▶ Started — watch the log →</p>')


@app.post("/api/analyze", response_class=HTMLResponse)
async def api_analyze():
    ok = _start_task("Analyse Websites", _do_analyze)
    if not ok:
        return HTMLResponse('<p class="text-yellow-400 text-sm">⚠ Another task is running</p>')
    return HTMLResponse('<p class="text-green-400 text-sm">▶ Started — watch the log →</p>')


@app.post("/api/generate", response_class=HTMLResponse)
async def api_generate(
    min_score: int = Form(0),
    max_score: int = Form(70),
):
    ok = _start_task(f"Generate Emails (score {min_score}–{max_score})",
                     lambda: _do_generate(min_score, max_score))
    if not ok:
        return HTMLResponse('<p class="text-yellow-400 text-sm">⚠ Another task is running</p>')
    return HTMLResponse('<p class="text-green-400 text-sm">▶ Started — watch the log →</p>')


@app.post("/api/send", response_class=HTMLResponse)
async def api_send(
    dry_run: str = Form(""),
    approved_only: str = Form(""),
):
    dr = bool(dry_run)
    ao = bool(approved_only)
    label = ("Dry Run — " if dr else "") + ("Approved Only" if ao else "Send All")
    ok = _start_task(label, lambda: _do_send(dr, ao))
    if not ok:
        return HTMLResponse('<p class="text-yellow-400 text-sm">⚠ Another task is running</p>')
    return HTMLResponse('<p class="text-green-400 text-sm">▶ Started — watch the log →</p>')


@app.post("/api/run", response_class=HTMLResponse)
async def api_run(
    industry: str = Form(...),
    location: str = Form(...),
    limit: int = Form(20),
    max_score: int = Form(70),
    dry_run: str = Form(""),
):
    dr = bool(dry_run)
    ok = _start_task(
        f"Full Pipeline: {industry} in {location}",
        lambda: _do_run(industry, location, limit, max_score, dr),
    )
    if not ok:
        return HTMLResponse('<p class="text-yellow-400 text-sm">⚠ Another task is running</p>')
    return HTMLResponse('<p class="text-green-400 text-sm">▶ Pipeline started — watch the log →</p>')


# ── live status fragment (HTMX polling target) ────────────────────────────────

@app.get("/api/status", response_class=HTMLResponse)
async def api_status():
    status = _task_state["status"]
    op = _task_state["operation"]
    lines = list(_task_log)

    dot_color = {"idle": "#6b7280", "running": "#facc15",
                 "done": "#4ade80", "error": "#f87171"}.get(status, "#6b7280")
    pulse = 'class="animate-pulse"' if status == "running" else ""

    def _line_class(line: str) -> str:
        if line.startswith("✓") or line.startswith("▶"):
            return "text-green-400"
        if line.startswith("✗"):
            return "text-red-400"
        if line.startswith("  ✓"):
            return "text-green-300"
        if line.startswith("  ✗"):
            return "text-red-300"
        return "text-gray-300"

    lines_html = "".join(
        f'<div class="{_line_class(l)} font-mono text-xs leading-5">{l}</div>'
        for l in lines
    ) if lines else '<div class="text-gray-600 font-mono text-xs">No activity yet — run an operation</div>'

    label = f"{status.upper()}" + (f" — {op}" if op and status != "idle" else "")

    return f"""
<div class="flex items-center gap-2 mb-3">
  <span {pulse} style="width:8px;height:8px;border-radius:50%;background:{dot_color};display:inline-block"></span>
  <span class="text-sm font-semibold" style="color:{dot_color}">{label}</span>
</div>
<div id="log-lines" class="overflow-y-auto max-h-96 space-y-0 pr-1">
  {lines_html}
</div>
"""


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
