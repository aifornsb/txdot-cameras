"""
TxDOT Camera Capture + NTE Toll Collector — IH35E @ Valley Ridge
=================================================================
Every 5 minutes:
  1. Fetches current toll prices from NTE / NTE 35W DMS signs
  2. Captures one image from IH35E @ Valley Ridge (Dallas)
  3. Writes both into summary.csv (one row per NTE sign per snapshot)
  4. Commits and pushes everything to GitHub
"""

import asyncio, base64, csv, hashlib, json, os, re, subprocess, sys, time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

CT           = timezone(timedelta(hours=-5))
INTERVAL     = 5 * 60
JOB_DURATION = 5 * 3600 + 50 * 60

# ── Cameras (unchanged) ───────────────────────────────────────────────────────

CAMERAS = [
    {
        "portal": "https://its.txdot.gov/its/District/DAL/cameras",
        "search": "IH35E @ Valley Ridge",
        "folder": "DAL-IH35E-Valley-Ridge",
    },
]

# ── Toll configuration ────────────────────────────────────────────────────────

TOLL_API_URL   = ("https://its.txdot.gov/its/DistrictIts/GetDmsListByDistrict"
                  "?districtCode={district}")
TOLL_DISTRICTS = ["DAL", "FTW"]
NTE_ROADWAYS   = {"IH35W", "LP820", "SH183", "SH121"}
NTE_KEYWORDS   = ["NTE", "TEXPRESS", "EXPRESS LANE", "MANAGED LANE",
                  "NORTH TARRANT", "35W", "LOOP 820"]
TOLL_PATTERN   = re.compile(r"\$?\s*(\d{1,2}\.\d{2})")
DIRECTION_MAP  = {1:"NB",2:"NEB",3:"EB",4:"SEB",5:"SB",6:"SWB",7:"WB",8:"NWB"}

TOLL_SESSION = requests.Session()
TOLL_SESSION.headers.update({
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"),
    "Accept":  "application/json, text/plain, */*",
    "Referer": "https://its.txdot.gov/its/District/DAL/dms-messages",
    "Origin":  "https://its.txdot.gov",
})
_cookie_file = Path("browser_cookies.json")
if _cookie_file.exists():
    for c in json.loads(_cookie_file.read_text()):
        TOLL_SESSION.cookies.set(c["name"], c["value"],
                                 domain=c.get("domain", "its.txdot.gov"))

# ── Toll helpers ──────────────────────────────────────────────────────────────

def _extract_message(sign):
    for f in ("messageText","message","dmsMessage","text","currentMessage"):
        v = sign.get(f)
        if v and isinstance(v, str) and v.strip():
            return v.strip()
        if v and isinstance(v, list):
            return " | ".join(str(x) for x in v if x)
    for container in ("phases","pages","lines"):
        items = sign.get(container)
        if items and isinstance(items, list):
            parts = []
            for item in items:
                if isinstance(item, dict):
                    for k in ("text","message","line"):
                        if item.get(k): parts.append(str(item[k]))
                elif isinstance(item, str):
                    parts.append(item)
            if parts:
                return " | ".join(parts)
    return ""

def _is_nte(roadway_key, sign):
    if roadway_key.upper() in NTE_ROADWAYS:
        return True
    text = " ".join([str(sign.get("name","")), str(sign.get("description","")),
                     str(sign.get("location","")), _extract_message(sign)]).upper()
    return any(k.upper() in text for k in NTE_KEYWORDS)

def collect_toll_prices():
    results = []
    for district in TOLL_DISTRICTS:
        url = TOLL_API_URL.format(district=district)
        try:
            resp = TOLL_SESSION.get(url, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"   [toll] {district} error: {e}")
            continue
        dir_lookup = {}
        for r in data.get("dmsRoadways", []):
            dir_lookup[r.get("name","")] = DIRECTION_MAP.get(
                r.get("direction"), r.get("directionDescription",""))
        for roadway_key, signs in data.get("roadwayDmses", {}).items():
            direction = dir_lookup.get(roadway_key, "")
            for sign in signs:
                if not _is_nte(roadway_key, sign):
                    continue
                msg    = _extract_message(sign)
                prices = TOLL_PATTERN.findall(msg)
                results.append({
                    "toll_roadway":     roadway_key,
                    "toll_direction":   direction,
                    "toll_sign_id":     str(sign.get("dmsId") or sign.get("id") or ""),
                    "toll_sign_name":   str(sign.get("name") or sign.get("description") or ""),
                    "toll_price":       prices[0] if prices else "",
                    "toll_all_prices":  ",".join(prices),
                    "toll_message_raw": msg,
                })
    if results:
        prices_found = [r["toll_price"] for r in results if r["toll_price"]]
        print(f"   [toll] {len(results)} NTE signs | prices: {prices_found or 'none parsed'}")
    else:
        print("   [toll] 0 NTE signs (cookie expired or API unreachable)")
    return results

