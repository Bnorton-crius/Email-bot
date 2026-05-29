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

## Visual design assessment (apply when a screenshot is provided)

Before writing the email, assess the screenshot for these outdated design signals:

Era / layout red flags:
- Fixed-width layout that doesn't fill a modern screen
- Table-based or multi-column cluttered layout with no clear visual hierarchy
- Horizontal mega-navigation bars crammed with links
- Thin centered column of text surrounded by empty space (early 2000s hallmark)
- Frames, iframes as primary layout mechanism

Typography red flags:
- Very small body text (below 14px apparent size)
- ALL CAPS overuse, or Comic Sans / Times New Roman / Courier body text
- Justified text blocks with no breathing room
- Too many different font sizes and weights on one page

Color & imagery red flags:
- Heavy gradient backgrounds (especially blue-to-black, red-to-orange)
- Neon or high-contrast color schemes that are hard to read
- Pixel-art or 16×16 tiled background textures
- Stock photography that looks like it's from 2005 (low-res, obvious pose)
- Animated GIF banners or spinning text elements
- Low-contrast text-on-background (white text on light grey, etc.)

Interaction / trust red flags:
- No visible CTA button above the fold
- Contact information buried (or missing entirely) in a wall of text
- No social proof: no reviews, testimonials, logos, or trust badges visible
- Obvious "Under Construction" banners or placeholder content
- Flash-era animated menu effects or marquee text

Modern signals that are absent:
- No hero section with a clear value proposition
- No whitespace / breathing room between sections
- No rounded corners, card-based layout, or modern component patterns
- Site looks identical to how it would have looked in Internet Explorer 8

## Email rules

- Address the company by name
- Under 200 words total
- Mention 2–3 specific issues (mix technical issues from the audit data AND at least one \
concrete visual observation from the screenshot if provided — be specific, not vague)
- If the site looks dated (pre-2015 era), say so directly but tactfully, e.g. "your site has \
a design style that was common around 2010" — don't just say "outdated"
- If their site runs on a page builder (Wix, Squarespace, GoDaddy, etc.), mention that it limits \
performance, customisation, and SEO ceiling — and that we can migrate them to a custom solution
- Professional but conversational — not salesy or pushy
- End with a single, low-pressure CTA (e.g. "happy to chat for 15 minutes")
- No "I hope this email finds you well" or similar filler
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
