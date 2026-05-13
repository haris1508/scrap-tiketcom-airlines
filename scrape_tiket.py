#!/usr/bin/env python3
"""
Scraper tiket pesawat tiket.com
Jadwal  : Setiap Senin jam 08:00 WIB (via GitHub Actions)
Data    : Setiap Jumat H+4 s/d H+88 (~3 bulan ke depan)
Rute    : Jakarta ke BPN, DPS, KNO, PKU, SUB, UPG, YIA, SIN, KUL
Output  : Excel di folder hasil_scraping/
"""

import asyncio
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
from playwright.async_api import async_playwright

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
DAYS_AHEAD  = list(range(4, 89, 7))
MAX_RETRY   = 3          # maksimal percobaan per rute/tanggal
OUTPUT_DIR  = Path(__file__).parent / "hasil_scraping"


def build_url(dest_info: dict, date_str: str) -> str:
    return (
        "https://www.tiket.com/id-id/flights/search"
        f"?d=JKTC&dType=CITY"
        f"&a={dest_info['code']}&aType={dest_info['type']}"
        f"&class=economy&adult=1&type=depart"
        f"&date={date_str}"
        f"&dLabel=Jakarta&aLabel={dest_info['label']}"
    )


async def scrape_page(page, url: str, dest_code: str, date_str: str, days: int) -> list[dict]:
    """Navigate ke URL dan ekstrak semua data penerbangan."""
    await page.goto(url, wait_until="domcontentloaded", timeout=60_000)

    try:
        await page.wait_for_selector('[class*="FlightCard_card__"]', timeout=30_000)
    except Exception:
        raise RuntimeError(f"Tidak ada hasil: {dest_code} {date_str}")

    # Scroll agar semua card ter-load
    for _ in range(5):
        await page.keyboard.press("End")
        await page.wait_for_timeout(800)

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
        except Exception as exc:
            if attempt < MAX_RETRY:
                print(f"⚠ retry {attempt}/{MAX_RETRY-1}... ", end="", flush=True)
                await asyncio.sleep(3 * attempt)   # backoff
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

    # Tracking sukses/gagal
    results_log: list[dict] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox"],
        )
        ctx = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1440, "height": 900},
            locale="id-ID",
        )
        page = await ctx.new_page()

        for dest_code, dest_info in ROUTES.items():
            print(f"🛫  Jakarta → {dest_code} ({dest_info['label']})")

            for days in DAYS_AHEAD:
                travel_date = today + timedelta(days=days)
                date_str    = travel_date.strftime("%Y-%m-%d")
                url         = build_url(dest_info, date_str)

                print(f"    📅 H+{days:2d}  ({date_str}) ... ", end="", flush=True)

                flights, ok = await scrape_with_retry(page, url, dest_code, date_str, days)

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

                await asyncio.sleep(2)

            print()

        await browser.close()

    # ---------------------------------------------------------------------------
    # Summary
    # ---------------------------------------------------------------------------
    total      = len(results_log)
    sukses     = sum(1 for r in results_log if "OK" in r["status"])
    gagal      = total - sukses

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
        sys.exit(1)   # exit code 1 agar GitHub Actions tandai sebagai gagal

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

        # Sheet summary
        pd.DataFrame(results_log).to_excel(writer, index=False, sheet_name="Summary")

    print(f"✅  Total data   : {len(df)} baris")
    print(f"📊  Excel        : {excel_path}\n")

    # Exit code 1 kalau ada yang gagal, supaya GitHub Actions kirim notif
    if gagal > 0:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
