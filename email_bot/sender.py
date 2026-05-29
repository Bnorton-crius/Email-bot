import smtplib
import time
from dataclasses import dataclass
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from rich.console import Console

console = Console()


@dataclass
class SmtpConfig:
    host: str
    port: int
    user: str
    password: str
    from_email: str
    from_name: str
    send_delay_seconds: int = 30


def send_email(
    draft,
    to_email: str,
    smtp_cfg: SmtpConfig,
    dry_run: bool = False,
    delay_seconds: int = 30,
) -> bool:
    """Send (or preview if dry_run) an email. Returns True on success."""
    if dry_run:
        console.print("\n[bold cyan]━━━  DRY RUN EMAIL  ━━━[/bold cyan]")
        console.print(f"[yellow]To:[/yellow]      {to_email or '(no address found)'}")
        console.print(f"[yellow]Subject:[/yellow] {draft.subject}")
        console.print(f"[yellow]Body:[/yellow]\n{draft.body}")
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

        msg.attach(MIMEText(draft.body, "plain"))

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
