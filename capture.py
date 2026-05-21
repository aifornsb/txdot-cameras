"""
TxDOT Multi-District Camera Capture
=====================================
Captures one frame from every configured camera across any TxDOT district.
Each camera entry specifies its own portal URL, so districts never get mixed up.

To add a camera: add one line to the CAMERAS list with its portal URL,
search name, and folder name. That's it.
"""

import asyncio, base64, csv, hashlib, sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

CT = timezone(timedelta(hours=-5))   # America/Chicago

# ══════════════════════════════════════════════════════════════════════════════
#  CAMERA LIST  — add / remove cameras here
#  Each entry needs three fields:
#    "portal"  — full URL of the district camera page
#    "search"  — exact camera name as it appears on the TxDOT portal
#    "folder"  — safe folder name saved to disk (no spaces, use hyphens)
# ══════════════════════════════════════════════════════════════════════════════
CAMERAS = [

    # ── Dallas (DAL) ──────────────────────────────────────────────────────────
    {
        "portal": "https://its.txdot.gov/its/District/DAL/cameras",
        "search": "IH35E @ Valley Ridge",
        "folder": "DAL-IH35E-Valley-Ridge",
    },
    {
        "portal": "https://its.txdot.gov/its/District/DAL/cameras",
        "search": "IH35E @ Valley Ridge North",
        "folder": "DAL-IH35E-Valley-Ridge-North",
    },

    # ── Austin (AUS) ──────────────────────────────────────────────────────────
    {
        "portal": "https://its.txdot.gov/its/District/AUS/cameras",
        "search": "LP-1 @ Steck Ave",
        "folder": "AUS-MoPac-Steck",
    },

    # ── Houston (HOU) ─────────────────────────────────────────────────────────
    {
        "portal": "https://its.txdot.gov/its/District/HOU/cameras",
        "search": "US-290 Northwest @ Gessner (W)",
        "folder": "HOU-US290-Gessner-W",
    },
    {
        "portal": "https://its.txdot.gov/its/District/HOU/cameras",
        "search": "US-290 Northwest @ Little York",
        "folder": "HOU-US290-LittleYork",
    },
    {
        "portal": "https://its.txdot.gov/its/District/HOU/cameras",
        "search": "IH-10 Katy @ SH 6 (W)",
        "folder": "HOU-Katy-ML-SH6-W",
    },
    {
        "portal": "https://its.txdot.gov/its/District/HOU/cameras",
        "search": "US-290 Northwest @ Cypress Rosehill",
        "folder": "HOU-US290-CypressRosehill",
    },

    # ── San Antonio (SAT) ─────────────────────────────────────────────────────
    {
        "portal": "https://its.txdot.gov/its/District/SAT/cameras",
        "search": "US 281 at Sprucewood",
        "folder": "SAT-US281-Sprucewood",
    },

    # ── Fort Worth (FTW) ──────────────────────────────────────────────────────
    {
        "portal": "https://its.txdot.gov/its/District/FTW/cameras",
        "search": "IH35W @ Golden Triangle",
        "folder": "FTW-IH35W-GoldenTriangle",
    },

]
# ══════════════════════════════════════════════════════════════════════════════


