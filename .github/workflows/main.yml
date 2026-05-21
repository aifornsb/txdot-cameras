"""
TxDOT Multi-District Camera Capture — 24-Hour Loop
====================================================
Runs inside a single GitHub Actions job for up to 6 hours
(GitHub's job time limit). The workflow relaunches itself
automatically, giving continuous 24-hour coverage.

Captures every camera every 5 minutes.
"""

import asyncio, base64, csv, hashlib, os, subprocess, sys, time
from datetime import datetime, timezone, timedelta
from pathlib import Path

CT              = timezone(timedelta(hours=-5))   # America/Chicago
INTERVAL        = 5 * 60                          # 5 minutes in seconds
JOB_DURATION    = 5 * 3600                        # stop looping after 5h50m
                                                  # (safely under GitHub's 6h limit)

# ══════════════════════════════════════════════════════════════════════════════
#  CAMERA LIST
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


def git_push(message: str):
    """Commit and push new images. Safe against concurrent runs."""
    cmds = [
        ["git", "config", "user.name",  "Camera Bot"],
        ["git", "config", "user.email", "camera-bot@github-actions"],
        ["git", "fetch", "origin", "main"],
        ["git", "reset", "--soft", "origin/main"],
        ["git", "add", "images/", "summary.csv"],
    ]
    for cmd in cmds:
        subprocess.run(cmd, check=False)

    # Only commit if there are staged changes
    diff = subprocess.run(
        ["git", "diff", "--staged", "--quiet"], check=False
    )
    if diff.returncode == 0:
        print("    (no changes to commit)")
        return

    subprocess.run(["git", "commit", "-m", message], check=False)
    result = subprocess.run(["git", "push"], check=False)
    if result.returncode != 0:
        # Retry once on push failure
        subprocess.run(["git", "fetch", "origin", "main"], check=False)
        subprocess.run(["git", "reset", "--soft", "origin/main"], check=False)
        subprocess.run(["git", "add", "images/", "summary.csv"], check=False)
        subprocess.run(["git", "commit", "--amend", "--no-edit"], check=False)
        subprocess.run(["git", "push", "--force-with-lease"], check=False)


async def capture_one(page, camera: dict, ts_ct, ts_utc) -> dict:
    """Capture a single camera. Returns result dict."""
    search = camera["search"]
    folder = camera["folder"]
    stamp  = ts_ct.strftime("%Y%m%d_%H%M%S")
    date   = ts_ct.strftime("%Y-%m-%d")

    img_dir = Path(f"images/{folder}/{date}")
    img_dir.mkdir(parents=True, exist_ok=True)

    # Clear search box and type this camera's name
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

    # Click matching result
    for sel in [f"text={search}",
                f"text={' '.join(search.split()[-3:])}",
                ".camera-item:first-child"]:
        try:
            await page.locator(sel).first.click(timeout=3_000)
            await page.wait_for_timeout(4_000)
            break
        except Exception:
            continue

    # Extract the largest base64 JPEG on the page
    data = None
    for _ in range(3):
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

    # Fallback: screenshot camera panel
    if not data:
        for sel in [".selected", ".camera-item.selected", "mat-dialog-container"]:
            try:
                data = await page.locator(sel).first.screenshot(
                    type="jpeg", quality=90, timeout=5_000)
                if data and len(data) > 20_000:
                    break
            except Exception:
                continue

    if not data or len(data) < 10_000:
        return {"status": "error", "notes": "no image",
                "filepath": "", "size": 0, "md5": ""}

    fname = f"{folder}_{stamp}.jpg"
    fpath = img_dir / fname
    fpath.write_bytes(data)
    md5   = hashlib.md5(data).hexdigest()
    rel   = f"images/{folder}/{date}/{fname}"
    print(f"      ✓ {rel}  ({len(data)//1024} KB)")
    return {"status": "captured", "notes": "",
            "filepath": rel, "size": len(data), "md5": md5}


async def run_one_interval(results_accumulator):
    """Open browser, capture all cameras, close browser, push images."""
    from playwright.async_api import async_playwright
    from collections import defaultdict

    ts_utc = datetime.now(timezone.utc)
    ts_ct  = ts_utc.astimezone(CT)
    print(f"\n  {'─'*54}")
    print(f"  Interval: {ts_ct.strftime('%Y-%m-%d %H:%M:%S CT')}")

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
                result = await capture_one(page, camera, ts_ct, ts_utc)
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
            w.writerow([
                ts_ct.strftime("%Y-%m-%d %H:%M:%S CT"),
                ts_utc.strftime("%Y-%m-%d %H:%M:%S UTC"),
                district, camera["search"], camera["folder"],
                r["filepath"], r["size"], r["md5"],
                r["status"], r["notes"],
            ])

    captured = sum(1 for _, r in interval_results if r["status"] == "captured")
    print(f"\n    Captured: {captured}/{len(CAMERAS)} cameras")

    # Push to GitHub after every interval
    git_push(f"Capture {ts_utc.strftime('%Y-%m-%d %H:%M UTC')} "
             f"({captured}/{len(CAMERAS)} cameras)")

    results_accumulator.extend(interval_results)


async def main():
    # Install browser
    subprocess.run(
        [sys.executable, "-m", "playwright", "install",
         "chromium", "--with-deps"], check=True
    )

    job_start    = time.monotonic()
    interval_n   = 0
    all_results  = []

    ts_start = datetime.now(timezone.utc).astimezone(CT)
    print(f"\n{'═'*56}")
    print(f"  TxDOT Camera Capture — 5-Minute Loop")
    print(f"  Started : {ts_start.strftime('%Y-%m-%d %H:%M:%S CT')}")
    print(f"  Cameras : {len(CAMERAS)}")
    print(f"  Interval: every {INTERVAL//60} minutes")
    print(f"  Job runs for ~6 hours then auto-relaunches")
    print(f"{'═'*56}")

    while time.monotonic() - job_start < JOB_DURATION:
        interval_n += 1
        elapsed_h = (time.monotonic() - job_start) / 3600
        print(f"\n  Interval #{interval_n}  "
              f"(job elapsed: {elapsed_h:.1f}h / 6h)")

        tick = time.monotonic()
        try:
            await run_one_interval(all_results)
        except Exception as e:
            print(f"  Interval error: {e}")

        # Sleep for the remainder of the 5-minute interval
        elapsed = time.monotonic() - tick
        sleep_s = max(0, INTERVAL - elapsed)

        # Stop looping if we're close to the job time limit
        if time.monotonic() - job_start + sleep_s + 180 > JOB_DURATION:
            print(f"\n  Approaching 6-hour job limit — stopping loop.")
            print(f"  Workflow will relaunch for the next 6-hour window.")
            break

        next_ct = datetime.now(timezone.utc).astimezone(CT) + timedelta(seconds=sleep_s)
        print(f"\n  Sleeping {sleep_s:.0f}s → next capture at "
              f"{next_ct.strftime('%H:%M:%S CT')}")
        time.sleep(sleep_s)

    captured_total = sum(1 for _, r in all_results if r["status"] == "captured")
    print(f"\n{'═'*56}")
    print(f"  Job complete: {interval_n} intervals, "
          f"{captured_total} images captured")
    print(f"{'═'*56}")


if __name__ == "__main__":
    asyncio.run(main())
