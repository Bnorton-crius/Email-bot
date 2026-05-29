#!/usr/bin/env python3
"""Email outreach bot CLI for web dev / AI agency."""

import argparse
import json
import sys

from dotenv import load_dotenv

load_dotenv()

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn
from rich.table import Table

from config import load_config
from database.tracker import (
    db_conn,
    get_all_companies,
    get_unscored,
    get_unsent,
    get_unsent_drafts,
    mark_sent,
    save_email_draft,
    update_score,
    upsert_company,
)
from email_bot.generator import generate_email
from email_bot.sender import SmtpConfig, send_email
from scraper.company_finder import find_companies
from scraper.http import make_session
from scraper.website_analyzer import ScoreResult, analyze_website, extract_email_from_site

console = Console()


# ── helpers ──────────────────────────────────────────────────────────────────

def _smtp_cfg_from(cfg) -> SmtpConfig:
    return SmtpConfig(
        host=cfg.smtp.host,
        port=cfg.smtp.port,
        user=cfg.smtp.user,
        password=cfg.smtp.password,
        from_email=cfg.smtp.from_email,
        from_name=cfg.smtp.from_name,
        send_delay_seconds=cfg.smtp.send_delay_seconds,
    )


def _score_color(score: int | None) -> str:
    if score is None:
        return "dim"
    if score >= 70:
        return "green"
    if score >= 40:
        return "yellow"
    return "red"


# ── commands ──────────────────────────────────────────────────────────────────

def cmd_find(args) -> None:
    cfg = load_config()
    console.print(
        Panel(
            f"[bold]Industry:[/bold] {args.industry}\n"
            f"[bold]Location:[/bold] {args.location}\n"
            f"[bold]Limit:[/bold]    {args.limit}",
            title="Finding Companies",
            border_style="blue",
        )
    )

    with Progress(SpinnerColumn(), TextColumn("{task.description}"), console=console) as p:
        task = p.add_task("Searching DuckDuckGo...", total=None)
        companies = find_companies(args.industry, args.location, args.limit)
        p.update(task, description=f"Found [bold]{len(companies)}[/bold] results")

    new_count = 0
    with db_conn(cfg.db_path) as conn:
        for company in companies:
            row_id = upsert_company(conn, company)
            if row_id is not None:
                new_count += 1

    console.print(
        f"[green]✓[/green] Added [bold]{new_count}[/bold] new companies "
        f"([dim]{len(companies) - new_count} duplicates skipped[/dim])"
    )


def cmd_analyze(args) -> None:
    cfg = load_config()
    session = make_session()

    with db_conn(cfg.db_path) as conn:
        companies = get_unscored(conn)

    if not companies:
        console.print("[yellow]No unscored companies — run 'find' first.[/yellow]")
        return

    console.print(f"[bold]Analyzing [cyan]{len(companies)}[/cyan] websites...[/bold]")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        console=console,
    ) as progress:
        task = progress.add_task("Starting...", total=len(companies))

        for company in companies:
            domain = company["domain"]
            progress.update(task, description=f"[cyan]{domain}[/cyan]")

            result = analyze_website(domain, session)

            email_addr = company.get("email")
            if not email_addr and result.status != "unreachable":
                email_addr = extract_email_from_site(domain, session)

            with db_conn(cfg.db_path) as conn:
                update_score(conn, company["id"], result)
                if email_addr and not company.get("email"):
                    conn.execute(
                        "UPDATE companies SET email = ? WHERE id = ?",
                        (email_addr, company["id"]),
                    )

            score_str = f"[{_score_color(result.total)}]{result.total}/100[/{_score_color(result.total)}]"
            progress.console.print(
                f"  {domain}: {score_str} [{result.status}]"
            )
            progress.advance(task)

    console.print("[green]✓ Analysis complete[/green]")


