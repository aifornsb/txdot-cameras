"""
TxDOT Camera Capture — IH35E @ Valley Ridge ONLY
=================================================
Captures one image every 5 minutes from IH35E @ Valley Ridge (Dallas).
All other cameras are commented out.

Images saved to: images/DAL-IH35E-Valley-Ridge/YYYY-MM-DD/
"""

import asyncio, base64, csv, hashlib, os, subprocess, sys, time
from datetime import datetime, timezone, timedelta
from pathlib import Path

CT           = timezone(timedelta(hours=-5))
INTERVAL     = 5 * 60
JOB_DURATION = 5 * 3600 + 50 * 60

# ══════════════════════════════════════════════════════════════════════════════
#  ACTIVE CAMERAS
# ══════════════════════════════════════════════════════════════════════════════
CAMERAS = [
    {
        "portal": "https://its.txdot.gov/its/District/DAL/cameras",
        "search": "IH35E @ Valley Ridge",
        "folder": "DAL-IH35E-Valley-Ridge",
    },
]

# ══════════════════════════════════════════════════════════════════════════════
#  COMMENTED OUT — add back when ready
# ══════════════════════════════════════════════════════════════════════════════
# {"portal": "https://its.txdot.gov/its/District/DAL/cameras",
#  "search": "IH35E @ Valley Ridge North",   "folder": "DAL-IH35E-Valley-Ridge-North"},
# {"portal": "https://its.txdot.gov/its/District/AUS/cameras",
#  "search": "LP-1 @ Duval Rd (12000) 27",  "folder": "AUS-MoPac-Duval"},
# {"portal": "https://its.txdot.gov/its/District/HOU/cameras",
#  "search": "IH-10 Katy @ SH 6 (W)",       "folder": "HOU-Katy-ML-SH6-W"},
# {"portal": "https://its.txdot.gov/its/District/SAT/cameras",
#  "search": "US 281 at Sprucewood",         "folder": "SAT-US281-Sprucewood"},
# {"portal": "https://its.txdot.gov/its/District/FTW/cameras",
#  "search": "IH35W @ Golden Triangle",      "folder": "FTW-IH35W-GoldenTriangle"},


def git_push(message: str):
    """Commit and push new images. Safe against concurrent runs."""
    for cmd in [
        ["git", "config", "user.name",  "Camera Bot"],
        ["git", "config", "user.email", "camera-bot@github-actions"],
        ["git", "fetch", "origin", "main"],
        ["git", "reset", "--soft", "origin/main"],
        ["git", "add", "images/", "summary.csv"],
    ]:
        subprocess.run(cmd, check=False)

    diff = subprocess.run(["git", "diff", "--staged", "--quiet"], check=False)
    if diff.returncode == 0:
        print("    (no new images to commit)")
        return

    subprocess.run(["git", "commit", "-m", message], check=False)
    result = subprocess.run(["git", "push"], check=False)
    if result.returncode != 0:
        subprocess.run(["git", "fetch", "origin", "main"], check=False)
        subprocess.run(["git", "reset", "--soft", "origin/main"], check=False)
        subprocess.run(["git", "add", "images/", "summary.csv"], check=False)
        subprocess.run(["git", "commit", "--amend", "--no-edit"], check=False)
        subprocess.run(["git", "push", "--force-with-lease"], check=False)


async def capture_one(page, camera: dict, ts_ct, ts_utc) -> dict:
    """
    Capture exactly one camera using its id= attribute.
    This guarantees the right image even when multiple cameras appear on screen.
    """
    search = camera["search"]
    folder = camera["folder"]
    stamp  = ts_ct.strftime("%Y%m%d_%H%M%S")
    date   = ts_ct.strftime("%Y-%m-%d")

    img_dir = Path(f"images/{folder}/{date}")
    img_dir.mkdir(parents=True, exist_ok=True)

    # Type camera name into search box
    for sel in ["input[placeholder*='Search']", "input[placeholder*='search']",
                "input[type='search']", ".search-input input"]:
        try:
            box = page.locator(sel).first
            await box.triple_click(timeout=2_000)
            await box.fill(search, timeout=2_000)
            await page.wait_for_timeout(2_500)
            break
        except Exception:
            continue

    # Click the exact card by its id= attribute (matches camera name exactly)
    clicked = False
    try:
        card = page.locator(f'div[id="{search}"]').first
        await card.click(timeout=3_000)
        await page.wait_for_timeout(4_000)
        clicked = True
    except Exception:
        pass

    if not clicked:
        try:
            link = page.get_by_role("link", name=search, exact=True).first
            await link.click(timeout=3_000)
            await page.wait_for_timeout(4_000)
            clicked = True
        except Exception:
            pass

    if not clicked:
        try:
            await page.locator(".cctv-list-item").first.click(timeout=3_000)
            await page.wait_for_timeout(4_000)
            clicked = True
        except Exception:
            pass

    if not clicked:
        return {"status": "error", "notes": "could not click camera result",
                "filepath": "", "size": 0, "md5": ""}

    # Extract image from the exact card whose id= matches the camera name
    data = None

    # Primary: get image from card with exact id
    try:
        b64 = await page.evaluate(
            """(search) => {
                const card = document.getElementById(search);
                if (!card) return null;
                const img = card.querySelector('img[src^="data:image/jpeg"]');
                if (!img || img.naturalWidth < 400) return null;
                return img.src.split(',')[1] || null;
            }""", search)
        if b64 and len(b64) > 10_000:
            data = base64.b64decode(b64)
    except Exception:
        pass

    # Secondary: get image from the .selected card
    if not data:
        try:
            b64 = await page.evaluate("""() => {
                const card = document.querySelector(
                    '.cctv-list-item.selected, .camera-item.selected');
                if (!card) return null;
                const img = card.querySelector('img[src^="data:image/jpeg"]');
                if (!img || img.naturalWidth < 400) return null;
                return img.src.split(',')[1] || null;
            }""")
            if b64 and len(b64) > 10_000:
                data = base64.b64decode(b64)
        except Exception:
            pass

    # Tertiary: screenshot the selected card element
    if not data:
        try:
            card_el = page.locator(
                f'div[id="{search}"], .cctv-list-item.selected').first
            data = await card_el.screenshot(type="jpeg", quality=90, timeout=5_000)
            if data and len(data) < 20_000:
                data = None
        except Exception:
            pass

    if not data or len(data) < 10_000:
        print(f"      No image captured")
        return {"status": "error", "notes": "no image data",
                "filepath": "", "size": 0, "md5": ""}

    # Verify we got the right camera by checking the OSD text in the image
    # (The OSD "IH35E-VALLEY RIDGE" text is burned into the image itself)
    fname = f"{folder}_{stamp}.jpg"
    fpath = img_dir / fname
    fpath.write_bytes(data)
    md5   = hashlib.md5(data).hexdigest()
    rel   = f"images/{folder}/{date}/{fname}"
    print(f"      ✓ Saved: {fname}  ({len(data)//1024} KB)  md5={md5[:8]}")

    return {"status": "captured", "notes": "",
            "filepath": rel, "size": len(data), "md5": md5}


