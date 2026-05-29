# Email Outreach Bot

Automated cold-outreach pipeline for a web development and AI agency. Finds companies, scores their websites, generates personalized emails with Claude, and sends them via SMTP.

## Features

- **Find** — discovers companies via DuckDuckGo search across any industry/location
- **Analyze** — scores each website out of 100 across 11 criteria (HTTPS, speed, mobile, SEO, accessibility, etc.)
- **Generate** — writes tailored cold-outreach emails using Claude AI based on each site's specific issues
- **Send** — dispatches emails via SMTP with configurable rate-limiting, or previews with `--dry-run`
- **Report** — rich terminal table showing all companies, scores, and statuses
- **Deduplication** — never emails the same domain twice

## Setup

```bash
# Install dependencies
pip install -r requirements.txt

# Configure credentials
cp .env.example .env
# Edit .env with your ANTHROPIC_API_KEY and SMTP settings
```

## Usage

```bash
# Find companies
python main.py find --industry "restaurants" --location "Austin TX" --limit 20

# Score their websites
python main.py analyze

# Generate personalized emails (for sites scoring below 70)
python main.py generate --max-score 70

# Preview emails without sending
python main.py send --dry-run

# Send for real
python main.py send

# View summary of all companies
python main.py report

# Run the full pipeline in one command
python main.py run --industry "dental clinics" --location "Denver CO" --limit 15 --dry-run
```

## Website Scoring (100 pts)

| Criterion | Points |
|---|---|
| HTTPS / valid SSL | 10 |
| Site loads without server errors | 15 |
| Response time < 2s | 12 |
| Title tag present | 5 |
| Meta description | 5 |
| Mobile viewport meta tag | 10 |
| Images have alt attributes | 5 |
| Favicon present | 5 |
| Semantic HTML / quality | 10 |
| Contact info visible | 13 |
| Social links + schema.org | 10 |

Companies scoring **below 70** are flagged as outreach targets by default (configurable with `--max-score`).

## Configuration (`.env`)

| Variable | Required | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | Claude API key |
| `SMTP_HOST` | Yes | e.g. `smtp.gmail.com` |
| `SMTP_PORT` | Yes | e.g. `587` |
| `SMTP_USER` | Yes | SMTP login username |
| `SMTP_PASSWORD` | Yes | SMTP login password / app password |
| `FROM_EMAIL` | Yes | Sender email address |
| `FROM_NAME` | Yes | Sender display name |
| `SEND_DELAY_SECONDS` | No | Delay between sends (default: 30) |
| `DB_PATH` | No | SQLite path (default: `data/companies.db`) |

## Data

All data is stored in `data/companies.db` (SQLite, git-ignored).

- `companies` table — company info, scores, status
- `emails_sent` table — generated/sent email history
