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
    # Irish political / news / government directories — we want personal sites, not listings
    "oireachtas.ie", "gov.ie", "rte.ie", "irishtimes.com", "independent.ie",
    "thejournal.ie", "breakingnews.ie", "irishexaminer.com", "politico.eu",
    "politics.ie", "electionsireland.org", "whogoesthere.ie", "merrionstreet.ie",
    "finegael.ie", "fiannafail.ie", "sinnfein.ie", "labour.ie", "greenparty.ie",
    "socialdemocrats.ie", "peoplebeforeprofit.ie", "aontu.ie",
}

# Keywords that indicate an Irish politician search
_POLITICIAN_KEYWORDS = {
    "politician", "politicians", "councillor", "councillors", "td", "tds",
    "teachta dála", "senator", "senators", "mep", "meps", "local representative",
    "local representatives", "seanad", "dáil", "oireachtas member",
}

# Irish political parties — used to build targeted search queries
_IRISH_PARTIES = [
    "Fianna Fáil", "Fine Gael", "Sinn Féin",
    "Labour", "Green Party", "Social Democrats",
    "People Before Profit", "Aontú", "Independent",
]


def _is_politician_search(industry: str) -> bool:
    return any(kw in industry.lower() for kw in _POLITICIAN_KEYWORDS)


def _politician_queries(location: str) -> list[str]:
    """Build DuckDuckGo queries targeted at Irish politicians' personal websites."""
    loc = location.strip()
    return [
        f'councillor "{loc}" Ireland personal website -site:oireachtas.ie',
        f'TD "{loc}" Ireland official personal website -site:oireachtas.ie',
        f'senator Ireland "{loc}" website -site:oireachtas.ie',
        f'"local councillor" "{loc}" site:ie',
        f'fianna fáil councillor "{loc}" website',
        f'fine gael TD "{loc}" website',
        f'sinn féin councillor "{loc}" personal site',
        f'labour green party councillor "{loc}" Ireland website',
        f'independent TD councillor "{loc}" Ireland official website',
    ]


def normalize_domain(raw: str) -> str:
    """Strip scheme, www., trailing slash, lowercase."""
    raw = raw.strip().lower()
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw
    parsed = urlparse(raw)
    domain = parsed.netloc or parsed.path
    domain = re.sub(r"^www\.", "", domain)
    domain = domain.split("/")[0]
    domain = domain.rstrip(".")
    return domain


def _is_skip_domain(domain: str) -> bool:
    return any(skip in domain for skip in _SKIP_DOMAINS)


def find_companies(industry: str, location: str, limit: int) -> list[dict]:
    """Find companies via Google Places (if configured) then DuckDuckGo."""
    results: list[dict] = []
    seen_domains: set[str] = set()

    # Try Google Places first — higher quality, structured data
    try:
        from scraper.places_finder import find_via_places
        places_results = find_via_places(industry, location, limit)
        for r in places_results:
            d = r["domain"]
            if d and not _is_skip_domain(d) and d not in seen_domains:
                seen_domains.add(d)
                results.append(r)
    except Exception:
        pass

    # Fill remaining slots with DuckDuckGo
    remaining = limit - len(results)
    if remaining <= 0:
        return results[:limit]

    queries = (
        _politician_queries(location)
        if _is_politician_search(industry)
        else [
            f"{industry} {location} official website",
            f"best {industry} in {location}",
            f"{industry} company {location} contact",
        ]
    )

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
