import re
import time
from dataclasses import dataclass, field
from urllib.parse import urljoin

import requests
import urllib3
from bs4 import BeautifulSoup

from scraper.http import make_session

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Scoring weights (total = 100)
_WEIGHTS = {
    "https_ssl": 10,
    "loads_ok": 15,
    "response_time": 12,
    "title_tag": 5,
    "meta_description": 5,
    "mobile_viewport": 10,
    "img_alt": 5,
    "favicon": 5,
    "html_quality": 10,
    "contact_info": 13,
    "modern_signals": 10,
}

# Platform fingerprints: (platform_name, [html_signals], [header_prefixes])
_PLATFORMS = [
    ("Wix",         ["static.wixstatic.com", "_wixCIDX", "wix.com/lpviral"],    ["x-wix-"]),
    ("Squarespace", ["static1.squarespace.com", "squarespace.com/static"],       []),
    ("Webflow",     ["webflow.io", ".webflow.com", "webflow.js"],                []),
    ("Shopify",     ["cdn.shopify.com", "myshopify.com"],                        []),
    ("GoDaddy",     ["secureserver.net", "godaddysites.com"],                    []),
    ("WordPress",   ["wp-content/", "wp-includes/"],                             []),
]


@dataclass
class ScoreResult:
    domain: str
    total: int
    breakdown: dict
    status: str  # "ok" | "unreachable" | "error"
    response_time_ms: int
    notes: list = field(default_factory=list)
    platform: str = "Unknown"


def detect_platform(resp: requests.Response, html: str) -> str:
    headers_lower = {k.lower(): v for k, v in resp.headers.items()}
    for name, html_signals, header_prefixes in _PLATFORMS:
        if any(h.startswith(prefix) for prefix in header_prefixes for h in headers_lower):
            return name
        if any(sig in html for sig in html_signals):
            return name
    return "Unknown"


def analyze_website(domain: str, session: requests.Session | None = None) -> ScoreResult:
    if session is None:
        session = make_session()

    ssl_valid = False
    resp = None
    response_time_ms = 0

    for scheme in ("https://", "http://"):
        url = f"{scheme}{domain}"
        try:
            t0 = time.time()
            try:
                resp = session.get(url, timeout=10, allow_redirects=True, verify=True)
                ssl_valid = scheme == "https://"
            except requests.exceptions.SSLError:
                resp = session.get(url, timeout=10, allow_redirects=True, verify=False)
                ssl_valid = False
            response_time_ms = int((time.time() - t0) * 1000)

            if resp.status_code < 500:
                break
        except (
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
            requests.exceptions.TooManyRedirects,
        ):
            resp = None
            continue
        except Exception:
            resp = None
            continue

    if resp is None:
        return ScoreResult(
            domain=domain,
            total=0,
            breakdown={k: 0 for k in _WEIGHTS},
            status="unreachable",
            response_time_ms=0,
            notes=["Site is unreachable or timed out"],
            platform="Unknown",
        )

    return _score_response(domain, resp, ssl_valid, response_time_ms, session)


