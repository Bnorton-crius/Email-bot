import os
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
    _PLAYWRIGHT_OK = True
except ImportError:
    _PLAYWRIGHT_OK = False


def take_screenshot(domain: str, output_dir: str = "data/screenshots") -> str | None:
    """Capture a viewport screenshot of the domain homepage.

    Returns the saved PNG path, or None if playwright is unavailable or the
    site cannot be reached.
    """
    if not _PLAYWRIGHT_OK:
        return None

    Path(output_dir).mkdir(parents=True, exist_ok=True)
    safe_name = domain.replace("/", "_").replace(":", "_").replace("*", "_")
    out_path = os.path.join(output_dir, f"{safe_name}.png")

    for scheme in ("https://", "http://"):
        url = f"{scheme}{domain}"
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
                ctx = browser.new_context(
                    viewport={"width": 1280, "height": 800},
                    ignore_https_errors=True,
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                )
                page = ctx.new_page()
                page.goto(url, timeout=15_000, wait_until="domcontentloaded")
                page.wait_for_timeout(1500)
                page.screenshot(path=out_path, full_page=False)
                browser.close()
            return out_path
        except Exception:
            continue

    return None
