# Email Outreach Bot

Automated cold-outreach pipeline for a web development / AI agency. Finds companies, scores their websites, generates personalised emails with Claude AI, and sends them via SMTP — all controllable from a web dashboard or the command line.

---

## How It Works

```
Find Companies → Analyse Websites → Generate Emails → Send
     ↓                  ↓                 ↓              ↓
 DuckDuckGo /      Score out of       Claude AI       SMTP with
 Google Places     100, screenshot,   writes email    open & click
                   detect platform,   per company     tracking
                   find email addr
```

1. **Find** — searches DuckDuckGo (and Google Places if configured) for businesses matching your industry and location. Deduplicates by domain so the same company is never targeted twice.
2. **Analyse** — visits each website, scores it out of 100 across 11 criteria, detects the platform (WordPress, Wix, Squarespace, etc.), extracts any contact email from the site, and takes a screenshot.
3. **Generate** — sends the score, issues, platform, and screenshot to Claude (claude-sonnet-4-6). Claude writes a short, warm, plain-English outreach email specific to that site's problems.
4. **Send** — dispatches emails via SMTP with configurable rate-limiting and open/click tracking pixels embedded automatically.

---

## Requirements

- Python 3.10+
- An [Anthropic API key](https://console.anthropic.com/) (for email generation)
- SMTP credentials (Gmail, Fastmail, SendGrid, etc.)

---

## Installation

```bash
# 1. Clone the repo
git clone <repo-url>
cd Email-bot

# 2. Install Python dependencies
pip install -r requirements.txt

# 3. Install Playwright browsers (for screenshots — optional but recommended)
playwright install chromium

# 4. Copy and fill in credentials
cp .env.example .env
```

Edit `.env` with your real values (see Configuration section below).

---

## Configuration

Create a `.env` file in the project root. All keys:

```env
# Required
ANTHROPIC_API_KEY=sk-ant-...          # Claude API key

# SMTP (required to send emails)
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=you@gmail.com
SMTP_PASSWORD=your-app-password       # Gmail: use an App Password, not your account password
FROM_EMAIL=you@gmail.com
FROM_NAME=Your Agency Name

# Optional
SEND_DELAY_SECONDS=30                 # Delay between each email send (default: 30)
DB_PATH=data/companies.db             # SQLite database location (default: data/companies.db)
GOOGLE_PLACES_API_KEY=AIza...         # Google Places API key — improves company discovery
TRACKING_URL=https://yourdomain.com   # Public URL of this server, used for open/click tracking pixels
```

### Gmail App Password

If using Gmail, you must generate an **App Password** (your account password will not work with SMTP):
1. Go to Google Account → Security → 2-Step Verification → App passwords
2. Create a password for "Mail"
3. Paste that 16-character password as `SMTP_PASSWORD`

---

## Option A — Web Dashboard (Recommended)

Start the dashboard and do everything from your browser:

```bash
python main.py dashboard --port 8000
```

Then open **http://localhost:8000**

### Dashboard Pages

| Page | URL | What it does |
|---|---|---|
| Overview | `/` | Stats summary and recently added companies |
| Companies | `/companies` | Full paginated table with scores, platforms, screenshots |
| Emails | `/emails` | Review drafts, approve/reject, send emails |
| Pipeline | `/pipeline` | Run any operation — find, analyse, generate, send, or full pipeline |

### Running Operations from the Dashboard

Go to the **Pipeline** page (`/pipeline`). You'll see five sections:

1. **Find Companies** — enter an industry and location, set a limit, click Find
2. **Analyse Websites** — click once to score all unscored companies
3. **Generate Emails** — set a max score threshold and generate drafts via Claude
4. **Send Emails** — choose a send mode (see below)
5. **Full Pipeline** — runs all four steps automatically in sequence

A live log panel on the right streams output in real time as each operation runs.

### Send Modes

The **Emails** page and **Pipeline → Send Emails** section both offer two modes:

| Mode | What it does |
|---|---|
| **Manual Mode** — Send Approved | You review each draft on the Emails page, click Approve, then send only approved ones |
| **Auto-Send Mode** — Send All Directly | Skips the approval step — sends every pending draft immediately |

Both modes have a **Dry Run** button that previews what would be sent without dispatching anything.

---

## Option B — Command Line

All features are also available as CLI subcommands.

### Step-by-step

```bash
# 1. Find companies (saves to database)
python main.py find --industry "dental clinics" --location "Dublin" --limit 20

# 2. Score websites, find emails, take screenshots
python main.py analyze

# 3. Generate personalised emails for sites scoring under 70
python main.py generate --max-score 70

# 4. Preview emails without sending
python main.py send --dry-run

# 5. Send for real (only approved drafts)
python main.py send --approved-only

# 6. Send all pending drafts without approval step
python main.py send

# 7. View a summary table of everything
python main.py report
```

### Full Pipeline (one command)

```bash
# Find, analyse, generate, and send in one go
python main.py run --industry "restaurants" --location "Cork" --limit 15 --dry-run

# Live send (remove --dry-run)
python main.py run --industry "plumbers" --location "Galway" --limit 20
```

### All CLI Options

```
python main.py find      --industry TEXT  --location TEXT  [--limit INT]
python main.py analyze
python main.py generate  [--min-score INT]  [--max-score INT]
python main.py send      [--dry-run]  [--approved-only]
python main.py report
python main.py dashboard [--port INT]
python main.py run       --industry TEXT  --location TEXT  [--limit INT]  [--max-score INT]  [--dry-run]
```

---

## Targeting Irish Politicians

The bot has built-in support for targeting Irish politicians (TDs, councillors, senators, MEPs). If you use the word "councillor", "TD", "politician", "senator", etc. as the industry, it automatically switches to a set of specialised search queries that find personal campaign websites rather than party HQ sites.

It also uses politician-specific email copy: constituent connection, phone-friendly sites, AI query tools for staff — not business growth language.

```bash
python main.py find --industry "councillors" --location "Dublin" --limit 20
```

---

## Website Scoring (100 points)

| Criterion | Points |
|---|---|
| HTTPS / valid SSL | 10 |
| Site loads (no server error) | 15 |
| Response time under 2 seconds | 12 |
| Page title present | 5 |
| Meta description present | 5 |
| Mobile-friendly viewport | 10 |
| Images have alt text | 5 |
| Favicon present | 5 |
| Semantic HTML quality | 10 |
| Contact information visible | 13 |
| Social links present | 10 |

Sites scoring **below 70** are outreach targets by default. Adjust with `--max-score`.

---

## Email Tracking

When `TRACKING_URL` is set to a publicly accessible URL (e.g. via ngrok or a VPS), every sent email includes:

- A **1×1 invisible pixel** that fires when the email is opened
- **Link wrapping** — any URLs in the email body become tracked redirect links

Opens and clicks are recorded in the database and shown on the Emails page.

---

## Project Structure

```
Email-bot/
├── main.py                    # CLI entry point
├── config.py                  # Loads .env into Config dataclass
├── requirements.txt
├── .env.example
│
├── scraper/
│   ├── company_finder.py      # DuckDuckGo + Google Places search
│   ├── website_analyzer.py    # Scores websites, detects platform, finds emails
│   ├── screenshot.py          # Playwright screenshot capture
│   ├── places_finder.py       # Google Places API integration
│   └── http.py                # Shared requests session
│
├── email_bot/
│   ├── generator.py           # Claude API — generates email drafts
│   └── sender.py              # SMTP send + tracking pixel injection
│
├── database/
│   └── tracker.py             # SQLite layer (companies + emails_sent tables)
│
├── dashboard/
│   ├── app.py                 # FastAPI app + background task runner
│   └── templates/
│       ├── base.html          # Sidebar layout
│       ├── index.html         # Overview page
│       ├── companies.html     # Companies table
│       ├── emails.html        # Draft review + send controls
│       └── pipeline.html      # Pipeline operations + live log
│
└── data/
    ├── companies.db           # SQLite database (auto-created)
    └── screenshots/           # Website screenshots (auto-created)
```

---

## Data Storage

Everything is stored locally in `data/companies.db` (SQLite). Two tables:

**`companies`** — one row per domain

| Column | Description |
|---|---|
| `domain` | Normalised domain (unique — prevents duplicates) |
| `name` | Business name from search result |
| `email` | Contact email found on their site |
| `website_score` | Score out of 100 |
| `platform` | Detected platform (WordPress, Wix, etc.) |
| `screenshot_path` | Path to PNG screenshot |
| `status` | `found` → `analyzed` → `emailed` |

**`emails_sent`** — one row per draft

| Column | Description |
|---|---|
| `subject` / `body` | Generated email content |
| `sent_at` | Timestamp when sent (NULL = pending) |
| `approved` | 1 if approved in dashboard |
| `opened` / `opened_at` | Tracking pixel fired |
| `click_count` | Number of link clicks tracked |
| `tracking_token` | UUID for open/click tracking |
