#!/usr/bin/env python3
"""
Scraper tiket pesawat tiket.com
Jadwal  : Setiap Senin jam 08:00 WIB (via GitHub Actions)
Data    : Setiap Jumat H+4 s/d H+88 (~3 bulan ke depan)
Rute    : Jakarta ke BPN, DPS, KNO, PKU, SUB, UPG, YIA, SIN, KUL
Output  : Excel di folder hasil_scraping/
"""

import asyncio
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
from playwright.async_api import async_playwright
from playwright_stealth import stealth_async

# ---------------------------------------------------------------------------
# Konfigurasi rute
# ---------------------------------------------------------------------------
ROUTES = {
    "BPN": {"code": "BPNC",  "type": "CITY",    "label": "Balikpapan"},
    "DPS": {"code": "DPSC",  "type": "CITY",    "label": "Denpasar-Bali"},
    "KNO": {"code": "KNO",   "type": "AIRPORT", "label": "Medan"},
    "PKU": {"code": "PKU",   "type": "AIRPORT", "label": "Pekanbaru"},
    "SUB": {"code": "SUBC",  "type": "CITY",    "label": "Surabaya"},
    "UPG": {"code": "UPGC",  "type": "CITY",    "label": "Makassar"},
    "YIA": {"code": "YIA",   "type": "AIRPORT", "label": "Yogyakarta"},
    "SIN": {"code": "SIN",   "type": "AIRPORT", "label": "Singapore"},
    "KUL": {"code": "KUL",   "type": "AIRPORT", "label": "Kuala-Lumpur"},
}

# Setiap Jumat: H+4, H+11, H+18, ..., H+88 (~3 bulan)
DAYS_AHEAD    = list(range(4, 89, 7))
MAX_RETRY     = 3       # maksimal percobaan per rute/tanggal
RESTART_EVERY = 8       # restart browser setiap N halaman
OUTPUT_DIR    = Path(__file__).parent / "hasil_scraping"

# Daftar user agent untuk dirotasi
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

VIEWPORTS = [
    {"width": 1440, "height": 900},
    {"width": 1920, "height": 1080},
    {"width": 1366, "height": 768},
    {"width": 1536, "height": 864},
]


def build_url(dest_info: dict, date_str: str) -> str:
    return (
        "https://www.tiket.com/id-id/flights/search"
        f"?d=JKTC&dType=CITY"
        f"&a={dest_info['code']}&aType={dest_info['type']}"
        f"&class=economy&adult=1&type=depart"
        f"&date={date_str}"
        f"&dLabel=Jakarta&aLabel={dest_info['label']}"
    )


async def new_context(browser):
    """Buat context baru dengan user agent & viewport random."""
    ua       = random.choice(USER_AGENTS)
    viewport = random.choice(VIEWPORTS)
    ctx = await browser.new_context(
        user_agent=ua,
        viewport=viewport,
        locale="id-ID",
        timezone_id="Asia/Jakarta",
        extra_http_headers={
            "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
            "sec-ch-ua-platform": '"Windows"',
        },
    )
    page = await ctx.new_page()
    await stealth_async(page)

    # Kunjungi homepage dulu agar terlihat seperti user biasa
    await page.goto("https://www.tiket.com/id-id/flights", wait_until="domcontentloaded", timeout=30_000)
    await page.wait_for_timeout(random.randint(2000, 4000))

    return ctx, page


async def scrape_page(page, url: str, dest_code: str, date_str: str, days: int) -> list[dict]:
    """Navigate ke URL dan ekstrak semua data penerbangan."""
    await page.goto(url, wait_until="domcontentloaded", timeout=60_000)

    try:
        await page.wait_for_selector('[class*="FlightCard_card__"]', timeout=35_000)
    except Exception:
        raise RuntimeError(f"Tidak ada hasil: {dest_code} {date_str}")

    # Scroll human-like
    for _ in range(5):
        await page.keyboard.press("End")
        await page.wait_for_timeout(random.randint(600, 1200))

    flights = await page.evaluate("""
        () => {
            const cards = document.querySelectorAll('[class*="FlightCard_card__"]');
            const results = [];

            cards.forEach(card => {
                let airline = card.querySelector('img[alt]')?.alt?.trim() || '';
                if (!airline) {
                    const spans = card.querySelectorAll('span');
                    for (const s of spans) {
                        const txt = s.textContent.trim();
                        if (txt && !txt.includes('IDR') && !txt.includes(':') &&
                            !txt.match(/^\\d/) && s.children.length === 0 && txt.length > 2) {
                            airline = txt;
                            break;
                        }
                    }
                }

                const times    = card.querySelectorAll('[class*="FlightCard_time_display"]');
                const airports = card.querySelectorAll('[class*="FlightCard_airport_code"]');
                const durationRaw = card.querySelector('[class*="time_duration"]')?.textContent?.trim() || '';

                let price = '', priceNum = null;
                card.querySelectorAll('span').forEach(span => {
                    if (span.children.length === 0 && span.textContent.includes('IDR') && !price) {
                        price    = span.textContent.trim();
                        priceNum = parseInt(price.replace(/[^\\d]/g, ''), 10) || null;
                    }
                });

                if (times[0]?.textContent?.trim() && price) {
                    results.push({
                        airline,
                        depTime  : times[0]?.textContent?.trim() || '',
                        arrTime  : times[1]?.textContent?.trim() || '',
                        depCode  : airports[0]?.textContent?.trim() || '',
                        arrCode  : airports[1]?.textContent?.trim() || '',
                        duration : durationRaw,
                        price,
                        priceNum,
                    });
                }
            });

            return results;
        }
    """)

    today_str = datetime.now().strftime("%Y-%m-%d")
    now_str   = datetime.now().strftime("%H:%M:%S")

    for f in flights:
        f["scrape_date"] = today_str
        f["scrape_time"] = now_str
        f["destination"] = dest_code
        f["h_plus"]      = f"H+{days}"
        f["travel_date"] = date_str

    return flights


