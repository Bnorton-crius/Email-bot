import re
import smtplib
import time
import urllib.parse
from dataclasses import dataclass
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from rich.console import Console

console = Console()

# 1×1 transparent GIF for open tracking
_PIXEL_GIF = (
    b"\x47\x49\x46\x38\x39\x61\x01\x00\x01\x00\x80\x00\x00"
    b"\xff\xff\xff\x00\x00\x00\x21\xf9\x04\x00\x00\x00\x00\x00"
    b"\x2c\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02\x44\x01\x00\x3b"
)


@dataclass
class SmtpConfig:
    host: str
    port: int
    user: str
    password: str
    from_email: str
    from_name: str
    send_delay_seconds: int = 30


_URL_RE = re.compile(r"(https?://[^\s<>\"']+)")


def _build_html(body: str, token: str, tracking_url: str) -> str:
    """Convert plain-text body → HTML with tracked links and a pixel beacon.

    URLs are wrapped first (before HTML-escaping) so that any & in query
    strings is encoded correctly rather than mangled to &amp;.
    re.split with a capturing group alternates [non-url, url, non-url, ...].
    """
    parts = _URL_RE.split(body)
    html_parts: list[str] = []
    for i, part in enumerate(parts):
        if i % 2 == 0:
            # Plain text segment — escape for HTML
            html_parts.append(
                part.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            )
        else:
            # URL segment — wrap in tracking anchor
            encoded = urllib.parse.quote(part, safe="")
            href = f"{tracking_url}/track/click/{token}?url={encoded}"
            html_parts.append(f'<a href="{href}">{part}</a>')

    html_body = "".join(html_parts)

    # Convert newlines to <br>
    html_body = html_body.replace("\n", "<br>\n")

    pixel = (
        f'<img src="{tracking_url}/track/open/{token}" '
        f'width="1" height="1" style="display:none" alt="" />'
    )

    return (
        f"<!DOCTYPE html><html><body>"
        f"<p style='font-family:sans-serif;line-height:1.6'>"
        f"{html_body}"
        f"</p>{pixel}</body></html>"
    )


def send_email(
    draft,
    to_email: str,
    smtp_cfg: SmtpConfig,
    dry_run: bool = False,
    delay_seconds: int = 30,
    tracking_token: str | None = None,
    tracking_url: str | None = None,
) -> bool:
    """Send (or preview if dry_run) an email. Returns True on success."""
    if dry_run:
        console.print("\n[bold cyan]━━━  DRY RUN EMAIL  ━━━[/bold cyan]")
        console.print(f"[yellow]To:[/yellow]      {to_email or '(no address found)'}")
        console.print(f"[yellow]Subject:[/yellow] {draft.subject}")
        console.print(f"[yellow]Body:[/yellow]\n{draft.body}")
        if tracking_token:
            console.print(f"[dim]Tracking token: {tracking_token}[/dim]")
        console.print("[bold cyan]━━━━━━━━━━━━━━━━━━━━━━[/bold cyan]\n")
        return True

    if not to_email:
        console.print(
            f"[yellow]Skipping company_id={draft.company_id} — no email address found[/yellow]"
        )
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = draft.subject
        msg["From"] = f"{smtp_cfg.from_name} <{smtp_cfg.from_email}>"
        msg["To"] = to_email

        # Plain-text part always included
        msg.attach(MIMEText(draft.body, "plain"))

        # HTML part with tracking if token provided
        if tracking_token and tracking_url:
            html = _build_html(draft.body, tracking_token, tracking_url)
            msg.attach(MIMEText(html, "html"))

        with smtplib.SMTP(smtp_cfg.host, smtp_cfg.port, timeout=30) as server:
            server.ehlo()
            server.starttls()
            server.login(smtp_cfg.user, smtp_cfg.password)
            server.sendmail(smtp_cfg.from_email, to_email, msg.as_string())

        if delay_seconds > 0:
            time.sleep(delay_seconds)
        return True

    except Exception as exc:
        console.print(f"[red]Failed to send to {to_email}: {exc}[/red]")
        return False
