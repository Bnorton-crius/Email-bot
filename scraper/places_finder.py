import os
import time

try:
    import googlemaps
    _GMAPS_OK = True
except ImportError:
    _GMAPS_OK = False

from scraper.company_finder import normalize_domain, _is_skip_domain


def find_via_places(industry: str, location: str, limit: int) -> list[dict]:
    """Discover companies using the Google Places API.

    Returns a list of company dicts (name, domain, industry) or an empty list
    if the API key is absent, the library is not installed, or any error occurs.
    """
    api_key = os.environ.get("GOOGLE_PLACES_API_KEY", "")
    if not api_key or not _GMAPS_OK:
        return []

    try:
        gmaps = googlemaps.Client(key=api_key)
        query = f"{industry} in {location}"
        results: list[dict] = []
        seen: set[str] = set()

        response = gmaps.places(query=query)

        while True:
            for place in response.get("results", []):
                if len(results) >= limit:
                    break

                place_id = place.get("place_id")
                if not place_id:
                    continue

                # Fetch full details to get the website field
                details = gmaps.place(
                    place_id,
                    fields=["name", "website", "formatted_phone_number"],
                )
                info = details.get("result", {})
                website = info.get("website", "")
                if not website:
                    continue

                domain = normalize_domain(website)
                if not domain or _is_skip_domain(domain) or domain in seen:
                    continue
                seen.add(domain)

                results.append(
                    {
                        "name": info.get("name") or place.get("name", "Unknown"),
                        "domain": domain,
                        "industry": industry,
                        "source_query": f"places:{query}",
                    }
                )
                time.sleep(0.15)  # stay within rate limits

            if len(results) >= limit:
                break

            next_token = response.get("next_page_token")
            if not next_token:
                break
            time.sleep(2)  # required pause before using next_page_token
            response = gmaps.places(page_token=next_token)

        return results[:limit]

    except Exception:
        return []
