import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright

URLS_FILE = "urls.txt"
OUT_DIR = "screenshots"
NY_TZ = ZoneInfo("America/New_York")

def slugify(url: str) -> str:
    slug = url.rstrip("/").split("/")[-1]
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", slug)
    return slug or "movie"

def read_urls():
    with open(URLS_FILE, "r", encoding="utf-8") as f:
        return [l.strip() for l in f if l.strip() and not l.startswith("#")]

def click_cookie_if_present(page):
    for name in ["Accept", "Accept All", "I Agree", "Agree", "OK"]:
        try:
            page.get_by_role("button", name=name).click(timeout=1500)
            break
        except Exception:
            pass

def main():
    urls = read_urls()
    os.makedirs(OUT_DIR, exist_ok=True)

    stamp = datetime.now(NY_TZ).strftime("%Y-%m-%d_%H-%M-%S_ET")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()

        for url in urls:
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            click_cookie_if_present(page)
            page.wait_for_timeout(2500)

            # Primary selector
            scoreboard = page.locator("score-board").first

            # Fallbacks
            fallbacks = [
                page.locator("[data-qa='score-panel']").first,
                page.locator("[data-qa='tomatometer-container']").first,
                page.locator("text=Tomatometer").first.locator(
                    "xpath=ancestor::*[self::section or self::div][1]"
                )
            ]

            target = scoreboard if scoreboard.count() else None
            if target is None:
                for fb in fallbacks:
                    if fb.count():
                        target = fb
                        break

            if target is None:
                # Last resort
                path = f"{OUT_DIR}/{stamp}__{slugify(url)}__TOP.png"
                page.screenshot(path=path)
                continue

            target.scroll_into_view_if_needed(timeout=5000)
            path = f"{OUT_DIR}/{stamp}__{slugify(url)}.png"
            target.screenshot(path=path)

        browser.close()

if __name__ == "__main__":
    main()
