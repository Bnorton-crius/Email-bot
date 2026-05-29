import json
import os
import sqlite3
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
    last_emailed_at    TEXT
);

CREATE TABLE IF NOT EXISTS emails_sent (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id  INTEGER NOT NULL,
    subject     TEXT,
    body        TEXT,
    sent_at     TEXT,
    opened      INTEGER DEFAULT 0,
    replied     INTEGER DEFAULT 0,
    FOREIGN KEY (company_id) REFERENCES companies(id)
);
"""


@contextmanager
def db_conn(db_path: str = "data/companies.db") -> Generator[sqlite3.Connection, None, None]:
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_CREATE_TABLES)
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
        return None  # already known

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
           SET website_score    = ?,
               score_breakdown  = ?,
               status           = ?
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
            company_id,
        ),
    )


def save_email_draft(
    conn: sqlite3.Connection, company_id: int, subject: str, body: str
) -> int:
    cursor = conn.execute(
        "INSERT INTO emails_sent (company_id, subject, body) VALUES (?, ?, ?)",
        (company_id, subject, body),
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


def get_unscored(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM companies WHERE website_score IS NULL AND opt_out = 0"
    ).fetchall()
    return [dict(r) for r in rows]


def get_unsent(
    conn: sqlite3.Connection, min_score: int = 0, max_score: int = 70
) -> list[dict]:
    """Companies in the score range that have never been emailed."""
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


def get_unsent_drafts(conn: sqlite3.Connection) -> list[dict]:
    """Email drafts that have been generated but not yet sent."""
    rows = conn.execute(
        """SELECT e.id, e.company_id, e.subject, e.body,
                  c.name, c.domain, c.email
           FROM emails_sent e
           JOIN companies c ON c.id = e.company_id
           WHERE e.sent_at IS NULL
        """
    ).fetchall()
    return [dict(r) for r in rows]


def get_all_companies(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM companies ORDER BY CASE WHEN website_score IS NULL THEN 1 ELSE 0 END, website_score ASC"
    ).fetchall()
    return [dict(r) for r in rows]
