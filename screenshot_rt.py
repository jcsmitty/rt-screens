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

def try_click_by_text(page, texts, timeout_ms=1500) -> bool:
    """Try clicking buttons/links by accessible name."""
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
        "button[aria-label='close']",
        "button[title='Close']",
        "button[title='close']",
        "[data-qa='modal-close']",
        "[data-testid='close-button']",
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

def click_tomatometer_icon(page) -> bool:
    """
    Click the Tomatometer icon/area to open the fresh vs rotten breakdown.

    Rotten Tomatoes markup varies a lot, so we try several strategies:
    - role-based click by accessible name
    - common data-qa/testid hooks
    - click inside score-board component
    - click near 'Tomatometer' label
    """
    # 1) Accessible name attempts (best when present)
    # These are intentionally broad.
    if try_click_by_text(page, ["Tomatometer", "Tomatometer®"], timeout_ms=2000):
        return True

    # 2) Common hooks / selectors (best-effort)
    selector_candidates = [
        # Sometimes the icon or trigger has a qa/test id
        "[data-qa*='tomatometer']",
        "[data-testid*='tomatometer']",
        "a[href*='tomatometer']",
        "button:has-text('Tomatometer')",
        "a:has-text('Tomatometer')",

        # RT often uses <score-board> web component near the top
        "score-board",
    ]

    for sel in selector_candidates:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0:
                # If we matched score-board itself, try clicking within it
                if sel == "score-board":
                    # Click the tomatometer "block" inside the scoreboard:
                    # Try to click on the percentage / tomato icon area by targeting common sub-elements.
                    inner_candidates = [
                        "button",
                        "a",
                        "[data-qa*='tomatometer']",
                        "text=Tomatometer",
                        "text=%",  # often the score contains a percent
                    ]
                    for inner in inner_candidates:
                        try:
                            inner_loc = loc.locator(inner).first
                            if inner_loc.count() > 0:
                                inner_loc.click(timeout=2000)
                                return True
                        except Exception:
                            pass
                else:
                    loc.click(timeout=2000)
                    return True
        except Exception:
            pass

    # 3) Click near the Tomatometer label: find the label, click its nearest clickable ancestor
    try:
        label = page.locator("text=Tomatometer").first
        if label.count() > 0:
            # Walk up to a reasonable ancestor and click it
            container = label.locator("xpath=ancestor::*[self::button or self::a or self::div][1]")
            if container.count() > 0:
                container.first.click(timeout=2000)
                return True
    except Exception:
        pass

    return False

def wait_for_breakdown_to_appear(page):
    """
    After clicking, wait briefly for a breakdown UI to appear.
    We keep this light because we still want the run to succeed even if it never appears.
    """
    # Try a few telltale words that often show up in the breakdown popover/modal.
    # If none appear, we just move on and screenshot anyway.
    try:
        page.wait_for_timeout(700)
        for pattern in ["Fresh", "Rotten", "fresh", "rotten"]:
            loc = page.locator(f"text={pattern}")
            if loc.count() > 0:
                return
        # If page is busy, give it a little more time
        page.wait_for_timeout(800)
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

                # Let RT finish hydrating
                try:
                    page.wait_for_load_state("networkidle", timeout=45_000)
                except PlaywrightTimeoutError:
                    # not fatal
                    pass

                page.wait_for_timeout(1500)
                close_popups(page)
                page.wait_for_timeout(800)
                close_popups(page)

                # Scroll down ~half a screen
                scroll_half_screen(page)
                page.wait_for_timeout(900)

                # Click Tomatometer icon/area to open breakdown
                clicked = click_tomatometer_icon(page)
                print(f"Tomatometer click: {'OK' if clicked else 'FAILED'}")

                # Some sites throw another modal after interaction
                page.wait_for_timeout(500)
                close_popups(page)

                # Wait briefly for breakdown to appear
                wait_for_breakdown_to_appear(page)

                # Fallback-only screenshot (full viewport) AFTER click
                page.screenshot(path=out_path, timeout=20_000)
                print(f"Saved: {out_path}")

            except Exception as e:
                print(f"ERROR on {url}: {e}")
                # Still try to capture *something*
                try:
                    page.screenshot(path=out_path, timeout=20_000)
                    print(f"Saved (error fallback): {out_path}")
                except Exception as e2:
                    print(f"ERROR saving screenshot for {url}: {e2}")

        context.close()
        browser.close()

if __name__ == "__main__":
    main()
