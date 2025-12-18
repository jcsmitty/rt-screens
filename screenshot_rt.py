import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

URLS_FILE = "urls.txt"
OUT_DIR = "screenshots"

NY_TZ = ZoneInfo("America/New_York")

VIEWPORT = {"width": 1440, "height": 900}

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

def try_click_by_text(page, texts, timeout_ms=1500):
    for t in texts:
        try:
            page.get_by_role("button", name=t).click(timeout=timeout_ms)
            return True
        except Exception:
            pass
        try:
            page.get_by_role("link", name=t).click(timeout=timeout_ms)
            return True
        except Exception:
            pass
    return False

def close_popups(page):
    # Consent / modal buttons
    try_click_by_text(page, ["Accept", "Accept All", "I Agree", "Agree", "OK", "Got it", "Close"])

    # Escape often closes overlays
    try:
        page.keyboard.press("Escape")
    except Exception:
        pass

    # Generic close controls
    selectors = [
        "button[aria-label='Close']",
        "button[title='Close']",
        "[data-qa='modal-close']",
        "button:has-text('×')",
        "button:has-text('✕')",
        "button:has-text('X')",
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0:
                loc.click(timeout=1000)
                break
        except Exception:
            pass

def scroll_half_screen(page):
    """Scroll down ~½ viewport height."""
    delta = VIEWPORT["height"] // 2
    try:
        page.mouse.wheel(0, delta)
    except Exception:
        page.evaluate("(h) => window.scrollBy(0, h)", delta)

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

                # Let RT finish hydrating
                try:
                    page.wait_for_load_state("networkidle", timeout=45_000)
                except PlaywrightTimeoutError:
                    pass

                page.wait_for_timeout(1500)
                close_popups(page)
                page.wait_for_timeout(800)
                close_popups(page)

                # Scroll down half a screen
                scroll_half_screen(page)
                page.wait_for_timeout(1000)

                # ALWAYS take fallback-style screenshot (full viewport)
                page.screenshot(path=out_path, timeout=20_000)
                print(f"Saved: {out_path}")

            except Exception as e:
                print(f"ERROR on {url}: {e}")

        context.close()
        browser.close()

if __name__ == "__main__":
    main()