# ── Git push (unchanged) ──────────────────────────────────────────────────────

def git_push(message: str):
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
        print("   (no new files to commit)")
        return
    subprocess.run(["git", "commit", "-m", message], check=False)
    result = subprocess.run(["git", "push"], check=False)
    if result.returncode != 0:
        subprocess.run(["git", "fetch", "origin", "main"], check=False)
        subprocess.run(["git", "reset", "--soft", "origin/main"], check=False)
        subprocess.run(["git", "add", "images/", "summary.csv"], check=False)
        subprocess.run(["git", "commit", "--amend", "--no-edit"], check=False)
        subprocess.run(["git", "push", "--force-with-lease"], check=False)

# ── Camera capture (unchanged) ────────────────────────────────────────────────

async def capture_one(page, camera: dict, ts_ct, ts_utc) -> dict:
    search  = camera["search"]
    folder  = camera["folder"]
    stamp   = ts_ct.strftime("%Y%m%d_%H%M%S")
    date    = ts_ct.strftime("%Y-%m-%d")
    img_dir = Path(f"images/{folder}/{date}")
    img_dir.mkdir(parents=True, exist_ok=True)

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

    clicked = False
    for locator in [
        page.locator(f'div[id="{search}"]').first,
        page.get_by_role("link", name=search, exact=True).first,
        page.locator(".cctv-list-item").first,
    ]:
        if clicked: break
        try:
            await locator.click(timeout=3_000)
            await page.wait_for_timeout(4_000)
            clicked = True
        except Exception:
            pass

    if not clicked:
        return {"status":"error","notes":"could not click camera result",
                "filepath":"","size":0,"md5":""}

    data = None
    try:
        b64 = await page.evaluate("""(search) => {
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
        return {"status":"error","notes":"no image data","filepath":"","size":0,"md5":""}

    fname = f"{folder}_{stamp}.jpg"
    fpath = img_dir / fname
    fpath.write_bytes(data)
    md5 = hashlib.md5(data).hexdigest()
    rel  = f"images/{folder}/{date}/{fname}"
    print(f"   ✓ {fname} ({len(data)//1024} KB) md5={md5[:8]}")
    return {"status":"captured","notes":"","filepath":rel,"size":len(data),"md5":md5}

# ── CSV (extended with toll columns) ─────────────────────────────────────────

CSV_FIELDS = [
    # original camera columns
    "timestamp_ct","timestamp_utc","district",
    "camera_name","folder","filepath","size_bytes","md5","status","notes",
    # new toll columns
    "toll_collected_at",
    "toll_roadway","toll_direction","toll_sign_id","toll_sign_name",
    "toll_price","toll_all_prices","toll_message_raw",
]

def write_csv_rows(ts_ct, ts_utc, district, camera, cam_result, toll_rows):
    csv_path   = Path("summary.csv")
    new_file   = not csv_path.exists()
    base = {
        "timestamp_ct":      ts_ct.strftime("%Y-%m-%d %H:%M:%S CT"),
        "timestamp_utc":     ts_utc.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "district":          district,
        "camera_name":       camera["search"],
        "folder":            camera["folder"],
        "filepath":          cam_result["filepath"],
        "size_bytes":        cam_result["size"],
        "md5":               cam_result["md5"],
        "status":            cam_result["status"],
        "notes":             cam_result["notes"],
        "toll_collected_at": ts_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    rows_to_write = toll_rows if toll_rows else [{}]
    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if new_file:
            w.writeheader()
        for t in rows_to_write:
            w.writerow({**base,
                "toll_roadway":     t.get("toll_roadway",""),
                "toll_direction":   t.get("toll_direction",""),
                "toll_sign_id":     t.get("toll_sign_id",""),
                "toll_sign_name":   t.get("toll_sign_name",""),
                "toll_price":       t.get("toll_price",""),
                "toll_all_prices":  t.get("toll_all_prices",""),
                "toll_message_raw": t.get("toll_message_raw",""),
            })

# ── Interval loop ─────────────────────────────────────────────────────────────

async def run_one_interval(results_accumulator):
    from playwright.async_api import async_playwright
    from collections import defaultdict

    ts_utc = datetime.now(timezone.utc)
    ts_ct  = ts_utc.astimezone(CT)
    print(f"\n {'─'*54}")
    print(f" {ts_ct.strftime('%Y-%m-%d %H:%M:%S CT')}")

    # 1. Toll prices (fast, ~1 s, no browser needed)
    print("   [toll] fetching NTE DMS prices …")
    toll_rows = collect_toll_prices()

    # 2. Camera capture (Playwright)
    by_portal = defaultdict(list)
    for cam in CAMERAS:
        by_portal[cam["portal"]].append(cam)

    interval_results = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox","--disable-dev-shm-usage","--disable-gpu"],
        )
        ctx = await browser.new_context(
            viewport={"width":1280,"height":900},
            user_agent=("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                        "Chrome/122.0.0.0 Safari/537.36"),
        )
        await ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        page = await ctx.new_page()

        for portal_url, cameras in by_portal.items():
            district = portal_url.split("/District/")[1].split("/")[0]
            print(f"   [camera] {district}")
            try:
                await page.goto(portal_url, wait_until="domcontentloaded", timeout=45_000)
            except Exception as e:
                print(f"   goto error: {e}")
            await page.wait_for_timeout(9_000)

            for camera in cameras:
                print(f"   [camera] {camera['search']}")
                try:
                    result = await capture_one(page, camera, ts_ct, ts_utc)
                except Exception as e:
                    print(f"   Error: {e}")
                    result = {"status":"error","notes":str(e)[:80],
                              "filepath":"","size":0,"md5":""}

                # 3. Write CSV: camera result joined with every NTE sign
                write_csv_rows(ts_ct, ts_utc, district, camera, result, toll_rows)
                interval_results.append((camera, result))

        await browser.close()

    captured = sum(1 for _, r in interval_results if r["status"] == "captured")
    print(f"   Result: {captured}/{len(CAMERAS)} images | {len(toll_rows)} NTE signs")

    git_push(f"IH35E Valley Ridge + toll — {ts_utc.strftime('%Y-%m-%d %H:%M UTC')}")
    results_accumulator.extend(interval_results)

# ── Entry point ───────────────────────────────────────────────────────────────

async def main():
    subprocess.run([sys.executable, "-m", "playwright", "install",
                    "chromium", "--with-deps"], check=True)

    job_start   = time.monotonic()
    interval_n  = 0
    all_results = []

    ts_start = datetime.now(timezone.utc).astimezone(CT)
    print(f"\n{'═'*56}")
    print(f" IH35E @ Valley Ridge — Camera + Toll Collector")
    print(f" Started : {ts_start.strftime('%Y-%m-%d %H:%M:%S CT')}")
    print(f" Interval: every {INTERVAL//60} minutes")
    print(f"{'═'*56}")

    while time.monotonic() - job_start < JOB_DURATION:
        interval_n += 1
        elapsed_h = (time.monotonic() - job_start) / 3600
        print(f"\n Interval #{interval_n}  ({elapsed_h:.1f}h / 6h)")

        tick = time.monotonic()
        try:
            await run_one_interval(all_results)
        except Exception as e:
            print(f" Interval error: {e}")

        elapsed   = time.monotonic() - tick
        sleep_s   = max(0, INTERVAL - elapsed)
        time_left = JOB_DURATION - (time.monotonic() - job_start)

        if time_left < sleep_s + 180:
            print("\n Approaching 6h limit — stopping cleanly.")
            break

        next_ct = datetime.now(timezone.utc).astimezone(CT) + timedelta(seconds=sleep_s)
        print(f"\n Next at {next_ct.strftime('%H:%M:%S CT')} (in {sleep_s:.0f}s)")
        time.sleep(sleep_s)

    total = sum(1 for _, r in all_results if r["status"] == "captured")
    print(f"\n{'═'*56}")
    print(f" Done: {interval_n} intervals, {total} images captured")
    print(f"{'═'*56}")

if __name__ == "__main__":
    asyncio.run(main())