def cmd_generate(args) -> None:
    cfg = load_config()

    if not cfg.anthropic_api_key:
        console.print("[red]Error: ANTHROPIC_API_KEY is not set in your .env file.[/red]")
        sys.exit(1)

    import anthropic
    client = anthropic.Anthropic(api_key=cfg.anthropic_api_key)

    with db_conn(cfg.db_path) as conn:
        companies = get_unsent(conn, min_score=args.min_score, max_score=args.max_score)

    if not companies:
        console.print(
            f"[yellow]No companies found with score {args.min_score}–{args.max_score} "
            f"awaiting an email draft.[/yellow]"
        )
        return

    console.print(
        f"[bold]Generating emails for [cyan]{len(companies)}[/cyan] companies "
        f"(score {args.min_score}–{args.max_score})...[/bold]"
    )

    cached_total = 0

    with Progress(SpinnerColumn(), TextColumn("{task.description}"), console=console) as p:
        task = p.add_task("...", total=len(companies))

        for company in companies:
            p.update(task, description=f"Writing email for [cyan]{company['name']}[/cyan]")

            bd_raw = company.get("score_breakdown") or "{}"
            bd_data = json.loads(bd_raw)

            score_result = ScoreResult(
                domain=company["domain"],
                total=company.get("website_score") or 0,
                breakdown=bd_data.get("breakdown", {}),
                status=company.get("status", "ok"),
                response_time_ms=bd_data.get("response_time_ms", 0),
                notes=bd_data.get("notes", []),
            )

            try:
                draft = generate_email(company, score_result, client)
                cached_total += draft.cached_tokens
                with db_conn(cfg.db_path) as conn:
                    save_email_draft(conn, company["id"], draft.subject, draft.body)
                p.console.print(f"  [green]✓[/green] {company['name']}: \"{draft.subject}\"")
            except Exception as exc:
                p.console.print(f"  [red]✗[/red] {company['name']}: {exc}")

            p.advance(task)

    console.print(
        f"[green]✓ Done[/green]  (cached tokens saved: [bold]{cached_total}[/bold])"
    )


def cmd_send(args) -> None:
    cfg = load_config()
    smtp_cfg = _smtp_cfg_from(cfg)

    with db_conn(cfg.db_path) as conn:
        drafts = get_unsent_drafts(conn)

    if not drafts:
        console.print("[yellow]No pending email drafts to send — run 'generate' first.[/yellow]")
        return

    prefix = "[DRY RUN] " if args.dry_run else ""
    console.print(f"[bold]{prefix}Sending [cyan]{len(drafts)}[/cyan] emails...[/bold]")

    from email_bot.generator import EmailDraft

    sent_count = 0
    for row in drafts:
        to_email = row.get("email") or ""

        if not args.dry_run and not to_email:
            console.print(f"[yellow]  Skipping {row['name']} — no email address[/yellow]")
            continue

        draft = EmailDraft(
            subject=row["subject"],
            body=row["body"],
            company_id=row["company_id"],
        )
        success = send_email(
            draft,
            to_email=to_email or "(preview)",
            smtp_cfg=smtp_cfg,
            dry_run=args.dry_run,
            delay_seconds=smtp_cfg.send_delay_seconds if not args.dry_run else 0,
        )
        if success:
            if not args.dry_run:
                with db_conn(cfg.db_path) as conn:
                    mark_sent(conn, row["id"])
            sent_count += 1

    verb = "Previewed" if args.dry_run else "Sent"
    console.print(f"[green]✓ {verb} {sent_count}/{len(drafts)} emails[/green]")


