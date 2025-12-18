import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

URLS_FILE = "urls.txt"
OUT_DIR = "screenshots"

# Use NY timezone for timestamping filenames (optional but nice)
NY_TZ = ZoneInfo("America/New_York")

# Fixed clip region at top of page (no scrolling).
# This should capture Tomatometer + review count for most RT pages on desktop viewport.
VIEWPORT = {"width": 1440, "height": 900}
CLIP = {"x": 0, "y": 0, "width": 1440, "height": 750}  # adjust height if needed

def slugify(url: str) -> str:
    slug = url.rstrip("/").split("/")[-1]
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", slug)
    return slug or "movie"

def read_urls(path: str):
    if not os.path.exists(path):
        raise FileNotFoundError(f"Missing {path}. Create it with one RT URL per line.")
    urls = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            urls.append(line)
    return urls

def click_cookie_if_present(page):
    # Best-effort: consent UIs vary
    for name in ["Accept", "Accept All", "I Agree", "Agree", "OK"]:
        try:
            page.get_by_role("button", name=name).click(timeout=1500)
            break
        except Exception:
            pass

def main():
    urls = read_urls(URLS_FILE)
    os.makedirs(OUT_DIR, exist_ok=True)

    stamp = datetime.now(NY_TZ).strftime("%Y-%m-%d_%H-%M-%S_ET")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        context = browser.new_context(
            viewport=VIEWPORT,
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        page = context.new_page()

        for url in urls:
            movie_slug = slugify(url)
            out_path = os.path.join(OUT_DIR, f"{stamp}__{movie_slug}.png")

            try:
                print(f"Loading: {url}")
                page.goto(url, wait_until="domcontentloaded", timeout=60_000)

                # Let network settle + web components render
                try:
                    page.wait_for_load_state("networkidle", timeout=60_000)
                except PlaywrightTimeoutError:
                    # Not fatal; some pages keep long-polling
                    pass

                click_cookie_if_present(page)
                page.wait_for_timeout(4000)

                # NO scrolling: screenshot a fixed top-of-page region
                page.screenshot(path=out_path, clip=CLIP, timeout=20_000)
                print(f"Saved: {out_path}")

            except Exception as e:
                # Don't fail the whole run—log and continue
                print(f"ERROR on {url}: {e}")

                # Fallback: full viewport screenshot (still no scrolling)
                fallback_path = os.path.join(OUT_DIR, f"{stamp}__{movie_slug}__FALLBACK.png")
                try:
                    page.screenshot(path=fallback_path, timeout=20_000)
                    print(f"Saved fallback: {fallback_path}")
                except Exception as e2:
                    print(f"ERROR saving fallback for {url}: {e2}")

        context.close()
        browser.close()

if __name__ == "__main__":
    main()
