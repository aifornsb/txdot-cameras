"""
TxDOT Dallas Camera Capture — Multi-Camera
===========================================
Captures one frame from every configured camera and saves it.
Called by GitHub Actions every 5 minutes.

To add or remove cameras, edit the CAMERAS list below.
"""

import asyncio, base64, csv, hashlib, sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

CT = timezone(timedelta(hours=-5))   # America/Chicago

# ── ADD / REMOVE CAMERAS HERE ─────────────────────────────────────────────────
# Each entry needs:
#   "search"  — exact text typed into the TxDOT search box
#   "folder"  — safe folder name used on disk (no spaces or special characters)
CAMERAS = [
    {"search": "IH35E @ Valley Ridge",       "folder": "IH35E-Valley-Ridge"},
    {"search": "IH35E @ Valley Ridge North", "folder": "IH35E-Valley-Ridge-North"},
    # Add more cameras below this line, same format:
    # {"search": "IH635 @ Luna Rd",          "folder": "IH635-Luna-Rd"},
    # {"search": "US75 @ Mockingbird",        "folder": "US75-Mockingbird"},
]
# ─────────────────────────────────────────────────────────────────────────────

PORTAL_URL = "https://its.txdot.gov/its/District/DAL/cameras"


async def capture_camera(page, camera: dict, ts_ct, ts_utc) -> dict:
    """
    Navigate to one camera, grab its image, save it.
    Returns a result dict with status and file info.
    """
    search = camera["search"]
    folder = camera["folder"]
    stamp  = ts_ct.strftime("%Y%m%d_%H%M%S")
    date   = ts_ct.strftime("%Y-%m-%d")

    img_dir = Path(f"images/{folder}/{date}")
    img_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n  [{search}]")

    # Clear and re-search for this camera
    for sel in ["input[placeholder*='Search']", "input[type='search']",
                ".search-input input"]:
        try:
            box = page.locator(sel).first
            await box.triple_click(timeout=2_000)
            await box.fill(search, timeout=2_000)
            await page.wait_for_timeout(2_500)
            break
        except Exception:
            continue

    # Click matching result
    clicked = False
    for sel in [f"text={search}", f"text={search.split('@')[1].strip()}",
                ".camera-item:first-child"]:
        try:
            await page.locator(sel).first.click(timeout=3_000)
            await page.wait_for_timeout(4_000)
            clicked = True
            break
        except Exception:
            continue

    if not clicked:
        print(f"    Could not click result — skipping")
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
                return imgs.reduce((a,b) =>
                    a.naturalWidth > b.naturalWidth ? a : b
                ).src.split(',')[1] || null;
            }""")
            if b64 and len(b64) > 10_000:
                data = base64.b64decode(b64)
                if len(data) > 20_000:
                    break
        except Exception:
            await page.wait_for_timeout(2_000)

    # Fallback: screenshot the selected panel
    if not data:
        for sel in [".selected", ".camera-item.selected",
                    "mat-dialog-container"]:
            try:
                data = await page.locator(sel).first.screenshot(
                    type="jpeg", quality=90, timeout=5_000)
                if data and len(data) > 20_000:
                    break
            except Exception:
                continue

    if not data or len(data) < 10_000:
        print(f"    No image captured")
        return {"status": "error", "notes": "no image data",
                "filepath": "", "size": 0, "md5": ""}

    # Save
    fname = f"{folder}_{stamp}.jpg"
    fpath = img_dir / fname
    fpath.write_bytes(data)
    md5   = hashlib.md5(data).hexdigest()
    rel   = f"images/{folder}/{date}/{fname}"
    print(f"    Saved: {rel}  ({len(data)//1024} KB)")

    return {"status": "captured", "notes": "",
            "filepath": rel, "size": len(data), "md5": md5}


async def main():
    # Install playwright browser
    import subprocess
    subprocess.run([sys.executable, "-m", "playwright", "install",
                    "chromium", "--with-deps"], check=True)

    from playwright.async_api import async_playwright

    ts_utc = datetime.now(timezone.utc)
    ts_ct  = ts_utc.astimezone(CT)
    print(f"Capture run: {ts_ct.strftime('%Y-%m-%d %H:%M:%S CT')}")
    print(f"Cameras:     {len(CAMERAS)}")

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

        # Load the portal once — reuse the same page for all cameras
        print("\nLoading TxDOT portal…")
        try:
            await page.goto(PORTAL_URL, wait_until="domcontentloaded",
                            timeout=40_000)
        except Exception as e:
            print(f"  goto timeout (normal for SPA): {e}")
        await page.wait_for_timeout(9_000)
        print(f"  Page ready: {await page.title()!r}")

        # Capture each camera in sequence
        results = []
        for camera in CAMERAS:
            result = await capture_camera(page, camera, ts_ct, ts_utc)
            results.append((camera, result))

        await browser.close()

    # Write CSV log
    csv_path = Path("summary.csv")
    new_file = not csv_path.exists()
    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["timestamp_ct", "timestamp_utc", "camera_name",
                        "folder", "filepath", "size_bytes", "md5",
                        "status", "notes"])
        for camera, r in results:
            w.writerow([
                ts_ct.strftime("%Y-%m-%d %H:%M:%S CT"),
                ts_utc.strftime("%Y-%m-%d %H:%M:%S UTC"),
                camera["search"], camera["folder"],
                r["filepath"], r["size"], r["md5"],
                r["status"], r["notes"],
            ])

    # Summary
    captured = sum(1 for _, r in results if r["status"] == "captured")
    print(f"\nDone: {captured}/{len(CAMERAS)} cameras captured successfully")
    if captured == 0:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