def _score_response(
    domain: str,
    resp: requests.Response,
    ssl_valid: bool,
    response_time_ms: int,
    session: requests.Session | None = None,
) -> ScoreResult:
    breakdown: dict[str, int] = {}
    notes: list[str] = []

    try:
        soup = BeautifulSoup(resp.text, "lxml")
    except Exception:
        soup = BeautifulSoup(resp.text, "html.parser")

    final_url = resp.url
    platform = detect_platform(resp, resp.text)

    # 1. HTTPS / SSL (10 pts)
    if ssl_valid and final_url.startswith("https://"):
        breakdown["https_ssl"] = 10
    else:
        breakdown["https_ssl"] = 0
        notes.append("No valid HTTPS/SSL — site is not secure")

    # 2. Loads without server errors (15 pts)
    if resp.status_code < 400:
        breakdown["loads_ok"] = 15
    elif resp.status_code < 500:
        breakdown["loads_ok"] = 5
        notes.append(f"Homepage returns {resp.status_code} client error")
    else:
        breakdown["loads_ok"] = 0
        notes.append(f"Homepage returns server error {resp.status_code}")

    # 3. Response time (12 pts)
    if response_time_ms < 2000:
        breakdown["response_time"] = 12
    elif response_time_ms < 3500:
        breakdown["response_time"] = 8
        notes.append(f"Slow load time ({response_time_ms}ms) — visitors may bounce")
    elif response_time_ms < 6000:
        breakdown["response_time"] = 4
        notes.append(f"Very slow load time ({response_time_ms}ms) — hurts SEO and conversions")
    else:
        breakdown["response_time"] = 0
        notes.append(f"Extremely slow load time ({response_time_ms}ms)")

    # 4. Title tag (5 pts)
    title_tag = soup.find("title")
    if title_tag and title_tag.get_text(strip=True):
        breakdown["title_tag"] = 5
    else:
        breakdown["title_tag"] = 0
        notes.append("Missing page title tag — hurts SEO")

    # 5. Meta description (5 pts)
    meta_desc = soup.find("meta", attrs={"name": re.compile("^description$", re.I)})
    if meta_desc and meta_desc.get("content", "").strip():
        breakdown["meta_description"] = 5
    else:
        breakdown["meta_description"] = 0
        notes.append("Missing meta description — hurts search engine click-through rates")

    # 6. Mobile viewport (10 pts)
    viewport = soup.find("meta", attrs={"name": re.compile("^viewport$", re.I)})
    if viewport:
        breakdown["mobile_viewport"] = 10
    else:
        breakdown["mobile_viewport"] = 0
        notes.append("Missing mobile viewport — site is not mobile-friendly")

    # 7. Images with alt attributes (5 pts)
    images = soup.find_all("img")
    if not images:
        breakdown["img_alt"] = 5
    else:
        imgs_with_alt = [img for img in images if img.get("alt") is not None]
        ratio = len(imgs_with_alt) / len(images)
        if ratio >= 0.8:
            breakdown["img_alt"] = 5
        elif ratio >= 0.5:
            breakdown["img_alt"] = 2
            notes.append(f"~{int((1 - ratio) * 100)}% of images are missing alt text (accessibility issue)")
        else:
            breakdown["img_alt"] = 0
            notes.append("Most images missing alt text — accessibility and SEO problem")

    # 8. Favicon (5 pts)
    # BS4 passes the joined rel string to the lambda, not a list — use CSS selector instead
    favicon_link = soup.select_one('link[rel*="icon"]')
    if favicon_link:
        breakdown["favicon"] = 5
    else:
        _fav_session = session if session is not None else make_session()
        try:
            fav_url = urljoin(final_url, "/favicon.ico")
            fav_resp = _fav_session.get(fav_url, timeout=5, verify=False)
            breakdown["favicon"] = 5 if fav_resp.status_code == 200 and len(fav_resp.content) > 0 else 0
        except Exception:
            breakdown["favicon"] = 0
        if breakdown["favicon"] == 0:
            notes.append("No favicon — affects brand professionalism")

    # 9. HTML quality / structure (10 pts)
    html_score = 0
    html_tag = soup.find("html")
    if html_tag and html_tag.get("lang"):
        html_score += 3
    else:
        notes.append("Missing lang attribute on <html> — accessibility issue")

    semantic_tags = ["header", "main", "nav", "footer", "article", "section"]
    found_semantic = [t for t in semantic_tags if soup.find(t)]
    if len(found_semantic) >= 3:
        html_score += 4
    elif len(found_semantic) >= 1:
        html_score += 2
    else:
        notes.append("No semantic HTML5 elements — outdated site structure")

    has_doctype = resp.text.strip().lower().startswith("<!doctype")
    if has_doctype:
        html_score += 3

    breakdown["html_quality"] = html_score

    # 10. Contact info (13 pts)
    page_text = soup.get_text(" ", strip=True)
    has_phone = bool(re.search(r"\(?\d{3}\)?[\s.\-]\d{3}[\s.\-]\d{4}", page_text))
    has_email_addr = bool(
        re.search(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", page_text)
    )
    has_contact_link = bool(
        soup.find("a", string=re.compile(r"contact|reach|get in touch", re.I))
        or soup.find("a", href=re.compile(r"/contact|/reach|/about", re.I))
    )
    has_address = bool(
        re.search(
            r"\b\d{1,5}\s+\w+\s+(st|street|ave|avenue|blvd|rd|road|dr|drive|ln|lane)\b",
            page_text,
            re.I,
        )
    )

    contact_score = 0
    if has_phone:
        contact_score += 4
    if has_email_addr:
        contact_score += 4
    if has_contact_link:
        contact_score += 3
    if has_address:
        contact_score += 2
    breakdown["contact_info"] = min(contact_score, 13)
    if breakdown["contact_info"] < 6:
        notes.append("Hard to find contact information on the homepage")

    # 11. Modern signals: social links, schema.org, OG tags (10 pts)
    social_domains = ["facebook.com", "twitter.com", "x.com", "instagram.com", "linkedin.com", "youtube.com"]
    all_hrefs = [a.get("href", "") for a in soup.find_all("a", href=True)]
    has_social = any(any(sd in href for sd in social_domains) for href in all_hrefs)
    has_schema = (
        bool(soup.find(attrs={"itemscope": True}))
        or '"@context"' in resp.text
        or "schema.org" in resp.text
    )
    has_og = bool(soup.find("meta", property=re.compile("^og:")))

    modern_score = 0
    if has_social:
        modern_score += 4
    else:
        notes.append("No social media links found")
    if has_schema:
        modern_score += 3
    if has_og:
        modern_score += 3
    else:
        notes.append("No Open Graph tags — poor social media sharing appearance")
    breakdown["modern_signals"] = modern_score

    # Platform note
    if platform not in ("Unknown",):
        notes.append(f"Site built on {platform} — limited customisation and performance ceiling")

    total = sum(breakdown.values())
    return ScoreResult(
        domain=domain,
        total=min(total, 100),
        breakdown=breakdown,
        status="ok",
        response_time_ms=response_time_ms,
        notes=notes,
        platform=platform,
    )


def extract_email_from_site(domain: str, session: requests.Session | None = None) -> str | None:
    """Scrape contact/about pages to find a contact email address."""
    if session is None:
        session = make_session()

    _skip_email_domains = {
        "example.com", "sentry.io", "wixpress.com", "shopify.com",
        "squarespace.com", "wordpress.com", "google.com", "w3.org",
    }

    for scheme in ("https://", "http://"):
        for path in ("/contact", "/contact-us", "/about", "/about-us", ""):
            url = f"{scheme}{domain}{path}"
            try:
                r = session.get(url, timeout=8, allow_redirects=True, verify=False)
                if r.status_code != 200:
                    continue
                emails = re.findall(
                    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
                    r.text,
                )
                for email in emails:
                    email_lower = email.lower()
                    if any(s in email_lower for s in _skip_email_domains):
                        continue
                    if email_lower.endswith((".png", ".jpg", ".svg", ".gif")):
                        continue
                    return email
            except Exception:
                continue
    return None
