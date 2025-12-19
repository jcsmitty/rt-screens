import os
import re
import json
import csv
from datetime import datetime
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

URLS_FILE = "urls.txt"

OUT_SCREEN_DIR = "screenshots"   # optional screenshots
OUT_DATA_DIR = "data"            # per-movie JSON dumps
OUT_CSV_DIR = "csv"              # aggregated CSV per run

NY_TZ = ZoneInfo("America/New_York")
VIEWPORT = {"width": 1440, "height": 900}

# Toggle these
TAKE_SCREENSHOTS = False
SCROLL_PIXELS = VIEWPORT["height"] // 3  # “half a screen less” than before

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
    try_click_by_text(page, ["Accept", "Accept All", "I Agree", "Agree", "OK", "Got it", "Close"])
    try:
        page.keyboard.press("Escape")
    except Exception:
        pass

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

def scroll_by(page, pixels: int):
    try:
        page.mouse.wheel(0, pixels)
    except Exception:
        page.evaluate("(h) => window.scrollBy(0, h)", pixels)

def get_media_scorecard_json(page) -> dict:
    """
    Extract the JSON from:
      <script id="media-scorecard-json" type="application/json"> ... </script>

    Returns parsed dict.
    """
    loc = page.locator("script#media-scorecard-json").first
    if loc.count() == 0:
        raise RuntimeError("media-scorecard-json script tag not found")

    raw = loc.inner_text(timeout=10_000).strip()
    if not raw:
        raise RuntimeError("media-scorecard-json script tag is empty")
    return json.loads(raw)

def pick(d: dict, keys):
    return {k: d.get(k) for k in keys}

def normalize_scorecard(url: str, slug: str, stamp: str, data: dict) -> dict:
    """
    Pull out the exact fields you care about in a stable structure.
    Your pasted blob includes:
      criticsScore: likedCount, notLikedCount, reviewCount, scorePercent, averageRating, ...
      audienceScore: ... same
      overlay: criticsAll, criticsTop, audienceAll, audienceVerified ...
    """
    out = {
        "url": url,
        "slug": slug,
        "timestamp_et": stamp,
    }

    critics = data.get("criticsScore") or {}
    audience = data.get("audienceScore") or {}
    overlay = data.get("overlay") or {}

    out["criticsScore"] = pick(critics, [
        "score", "scorePercent", "averageRating", "reviewCount", "ratingCount",
        "likedCount", "notLikedCount", "sentiment", "certified", "reviewsPageUrl", "title"
    ])

    out["audienceScore"] = pick(audience, [
        "score", "scorePercent", "averageRating", "reviewCount",
        "likedCount", "notLikedCount", "sentiment", "certified", "scoreType",
        "bandedRatingCount", "reviewsPageUrl", "title"
    ])

    # Breakdown buckets (when present)
    out["overlay"] = {}
    for key in ["criticsAll", "criticsTop", "audienceAll", "audienceVerified"]:
        if key in overlay and isinstance(overlay[key], dict):
            out["overlay"][key] = pick(overlay[key], [
                "score", "scorePercent", "averageRating", "reviewCount", "ratingCount",
                "likedCount", "notLikedCount", "scoreType", "bandedRatingCount",
                "sentiment", "certified", "scoreLinkText", "scoreLinkUrl", "reviewsPageUrl", "title"
            ])

    # Useful metadata if you want it
    out["mediaType"] = overlay.get("mediaType") or data.get("mediaType")
    out["primaryImageUrl"] = data.get("primaryImageUrl")
    out["description"] = data.get("description")

    return out

