import base64
import json
import os
from dataclasses import dataclass, field

import anthropic

from scraper.website_analyzer import ScoreResult

_SYSTEM_PROMPT = """You are an outreach specialist for a web development and AI agency. \
You write short, warm, human emails to potential clients — never sounding like a sales robot \
or a technical manual.

Our agency builds:
- Clean, modern websites that work perfectly on phones
- AI tools that answer customer or constituent queries automatically
- Booking and contact systems that save time
- Custom web applications

## Audience context

If the target is a politician (TD, councillor, senator, MEP, local representative):
- The pitch is about connecting with constituents, not "conversion funnels"
- Voters browse on their phones — mobile matters more than anything
- An AI-powered contact/query tool means staff spend less time answering the same questions
- A clean, trustworthy-looking site builds credibility before an election
- CTA example: "happy to show you what a modern politician's site looks like"
- Do NOT mention e-commerce, bookings, or business growth metrics

## Visual design assessment (apply when a screenshot is provided)

Before writing the email, look at the screenshot for:
- Does it feel modern or like something from 10–15 years ago?
- Is the layout cramped, cluttered, or hard to navigate?
- Does it look good on a phone, or only on a desktop?
- Are there obvious design quirks: garish colours, tiny text, no clear message at the top?
- Is it immediately obvious what the person or business does?

## Language rules — STRICT

Write the email the way you would talk to a neighbour, not a developer.

NEVER use any of these words or phrases:
meta description, viewport, schema.org, Open Graph, HTML, CSS, SEO, canonical, sitemap,
robots.txt, HTTPS, SSL certificate, semantic, alt text, favicon, API, JavaScript,
responsive, frontend, backend, CTA, UX, UI, page speed score, lighthouse.

Translate technical issues into plain human language before mentioning them:
- "No HTTPS/SSL" → "browsers flag it as 'not secure' — visitors see a warning"
- "Slow response time" → "takes a long time to load, especially on mobile"
- "No mobile viewport" → "hard to read on a phone without zooming"
- "Missing meta description" → "doesn't show a useful preview when it appears in Google"
- "No social links" → "no easy way for people to follow along"
- "Missing contact info" → "hard to find how to get in touch"
- "No favicon" → omit entirely — too minor to mention
- "Open Graph / schema.org" → omit entirely — too technical
- "Missing alt text" → omit entirely — too technical for a first email
- "No semantic HTML" → omit entirely
- Platform (Wix, Squarespace, etc.) → say "built on [Platform], which has real limits \
on how fast and how customised it can get" — keep it simple

## Email rules

- Address them by name (first name if it looks like a person's name, otherwise company name)
- Under 180 words
- Warm, direct, conversational — like a note from someone who genuinely noticed something
- Mention 2–3 issues but phrase them as missed opportunities, not criticism
- If a screenshot is provided, include one concrete visual detail you actually observed
- If the site looks dated, say something like "your site has a design style that was \
common about 10 years ago" — not "outdated" (too blunt) and not "your meta tags" (too technical)
- End with one low-pressure question or offer, not a hard close
- No "I hope this email finds you well", "I came across your website", or similar filler openers
- Sign off as "The [Agency Name] Team"

Return ONLY valid JSON with exactly two keys: "subject" and "body".
Do not wrap in markdown code fences."""

# Visual assessment task prepended to the user message when a screenshot is available
_VISUAL_TASK = """\
Look at this screenshot carefully before writing anything. Assess:
1. Design era — does it look modern (2020s), aging (2015–2019), or clearly dated (pre-2015)?
2. Pick the single most visually striking problem you can see (layout, color, typography, imagery).
3. Is there a clear call-to-action visible above the fold?

Use your visual assessment to write a more specific and compelling outreach email.

"""


@dataclass
class EmailDraft:
    subject: str
    body: str
    company_id: int
    prompt_tokens: int = 0
    cached_tokens: int = 0


def generate_email(
    company: dict,
    score_result: ScoreResult,
    client: anthropic.Anthropic | None = None,
    screenshot_path: str | None = None,
) -> EmailDraft:
    if client is None:
        client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))

    issues = score_result.notes[:3] if score_result.notes else ["outdated overall design"]
    issues_text = "\n".join(f"- {issue}" for issue in issues)

    platform = company.get("platform") or getattr(score_result, "platform", "Unknown")
    platform_line = (
        f"Detected platform: {platform} (mention this in the email)\n"
        if platform and platform != "Unknown"
        else ""
    )

    user_text = (
        f"Write a cold outreach email for this company:\n\n"
        f"Company name: {company.get('name', 'the company')}\n"
        f"Website: {company.get('domain', '')}\n"
        f"Industry: {company.get('industry', 'business')}\n"
        f"Website quality score: {score_result.total}/100\n"
        f"{platform_line}"
        f"\nSpecific issues found on their current website:\n{issues_text}\n\n"
        f"Return JSON only."
    )

    # Build user message — include screenshot image block if available
    user_content: list[dict] | str
    if screenshot_path and os.path.isfile(screenshot_path):
        try:
            with open(screenshot_path, "rb") as f:
                img_b64 = base64.standard_b64encode(f.read()).decode("utf-8")
            user_content = [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": img_b64,
                    },
                },
                # Prepend visual assessment task so Claude looks at the image
                # before writing — this surfaces outdated design patterns specifically
                {"type": "text", "text": _VISUAL_TASK + user_text},
            ]
        except Exception:
            user_content = user_text
    else:
        user_content = user_text

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=600,
        system=[
            {
                "type": "text",
                "text": _SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_content}],
    )

    raw = response.content[0].text.strip()

    # Strip accidental markdown fences
    if raw.startswith("```"):
        raw = raw.lstrip("`")
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.rstrip("`").strip()

    data = json.loads(raw)

    usage = response.usage
    cached = getattr(usage, "cache_read_input_tokens", 0) or 0

    return EmailDraft(
        subject=data["subject"],
        body=data["body"],
        company_id=company["id"],
        prompt_tokens=usage.input_tokens,
        cached_tokens=cached,
    )
