import os
import time

import requests

from scraper.company_finder import normalize_domain, _is_skip_domain

# New Places API v1 — returns website in the same call, no separate Details request needed
_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
_FIELD_MASK = "places.id,places.displayName,places.websiteUri,nextPageToken"


def find_via_places(industry: str, location: str, limit: int, log=None) -> list[dict]:
    """Search Google Places API (v1) for businesses with a website.

    Returns a list of company dicts, or [] if no API key is set or an error occurs.
    """
    def _log(msg: str) -> None:
        if log:
            log(msg)

    api_key = os.environ.get("GOOGLE_PLACES_API_KEY", "")
    if not api_key:
        _log("  Google Places: GOOGLE_PLACES_API_KEY not set — skipping")
        return []

    query = f"{industry} in {location}"
    _log(f"  Google Places query: '{query}'")

    results: list[dict] = []
    seen: set[str] = set()
    page_token: str | None = None
    page = 0

    while len(results) < limit:
        page += 1
        payload: dict = {
            "textQuery": query,
            "maxResultCount": min(20, limit),  # API cap is 20 per page
        }
        if page_token:
            payload["pageToken"] = page_token

        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": _FIELD_MASK,
        }

        try:
            resp = requests.post(_SEARCH_URL, json=payload, headers=headers, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except requests.HTTPError as exc:
            body = exc.response.text[:200] if exc.response is not None else ""
            _log(f"  ✗ Google Places API error {exc.response.status_code}: {body}")
            break
        except Exception as exc:
            _log(f"  ✗ Google Places request failed: {exc}")
            break

        places = data.get("places", [])
        if not places:
            _log(f"  Google Places: no results on page {page}")
            break

        before = len(results)
        for place in places:
            if len(results) >= limit:
                break

            website = place.get("websiteUri", "")
            if not website:
                continue

            domain = normalize_domain(website)
            if not domain or _is_skip_domain(domain) or domain in seen:
                continue

            display = place.get("displayName", {})
            name = display.get("text", "Unknown") if isinstance(display, dict) else str(display)

            seen.add(domain)
            results.append({
                "name": name,
                "domain": domain,
                "industry": industry,
                "source_query": f"places:{query}",
            })

        added = len(results) - before
        _log(f"  Google Places page {page}: {len(places)} places → {added} new domain(s) (total: {len(results)})")

        page_token = data.get("nextPageToken")
        if not page_token or len(results) >= limit:
            break
        time.sleep(1)  # brief pause between pages

    return results[:limit]