async def capture_one(page, camera: dict, ts_ct, ts_utc) -> dict:
    """
    Navigate to a single camera on the already-loaded portal page,
    extract its image, and save it.
    Returns a result dict.
    """
    search = camera["search"]
    folder = camera["folder"]
    stamp  = ts_ct.strftime("%Y%m%d_%H%M%S")
    date   = ts_ct.strftime("%Y-%m-%d")

    img_dir = Path(f"images/{folder}/{date}")
    img_dir.mkdir(parents=True, exist_ok=True)

    print(f"    [{search}]")

    # Clear the search box and type this camera's name
    for sel in [
        "input[placeholder*='Search']",
        "input[placeholder*='search']",
        "input[type='search']",
        ".search-input input",
    ]:
        try:
            box = page.locator(sel).first
            await box.triple_click(timeout=2_000)
            await box.fill(search, timeout=2_000)
            await page.wait_for_timeout(2_500)
            break
        except Exception:
            continue

    # Click the matching result
    clicked = False
    # Try exact name first, then progressive fallbacks
    search_words = search.split()
    fallbacks = [
        f"text={search}",
        f"text={' '.join(search_words[-3:])}",   # last 3 words
        f"text={search_words[-1]}",               # last word
        ".camera-item:first-child",
    ]
    for sel in fallbacks:
        try:
            el = page.locator(sel).first
            await el.click(timeout=3_000)
            await page.wait_for_timeout(4_000)
            clicked = True
            break
        except Exception:
            continue

    if not clicked:
        print(f"      Could not click result — skipping")
        return {"status": "error", "notes": "result not clickable",
                "filepath": "", "size": 0, "md5": ""}

    # Extract the largest base64 JPEG on the page
    data = None
    for attempt in range(3):
        try:
            b64 = await page.evaluate("""() => {
                const imgs = [...document.querySelectorAll('img')]
                    .filter(i => i.naturalWidth >= 640
                             && i.src.startsWith('data:image/jpeg'));
                if (!imgs.length) return null;
                return imgs.reduce((a, b) =>
                    a.naturalWidth > b.naturalWidth ? a : b
                ).src.split(',')[1] || null;
            }""")
            if b64 and len(b64) > 10_000:
                data = base64.b64decode(b64)
                if len(data) > 20_000:
                    break
        except Exception:
            await page.wait_for_timeout(2_000)

    # Fallback: screenshot the selected camera panel
    if not data:
        for sel in [".selected", ".camera-item.selected",
                    "mat-dialog-container", ".camera-panel"]:
            try:
                data = await page.locator(sel).first.screenshot(
                    type="jpeg", quality=90, timeout=5_000)
                if data and len(data) > 20_000:
                    print(f"      Used panel screenshot fallback")
                    break
            except Exception:
                continue

    if not data or len(data) < 10_000:
        print(f"      No image captured")
        return {"status": "error", "notes": "no image data",
                "filepath": "", "size": 0, "md5": ""}

    # Save the image
    fname = f"{folder}_{stamp}.jpg"
    fpath = img_dir / fname
    fpath.write_bytes(data)
    md5   = hashlib.md5(data).hexdigest()
    rel   = f"images/{folder}/{date}/{fname}"
    print(f"      Saved: {rel}  ({len(data)//1024} KB)")

    return {"status": "captured", "notes": "",
            "filepath": rel, "size": len(data), "md5": md5}


async def main():
    # ── Install Playwright browser ────────────────────────────────────────────
    import subprocess
    subprocess.run(
        [sys.executable, "-m", "playwright", "install", "chromium", "--with-deps"],
        check=True,
    )
    from playwright.async_api import async_playwright

    ts_utc = datetime.now(timezone.utc)
    ts_ct  = ts_utc.astimezone(CT)
    print(f"\nCapture run : {ts_ct.strftime('%Y-%m-%d %H:%M:%S CT')}")
    print(f"Cameras     : {len(CAMERAS)}")

    # ── Group cameras by portal URL ───────────────────────────────────────────
    # This means we only load each district portal once, then capture all
    # cameras from that district before moving on — much faster.
    from collections import defaultdict
    by_portal = defaultdict(list)
    for cam in CAMERAS:
        by_portal[cam["portal"]].append(cam)

    results = []   # list of (camera, result) tuples

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
        )
        ctx = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
        )
        await ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
        )
        page = await ctx.new_page()

        # ── Process each district portal ──────────────────────────────────────
        for portal_url, cameras in by_portal.items():
            district = portal_url.split("/District/")[1].split("/")[0]
            print(f"\n  District: {district}  ({len(cameras)} cameras)")
            print(f"  Portal  : {portal_url}")

            # Load this district's portal
            try:
                await page.goto(portal_url,
                                wait_until="domcontentloaded", timeout=45_000)
            except Exception as e:
                print(f"  goto timeout (normal for SPA): {e}")

            await page.wait_for_timeout(9_000)
            print(f"  Page ready: {await page.title()!r}")

            # Capture each camera from this district
            for camera in cameras:
                result = await capture_one(page, camera, ts_ct, ts_utc)
                results.append((camera, result))

        await browser.close()

    # ── Write CSV log ─────────────────────────────────────────────────────────
    csv_path = Path("summary.csv")
    new_file = not csv_path.exists()
    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow([
                "timestamp_ct", "timestamp_utc",
                "district", "camera_name", "folder",
                "filepath", "size_bytes", "md5",
                "status", "notes",
            ])
        for camera, r in results:
            district = camera["portal"].split("/District/")[1].split("/")[0]
            w.writerow([
                ts_ct.strftime("%Y-%m-%d %H:%M:%S CT"),
                ts_utc.strftime("%Y-%m-%d %H:%M:%S UTC"),
                district,
                camera["search"],
                camera["folder"],
                r["filepath"],
                r["size"],
                r["md5"],
                r["status"],
                r["notes"],
            ])

    # ── Final summary ─────────────────────────────────────────────────────────
    captured = sum(1 for _, r in results if r["status"] == "captured")
    failed   = sum(1 for _, r in results if r["status"] == "error")
    print(f"\n{'─'*50}")
    print(f"  Done: {captured}/{len(CAMERAS)} captured   {failed} failed")
    print(f"{'─'*50}")

    if captured == 0:
        sys.exit(1)   # fail the GitHub Actions step if nothing worked


if __name__ == "__main__":
    asyncio.run(main())