def cmd_report(args) -> None:
    cfg = load_config()

    with db_conn(cfg.db_path) as conn:
        companies = get_all_companies(conn)

    if not companies:
        console.print("[yellow]No companies in database yet.[/yellow]")
        return

    table = Table(title="Company Report", box=box.ROUNDED, show_lines=False)
    table.add_column("ID",       style="dim",    width=4,  justify="right")
    table.add_column("Company",  style="cyan",   max_width=28)
    table.add_column("Domain",   style="blue",   max_width=26)
    table.add_column("Score",    justify="center", width=8)
    table.add_column("Status",   width=12)
    table.add_column("Email",    width=6, justify="center")
    table.add_column("Industry", max_width=14)

    status_colors = {
        "emailed":     "green",
        "analyzed":    "blue",
        "found":       "dim",
        "unreachable": "red",
        "ok":          "blue",
        "error":       "red",
    }

    for c in companies:
        score = c.get("website_score")
        sc = _score_color(score)
        score_str = f"[{sc}]{score}[/{sc}]" if score is not None else "[dim]—[/dim]"

        status = c.get("status", "found")
        sc2 = status_colors.get(status, "white")
        status_str = f"[{sc2}]{status}[/{sc2}]"

        has_email = "[green]✓[/green]" if c.get("email") else "[red]✗[/red]"

        table.add_row(
            str(c["id"]),
            (c.get("name") or "")[:28],
            (c.get("domain") or "")[:26],
            score_str,
            status_str,
            has_email,
            (c.get("industry") or "")[:14],
        )

    console.print(table)
    scored = sum(1 for c in companies if c.get("website_score") is not None)
    emailed = sum(1 for c in companies if c.get("status") == "emailed")
    console.print(
        f"\n[dim]Total: {len(companies)}  |  Scored: {scored}  |  Emailed: {emailed}[/dim]"
    )


def cmd_run(args) -> None:
    console.print(
        Panel(
            f"[bold]Industry:[/bold] {args.industry}\n"
            f"[bold]Location:[/bold] {args.location}\n"
            f"[bold]Limit:[/bold]    {args.limit}\n"
            f"[bold]Dry run:[/bold]  {args.dry_run}",
            title="[bold magenta]Full Pipeline[/bold magenta]",
            border_style="magenta",
        )
    )

    cmd_find(argparse.Namespace(
        industry=args.industry, location=args.location, limit=args.limit
    ))
    cmd_analyze(argparse.Namespace())
    cmd_generate(argparse.Namespace(min_score=0, max_score=args.max_score))
    cmd_send(argparse.Namespace(dry_run=args.dry_run))

    console.print(Panel("[bold green]Pipeline complete![/bold green]", border_style="green"))


# ── CLI definition ────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="email-bot",
        description="Automated outreach bot for web dev / AI agency",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python main.py find --industry restaurants --location 'Austin TX' --limit 20\n"
            "  python main.py analyze\n"
            "  python main.py generate --max-score 60\n"
            "  python main.py send --dry-run\n"
            "  python main.py report\n"
            "  python main.py run --industry restaurants --location 'Austin TX' --dry-run\n"
        ),
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    sub.required = True

    # find
    p = sub.add_parser("find", help="Discover companies via web search")
    p.add_argument("--industry", required=True, help='e.g. "restaurants"')
    p.add_argument("--location", required=True, help='e.g. "Austin TX"')
    p.add_argument("--limit", type=int, default=20, help="Max companies to find")
    p.set_defaults(func=cmd_find)

    # analyze
    p = sub.add_parser("analyze", help="Score websites for all unscored companies")
    p.set_defaults(func=cmd_analyze)

    # generate
    p = sub.add_parser("generate", help="Generate personalized email drafts via Claude")
    p.add_argument("--min-score", type=int, default=0, dest="min_score",
                   help="Minimum website score (default: 0)")
    p.add_argument("--max-score", type=int, default=70, dest="max_score",
                   help="Maximum website score — target companies below this (default: 70)")
    p.set_defaults(func=cmd_generate)

    # send
    p = sub.add_parser("send", help="Send (or preview) pending email drafts")
    p.add_argument("--dry-run", action="store_true",
                   help="Print emails to console instead of sending")
    p.set_defaults(func=cmd_send)

    # report
    p = sub.add_parser("report", help="Print a summary table of all tracked companies")
    p.set_defaults(func=cmd_report)

    # run  (full pipeline)
    p = sub.add_parser("run", help="Full pipeline: find → analyze → generate → send")
    p.add_argument("--industry", required=True)
    p.add_argument("--location", required=True)
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--max-score", type=int, default=70, dest="max_score")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_run)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
