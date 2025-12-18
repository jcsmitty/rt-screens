import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

URLS_FILE = "urls.txt"
OUT_DIR = "screenshots"

NY_TZ = ZoneInfo("America/New_York")

VIEWPORT = {"width": 1440, "height": 900}

# Screenshot region relative to the viewport (we'll convert to page coords after scrolling)
VIEWPORT_CLIP = {"x": 0, "y": 0, "width": 1440, "height": 750}  # adjust height if needed

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
    """Try clicking buttons/links by accessible name."""
    for t in texts:
        # Try button
        try:
            page.get_by_role("button", name=t).click(timeout=timeout_ms)
            return True
        except Exception:
            pass
        # Try link (some consent banners use links)
        try:
            page.get_by_role("link", name=t).click(timeout=timeout_ms)
            return True
        except Exception:
            pass
    return False

def try_close_popups(page):
    """
    Best-effort popup handling:
    - click common consent buttons
    - press Escape
    - click common "X" close controls
    """
    # Common consent/close actions
    try_click_by_text(page, ["Accept", "Accept All", "I Agree", "Agree", "OK", "Got it", "Close"])

    # Escape sometimes closes modals
    try:
        page.keyboard.press("Escape")
    except Exception:
        pass

    # Try common close selectors for modals/overlays
    close_selectors = [
        "button[aria-label='Close']",
        "button[aria-label='close']",
        "button[title='Close']",
        "button[title='close']",
        "[data-qa='modal-close']",
        "[data-testid='close-button']",
        "button:has-text('×')",
        "button:has-text('✕')",
        "button:has-text('X')",
        "svg[aria-label='Close']",
    ]

    for sel in close_selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0:
                loc.click(timeout=1000)
                break
        except Exception:
            pass

def scroll_one_screen(page):
    """Scroll down by ~one viewport height."""
    try:
        # Wheel scroll is simple and tends to work even if the page is “busy”
        page.mouse.wheel(0, VIEWPORT["height"])
    except Exception:
        # Fallback to JS scroll
        page.evaluate("(h) => window.scrollBy(0, h)", VIEWPORT["height"])

def screenshot_viewport_clip(page, out_path: str):
    """
    Take a screenshot of a fixed region of the current viewport.
    Playwright's clip uses PAGE coordinates, so we add window.scrollY.
    """
    scroll_y = page.evaluate("() => window.scrollY") or 0
    clip = {
        "x": VIEWPORT_CLIP["x"],
        "y": scroll_y + VIEWPORT_CLIP["y"],
        "width": VIEWPORT_CLIP["width"],
        "height": VIEWPORT_CLIP["height"],
    }
    page.screenshot(path=out_path, clip=clip, timeout=20_000)

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

                # Let the page settle; networkidle can hang sometimes, so treat it as best-effort
                try:
                    page.wait_for_load_state("networkidle", timeout=45_000)
                except PlaywrightTimeoutError:
                    pass

                # Try to close popups (sometimes they appear after load)
                page.wait_for_timeout(1500)
                try_close_popups(page)
                page.wait_for_timeout(1000)
                try_close_popups(page)

                # Now scroll down one screen (as you requested)
                scroll_one_screen(page)

                # Short wait for any sticky headers / lazy render
                page.wait_for_timeout(1200)

                # Screenshot without using any element locators (stable)
                screenshot_viewport_clip(page, out_path)
                print(f"Saved: {out_path}")

            except Exception as e:
                print(f"ERROR on {url}: {e}")

                # Fallback: just grab the viewport (still after whatever scroll happened)
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