async def scrape_with_retry(page, url, dest_code, date_str, days) -> tuple[list, bool]:
    """Coba scrape sampai MAX_RETRY kali. Return (data, sukses)."""
    for attempt in range(1, MAX_RETRY + 1):
        try:
            flights = await scrape_page(page, url, dest_code, date_str, days)
            return flights, True
        except Exception:
            if attempt < MAX_RETRY:
                wait = random.randint(5, 10) * attempt
                print(f"⚠ retry {attempt}/{MAX_RETRY-1} (tunggu {wait}s)... ", end="", flush=True)
                await asyncio.sleep(wait)
            else:
                return [], False
    return [], False


async def main():
    today = datetime.now()
    print(f"\n{'='*60}")
    print(f"  Scraping tiket.com — {today.strftime('%A, %d %B %Y %H:%M')}")
    print(f"  Tanggal target: {len(DAYS_AHEAD)} Jumat (H+4 s/d H+88)")
    print(f"{'='*60}\n")

    OUTPUT_DIR.mkdir(exist_ok=True)
    all_flights: list[dict] = []
    results_log: list[dict] = []
    page_count = 0

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ],
        )

        ctx, page = await new_context(browser)

        for dest_code, dest_info in ROUTES.items():
            print(f"🛫  Jakarta → {dest_code} ({dest_info['label']})")

            for days in DAYS_AHEAD:
                # Restart browser setiap RESTART_EVERY halaman
                if page_count > 0 and page_count % RESTART_EVERY == 0:
                    print(f"\n  🔄 Restart browser (halaman ke-{page_count})...")
                    await ctx.close()
                    await asyncio.sleep(random.randint(5, 10))
                    ctx, page = await new_context(browser)
                    print()

                travel_date = today + timedelta(days=days)
                date_str    = travel_date.strftime("%Y-%m-%d")
                url         = build_url(dest_info, date_str)

                print(f"    📅 H+{days:2d}  ({date_str}) ... ", end="", flush=True)

                flights, ok = await scrape_with_retry(page, url, dest_code, date_str, days)
                page_count += 1

                if ok:
                    all_flights.extend(flights)
                    print(f"✅  {len(flights)} penerbangan")
                else:
                    print(f"❌  GAGAL setelah {MAX_RETRY}x percobaan")

                results_log.append({
                    "destination": dest_code,
                    "h_plus"     : f"H+{days}",
                    "travel_date": date_str,
                    "status"     : "✅ OK" if ok else "❌ GAGAL",
                    "jumlah"     : len(flights),
                })

                # Delay random antar request (lebih human-like)
                await asyncio.sleep(random.uniform(4, 8))

            print()

        await ctx.close()
        await browser.close()

    # ---------------------------------------------------------------------------
    # Summary
    # ---------------------------------------------------------------------------
    total  = len(results_log)
    sukses = sum(1 for r in results_log if "OK" in r["status"])
    gagal  = total - sukses

    print(f"\n{'='*60}")
    print(f"  SUMMARY")
    print(f"{'='*60}")
    print(f"  Total kombinasi : {total}")
    print(f"  ✅  Sukses      : {sukses}")
    print(f"  ❌  Gagal       : {gagal}")
    if gagal:
        print(f"\n  Rute yang gagal:")
        for r in results_log:
            if "GAGAL" in r["status"]:
                print(f"    - {r['destination']} {r['h_plus']} ({r['travel_date']})")
    print(f"{'='*60}\n")

    # ---------------------------------------------------------------------------
    # Simpan hasil
    # ---------------------------------------------------------------------------
    if not all_flights:
        print("⚠  Tidak ada data yang berhasil di-scrape.")
        sys.exit(1)

    df = pd.DataFrame(all_flights)

    col_order = [
        "scrape_date", "scrape_time",
        "destination", "h_plus", "travel_date",
        "airline", "depTime", "arrTime", "depCode", "arrCode",
        "duration", "price", "priceNum",
    ]
    df = df[[c for c in col_order if c in df.columns]]
    df = df.rename(columns={
        "depTime" : "jam_berangkat",
        "arrTime" : "jam_tiba",
        "depCode" : "bandara_asal",
        "arrCode" : "bandara_tujuan",
        "price"   : "harga_display",
        "priceNum": "harga_angka",
    })

    ts         = today.strftime("%y%m%d")
    excel_path = OUTPUT_DIR / f"{ts}-Scrap Tiket Pesawat.xlsx"

    with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Semua Rute")
        for dest in df["destination"].unique():
            df[df["destination"] == dest].to_excel(writer, index=False, sheet_name=dest)
        pd.DataFrame(results_log).to_excel(writer, index=False, sheet_name="Summary")

    print(f"✅  Total data   : {len(df)} baris")
    print(f"📊  Excel        : {excel_path}\n")

    if gagal > 0:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