async def run_one_interval(results_accumulator):
    from playwright.async_api import async_playwright
    from collections import defaultdict

    ts_utc = datetime.now(timezone.utc)
    ts_ct  = ts_utc.astimezone(CT)
    print(f"\n  {'─'*54}")
    print(f"  {ts_ct.strftime('%Y-%m-%d %H:%M:%S CT')}  — capturing {len(CAMERAS)} camera(s)")

    by_portal = defaultdict(list)
    for cam in CAMERAS:
        by_portal[cam["portal"]].append(cam)

    interval_results = []

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

        for portal_url, cameras in by_portal.items():
            district = portal_url.split("/District/")[1].split("/")[0]
            print(f"\n    District: {district}")
            try:
                await page.goto(portal_url,
                                wait_until="domcontentloaded", timeout=45_000)
            except Exception as e:
                print(f"    goto timeout: {e}")
            await page.wait_for_timeout(9_000)

            for camera in cameras:
                print(f"    [{camera['search']}]")
                try:
                    result = await capture_one(page, camera, ts_ct, ts_utc)
                except Exception as e:
                    print(f"      Error: {e}")
                    result = {"status": "error", "notes": str(e)[:80],
                              "filepath": "", "size": 0, "md5": ""}
                interval_results.append((camera, result))

        await browser.close()

    # Write CSV
    csv_path = Path("summary.csv")
    new_file = not csv_path.exists()
    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        import csv as csv_mod
        w = csv_mod.writer(f)
        if new_file:
            w.writerow(["timestamp_ct", "timestamp_utc", "district",
                        "camera_name", "folder", "filepath",
                        "size_bytes", "md5", "status", "notes"])
        for camera, r in interval_results:
            district = camera["portal"].split("/District/")[1].split("/")[0]
            w.writerow([ts_ct.strftime("%Y-%m-%d %H:%M:%S CT"),
                        ts_utc.strftime("%Y-%m-%d %H:%M:%S UTC"),
                        district, camera["search"], camera["folder"],
                        r["filepath"], r["size"], r["md5"],
                        r["status"], r["notes"]])

    captured = sum(1 for _, r in interval_results if r["status"] == "captured")
    print(f"\n    Result: {captured}/{len(CAMERAS)} captured")
    git_push(f"IH35E Valley Ridge — {ts_utc.strftime('%Y-%m-%d %H:%M UTC')}")
    results_accumulator.extend(interval_results)


async def main():
    subprocess.run([sys.executable, "-m", "playwright", "install",
                    "chromium", "--with-deps"], check=True)

    job_start   = time.monotonic()
    interval_n  = 0
    all_results = []

    ts_start = datetime.now(timezone.utc).astimezone(CT)
    print(f"\n{'═'*56}")
    print(f"  IH35E @ Valley Ridge — Capture Agent")
    print(f"  Started : {ts_start.strftime('%Y-%m-%d %H:%M:%S CT')}")
    print(f"  Camera  : IH35E @ Valley Ridge (Dallas)")
    print(f"  Interval: every {INTERVAL//60} minutes")
    print(f"  Output  : images/DAL-IH35E-Valley-Ridge/")
    print(f"{'═'*56}")

    while time.monotonic() - job_start < JOB_DURATION:
        interval_n += 1
        elapsed_h = (time.monotonic() - job_start) / 3600
        print(f"\n  Interval #{interval_n}  (elapsed: {elapsed_h:.1f}h / 6h)")

        tick = time.monotonic()
        try:
            await run_one_interval(all_results)
        except Exception as e:
            print(f"  Error: {e}")

        elapsed  = time.monotonic() - tick
        sleep_s  = max(0, INTERVAL - elapsed)
        time_left = JOB_DURATION - (time.monotonic() - job_start)

        if time_left < sleep_s + 180:
            print(f"\n  Approaching 6h limit — stopping cleanly.")
            break

        next_ct = datetime.now(timezone.utc).astimezone(CT) + timedelta(seconds=sleep_s)
        print(f"\n  Next capture at {next_ct.strftime('%H:%M:%S CT')} (in {sleep_s:.0f}s)")
        time.sleep(sleep_s)

    total = sum(1 for _, r in all_results if r["status"] == "captured")
    print(f"\n{'═'*56}")
    print(f"  Done: {interval_n} intervals, {total} images captured")
    print(f"{'═'*56}")


if __name__ == "__main__":
    asyncio.run(main())
