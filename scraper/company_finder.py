import re
import time
from urllib.parse import urlparse

from duckduckgo_search import DDGS

_SKIP_DOMAINS = {
    "yelp.com", "google.com", "facebook.com", "linkedin.com",
    "instagram.com", "twitter.com", "youtube.com", "wikipedia.org",
    "reddit.com", "tripadvisor.com", "bbb.org", "yellowpages.com",
    "angieslist.com", "thumbtack.com", "houzz.com", "nextdoor.com",
    "angi.com", "homeadvisor.com", "citysearch.com", "mapquest.com",
    "apple.com", "amazon.com", "bing.com", "yahoo.com",
}


def normalize_domain(raw: str) -> str:
    """Strip scheme, www., trailing slash, lowercase."""
    raw = raw.strip().lower()
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw
    parsed = urlparse(raw)
    domain = parsed.netloc or parsed.path
    domain = re.sub(r"^www\.", "", domain)
    domain = domain.split("/")[0]  # strip any path
    domain = domain.rstrip(".")
    return domain


def _is_skip_domain(domain: str) -> bool:
    return any(skip in domain for skip in _SKIP_DOMAINS)


def find_companies(industry: str, location: str, limit: int) -> list[dict]:
    """Search DuckDuckGo for companies in a given industry and location."""
    results: list[dict] = []
    seen_domains: set[str] = set()

    queries = [
        f"{industry} {location} official website",
        f"best {industry} in {location}",
        f"{industry} company {location} contact",
    ]

    with DDGS() as ddgs:
        for query in queries:
            if len(results) >= limit:
                break
            try:
                for hit in ddgs.text(query, max_results=25):
                    if len(results) >= limit:
                        break
                    url = hit.get("href", "")
                    title = hit.get("title", "Unknown")
                    if not url:
                        continue

                    domain = normalize_domain(url)
                    if not domain or _is_skip_domain(domain):
                        continue
                    if domain in seen_domains:
                        continue

                    seen_domains.add(domain)
                    # Clean up the title – strip " - Company | Page" suffixes
                    name = re.split(r"\s[-|–]\s", title)[0].strip()[:100]
                    results.append(
                        {
                            "name": name,
                            "domain": domain,
                            "industry": industry,
                            "source_query": query,
                        }
                    )
                time.sleep(1.5)
            except Exception:
                time.sleep(3)
                continue

    return results[:limit]
