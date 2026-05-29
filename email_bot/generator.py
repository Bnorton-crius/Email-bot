import base64
import json
import os
from dataclasses import dataclass, field

import anthropic

from scraper.website_analyzer import ScoreResult

_SYSTEM_PROMPT = """You are an outreach specialist for a boutique web development and AI agency. \
Your job is to write concise, friendly cold outreach emails to small and medium businesses \
that might benefit from a modernized website or AI-powered tools.

Our agency builds:
- Fast, mobile-first websites with modern design
- AI chatbots and workflow automation
- E-commerce and booking integrations
- Custom web applications

Email rules:
- Address the company by name
- Under 200 words total
- Mention 2–3 specific issues you spotted on their site (be specific, not generic)
- If a screenshot is provided, reference one visual detail you actually observe
- If their site runs on a page builder (Wix, Squarespace, GoDaddy, etc.), mention that it limits \
performance, customisation, and SEO ceiling — and that we can migrate them to a custom solution
- Professional but conversational — not salesy or pushy
- End with a single, low-pressure CTA (e.g. "happy to chat for 15 minutes")
- No "I hope this email finds you well" or similar filler
- Sign off as "The [Agency Name] Team"

Return ONLY valid JSON with exactly two keys: "subject" and "body".
Do not wrap in markdown code fences."""


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
                {"type": "text", "text": user_text},
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
