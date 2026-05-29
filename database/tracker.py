import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from typing import Generator

_CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS companies (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    name               TEXT    NOT NULL,
    domain             TEXT    NOT NULL,
    normalized_domain  TEXT    NOT NULL UNIQUE,
    email              TEXT,
    industry           TEXT,
    found_at           TEXT    DEFAULT (datetime('now')),
    website_score      INTEGER,
    score_breakdown    TEXT,
    status             TEXT    DEFAULT 'found',
    opt_out            INTEGER DEFAULT 0,
    last_emailed_at    TEXT,
    screenshot_path    TEXT,
    platform           TEXT
);

CREATE TABLE IF NOT EXISTS emails_sent (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id      INTEGER NOT NULL,
    subject         TEXT,
    body            TEXT,
    sent_at         TEXT,
    opened          INTEGER DEFAULT 0,
    replied         INTEGER DEFAULT 0,
    tracking_token  TEXT,
    click_count     INTEGER DEFAULT 0,
    opened_at       TEXT,
    approved        INTEGER DEFAULT 0,
    FOREIGN KEY (company_id) REFERENCES companies(id)
);
"""

# Migrations for existing DBs that predate new columns
_MIGRATIONS = [
    "ALTER TABLE companies ADD COLUMN screenshot_path TEXT",
    "ALTER TABLE companies ADD COLUMN platform TEXT",
    "ALTER TABLE emails_sent ADD COLUMN tracking_token TEXT",
    "ALTER TABLE emails_sent ADD COLUMN click_count INTEGER DEFAULT 0",
    "ALTER TABLE emails_sent ADD COLUMN opened_at TEXT",
    "ALTER TABLE emails_sent ADD COLUMN approved INTEGER DEFAULT 0",
]


def _run_migrations(conn: sqlite3.Connection) -> None:
    for stmt in _MIGRATIONS:
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError:
            pass  # column already exists


@contextmanager
def db_conn(db_path: str = "data/companies.db") -> Generator[sqlite3.Connection, None, None]:
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_CREATE_TABLES)
    _run_migrations(conn)
    conn.commit()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def upsert_company(conn: sqlite3.Connection, company: dict) -> int | None:
    """Insert a company by normalized domain. Returns row id, or None if duplicate."""
    normalized = company["domain"].lower().strip()
    existing = conn.execute(
        "SELECT id FROM companies WHERE normalized_domain = ?", (normalized,)
    ).fetchone()
    if existing:
        return None

    cursor = conn.execute(
        """INSERT INTO companies (name, domain, normalized_domain, email, industry)
           VALUES (?, ?, ?, ?, ?)""",
        (
            company.get("name", "Unknown"),
            company.get("domain", ""),
            normalized,
            company.get("email"),
            company.get("industry", ""),
        ),
    )
    return cursor.lastrowid


def update_score(conn: sqlite3.Connection, company_id: int, score_result) -> None:
    conn.execute(
        """UPDATE companies
           SET website_score   = ?,
               score_breakdown = ?,
               status          = ?,
               platform        = ?
           WHERE id = ?""",
        (
            score_result.total,
            json.dumps(
                {
                    "breakdown": score_result.breakdown,
                    "notes": score_result.notes,
                    "response_time_ms": score_result.response_time_ms,
                }
            ),
            score_result.status,
            getattr(score_result, "platform", "Unknown"),
            company_id,
        ),
    )


def update_screenshot(conn: sqlite3.Connection, company_id: int, path: str) -> None:
    conn.execute(
        "UPDATE companies SET screenshot_path = ? WHERE id = ?",
        (path, company_id),
    )


def save_email_draft(
    conn: sqlite3.Connection, company_id: int, subject: str, body: str
) -> int:
    token = str(uuid.uuid4())
    cursor = conn.execute(
        "INSERT INTO emails_sent (company_id, subject, body, tracking_token) VALUES (?, ?, ?, ?)",
        (company_id, subject, body, token),
    )
    return cursor.lastrowid


def mark_sent(conn: sqlite3.Connection, email_id: int) -> None:
    conn.execute(
        "UPDATE emails_sent SET sent_at = datetime('now') WHERE id = ?",
        (email_id,),
    )
    conn.execute(
        """UPDATE companies
           SET status = 'emailed', last_emailed_at = datetime('now')
           WHERE id = (SELECT company_id FROM emails_sent WHERE id = ?)""",
        (email_id,),
    )


def approve_draft(conn: sqlite3.Connection, email_id: int) -> None:
    conn.execute("UPDATE emails_sent SET approved = 1 WHERE id = ?", (email_id,))


def reject_draft(conn: sqlite3.Connection, email_id: int) -> None:
    conn.execute("DELETE FROM emails_sent WHERE id = ? AND sent_at IS NULL", (email_id,))


def track_open(conn: sqlite3.Connection, token: str) -> None:
    conn.execute(
        "UPDATE emails_sent SET opened = 1, opened_at = datetime('now') WHERE tracking_token = ?",
        (token,),
    )


def track_click(conn: sqlite3.Connection, token: str) -> None:
    conn.execute(
        "UPDATE emails_sent SET click_count = click_count + 1 WHERE tracking_token = ?",
        (token,),
    )


def get_unscored(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM companies WHERE website_score IS NULL AND opt_out = 0"
    ).fetchall()
    return [dict(r) for r in rows]


def get_unsent(
    conn: sqlite3.Connection, min_score: int = 0, max_score: int = 70
) -> list[dict]:
    rows = conn.execute(
        """SELECT c.*
           FROM companies c
           WHERE c.website_score BETWEEN ? AND ?
             AND c.opt_out  = 0
             AND c.status  != 'emailed'
             AND c.status  != 'unreachable'
             AND NOT EXISTS (
                 SELECT 1 FROM emails_sent e
                 WHERE e.company_id = c.id
             )
        """,
        (min_score, max_score),
    ).fetchall()
    return [dict(r) for r in rows]


def get_unsent_drafts(
    conn: sqlite3.Connection, approved_only: bool = False
) -> list[dict]:
    where = "e.sent_at IS NULL"
    if approved_only:
        where += " AND e.approved = 1"
    rows = conn.execute(
        f"""SELECT e.id, e.company_id, e.subject, e.body,
                  e.tracking_token, e.approved,
                  c.name, c.domain, c.email
           FROM emails_sent e
           JOIN companies c ON c.id = e.company_id
           WHERE {where}
        """
    ).fetchall()
    return [dict(r) for r in rows]


def get_all_drafts(conn: sqlite3.Connection) -> list[dict]:
    """All drafts (sent + unsent) with company info."""
    rows = conn.execute(
        """SELECT e.*, c.name, c.domain, c.email, c.website_score, c.platform
           FROM emails_sent e
           JOIN companies c ON c.id = e.company_id
           ORDER BY e.id DESC
        """
    ).fetchall()
    return [dict(r) for r in rows]


def get_recent_companies(conn: sqlite3.Connection, limit: int = 10) -> list[dict]:
    """Most recently added companies."""
    rows = conn.execute(
        "SELECT * FROM companies ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


def get_all_companies(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM companies ORDER BY CASE WHEN website_score IS NULL THEN 1 ELSE 0 END, website_score ASC"
    ).fetchall()
    return [dict(r) for r in rows]


def get_stats(conn: sqlite3.Connection) -> dict:
    total = conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
    scored = conn.execute("SELECT COUNT(*) FROM companies WHERE website_score IS NOT NULL").fetchone()[0]
    emailed = conn.execute("SELECT COUNT(*) FROM companies WHERE status = 'emailed'").fetchone()[0]
    pending_drafts = conn.execute("SELECT COUNT(*) FROM emails_sent WHERE sent_at IS NULL").fetchone()[0]
    avg_score_row = conn.execute(
        "SELECT AVG(website_score) FROM companies WHERE website_score IS NOT NULL"
    ).fetchone()
    avg_score = round(avg_score_row[0] or 0, 1)
    opens = conn.execute("SELECT COUNT(*) FROM emails_sent WHERE opened = 1").fetchone()[0]
    clicks = conn.execute("SELECT SUM(click_count) FROM emails_sent").fetchone()[0] or 0
    return {
        "total": total,
        "scored": scored,
        "emailed": emailed,
        "pending_drafts": pending_drafts,
        "avg_score": avg_score,
        "opens": opens,
        "clicks": clicks,
    }