def flatten_for_csv(record: dict) -> dict:
    """
    Produce one CSV row with the most important numbers.
    """
    def g(path, default=None):
        cur = record
        for p in path:
            if not isinstance(cur, dict):
                return default
            cur = cur.get(p)
        return cur if cur is not None else default

    row = {
        "timestamp_et": record["timestamp_et"],
        "slug": record["slug"],
        "url": record["url"],

        "critics_score_percent": g(["criticsScore", "scorePercent"]),
        "critics_review_count": g(["criticsScore", "reviewCount"]),
        "critics_liked": g(["criticsScore", "likedCount"]),
        "critics_not_liked": g(["criticsScore", "notLikedCount"]),
        "critics_avg_rating": g(["criticsScore", "averageRating"]),

        "audience_score_percent": g(["audienceScore", "scorePercent"]),
        "audience_review_count": g(["audienceScore", "reviewCount"]),
        "audience_liked": g(["audienceScore", "likedCount"]),
        "audience_not_liked": g(["audienceScore", "notLikedCount"]),
        "audience_avg_rating": g(["audienceScore", "averageRating"]),
        "audience_score_type": g(["audienceScore", "scoreType"]),
    }

    # Optional breakdown columns
    for key, prefix in [
        ("criticsAll", "critics_all"),
        ("criticsTop", "critics_top"),
        ("audienceAll", "audience_all"),
        ("audienceVerified", "audience_verified"),
    ]:
        row[f"{prefix}_score_percent"] = g(["overlay", key, "scorePercent"])
        row[f"{prefix}_review_count"] = g(["overlay", key, "reviewCount"])
        row[f"{prefix}_liked"] = g(["overlay", key, "likedCount"])
        row[f"{prefix}_not_liked"] = g(["overlay", key, "notLikedCount"])
        row[f"{prefix}_avg_rating"] = g(["overlay", key, "averageRating"])

    return row

def main():
    urls = read_urls(URLS_FILE)
    os.makedirs(OUT_DATA_DIR, exist_ok=True)
    os.makedirs(OUT_CSV_DIR, exist_ok=True)
    if TAKE_SCREENSHOTS:
        os.makedirs(OUT_SCREEN_DIR, exist_ok=True)

    stamp = datetime.now(NY_TZ).strftime("%Y-%m-%d_%H-%M-%S_ET")

    rows = []

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
            slug = slugify(url)
            json_out_path = os.path.join(OUT_DATA_DIR, f"{stamp}__{slug}.json")
            png_out_path = os.path.join(OUT_SCREEN_DIR, f"{stamp}__{slug}.png")

            try:
                print(f"Loading: {url}")
                page.goto(url, wait_until="domcontentloaded", timeout=60_000)

                # Let RT hydrate; networkidle is best-effort (can hang)
                try:
                    page.wait_for_load_state("networkidle", timeout=45_000)
                except PlaywrightTimeoutError:
                    pass

                # Popups can block content; best-effort close
                page.wait_for_timeout(1200)
                close_popups(page)
                page.wait_for_timeout(700)
                close_popups(page)

                # Optional: scroll a bit if your popup/headers cover the scorecard
                scroll_by(page, SCROLL_PIXELS)
                page.wait_for_timeout(700)

                # ✅ Extract the embedded JSON (this is the main win)
                raw = get_media_scorecard_json(page)
                record = normalize_scorecard(url=url, slug=slug, stamp=stamp, data=raw)

                with open(json_out_path, "w", encoding="utf-8") as f:
                    json.dump(record, f, ensure_ascii=False, indent=2)
                print(f"Saved data: {json_out_path}")

                rows.append(flatten_for_csv(record))

                # Optional: still grab your fallback screenshot for visual audit
                if TAKE_SCREENSHOTS:
                    page.screenshot(path=png_out_path, timeout=20_000)
                    print(f"Saved screenshot: {png_out_path}")

            except Exception as e:
                print(f"ERROR on {url}: {e}")

        context.close()
        browser.close()

    # Write a per-run CSV
    if rows:
        csv_path = os.path.join(OUT_CSV_DIR, f"{stamp}__rt_scores.csv")
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        print(f"Saved CSV: {csv_path}")

if __name__ == "__main__":
    main()
