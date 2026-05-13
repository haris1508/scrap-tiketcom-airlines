#!/usr/bin/env python3
"""
Scraper tiket pesawat tiket.com
Jadwal: Setiap Senin — ambil harga H+4, H+11, H+32, H+60
Rute: Jakarta ke BPN, DPS, KNO, PKU, SUB, UPG, YIA, SIN, KUL
Output: CSV + Excel di folder hasil_scraping/
"""

import asyncio
import os
import re
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
from playwright.async_api import async_playwright

# ---------------------------------------------------------------------------
# Konfigurasi rute
# Format: "KODE_IATA": {"code": "kode_tiket", "type": "CITY|AIRPORT", "label": "..."}
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

# Setiap Jumat dari H+4 sampai ~3 bulan ke depan (H+4, H+11, ..., H+88)
DAYS_AHEAD = list(range(4, 89, 7))

OUTPUT_DIR = Path(__file__).parent / "hasil_scraping"


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

    # Tunggu card pertama muncul
    try:
        await page.wait_for_selector('[class*="FlightCard_card__"]', timeout=30_000)
    except Exception:
        print(f"    ⚠  Timeout / tidak ada hasil: {dest_code} {date_str}")
        return []

    # Scroll ke bawah agar semua card ter-load
    for _ in range(5):
        await page.keyboard.press("End")
        await page.wait_for_timeout(800)

    # Ekstrak data via JavaScript di dalam browser
    flights = await page.evaluate("""
        () => {
            const cards = document.querySelectorAll('[class*="FlightCard_card__"]');
            const results = [];

            cards.forEach(card => {
                // Nama maskapai — dari alt gambar atau teks fallback
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

                // Waktu departure & arrival
                const times = card.querySelectorAll('[class*="FlightCard_time_display"]');
                const depTime = times[0]?.textContent?.trim() || '';
                const arrTime = times[1]?.textContent?.trim() || '';

                // Kode bandara
                const airports = card.querySelectorAll('[class*="FlightCard_airport_code"]');
                const depCode = airports[0]?.textContent?.trim() || '';
                const arrCode = airports[1]?.textContent?.trim() || '';

                // Durasi & transit
                const durationRaw = card.querySelector('[class*="time_duration"]')?.textContent?.trim() || '';
                const duration = durationRaw.replace('Langsung', 'Langsung').replace('Singgah', 'Transit');

                // Harga — span paling dalam yang mengandung "IDR"
                let price = '';
                let priceNum = null;
                card.querySelectorAll('span').forEach(span => {
                    if (span.children.length === 0 && span.textContent.includes('IDR') && !price) {
                        price = span.textContent.trim();
                        priceNum = parseInt(price.replace(/[^\\d]/g, ''), 10) || null;
                    }
                });

                if (depTime && price) {
                    results.push({ airline, depTime, arrTime, depCode, arrCode, duration, price, priceNum });
                }
            });

            return results;
        }
    """)

    today_str = datetime.now().strftime("%Y-%m-%d")
    now_str   = datetime.now().strftime("%H:%M:%S")

    for f in flights:
        f["scrape_date"]   = today_str
        f["scrape_time"]   = now_str
        f["destination"]   = dest_code
        f["h_plus"]        = f"H+{days}"
        f["travel_date"]   = date_str

    return flights


async def main():
    today = datetime.now()
    print(f"\n{'='*60}")
    print(f"  Scraping tiket.com — {today.strftime('%A, %d %B %Y %H:%M')}")
    print(f"{'='*60}\n")

    OUTPUT_DIR.mkdir(exist_ok=True)
    all_flights: list[dict] = []

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
                try:
                    flights = await scrape_page(page, url, dest_code, date_str, days)
                    all_flights.extend(flights)
                    print(f"✅  {len(flights)} penerbangan")
                except Exception as exc:
                    print(f"❌  {exc}")

                await asyncio.sleep(2)   # jeda sopan antar request

            print()

        await browser.close()

    # -----------------------------------------------------------------------
    # Simpan hasil
    # -----------------------------------------------------------------------
    if not all_flights:
        print("⚠  Tidak ada data yang berhasil di-scrape.")
        return

    df = pd.DataFrame(all_flights)

    # Urutkan kolom
    col_order = [
        "scrape_date", "scrape_time",
        "destination", "h_plus", "travel_date",
        "airline", "depTime", "arrTime", "depCode", "arrCode",
        "duration", "price", "priceNum",
    ]
    df = df[[c for c in col_order if c in df.columns]]
    df = df.rename(columns={
        "depTime": "jam_berangkat",
        "arrTime": "jam_tiba",
        "depCode": "bandara_asal",
        "arrCode": "bandara_tujuan",
        "price":   "harga_display",
        "priceNum":"harga_angka",
    })

    ts = today.strftime("%y%m%d")
    excel_path = OUTPUT_DIR / f"{ts}-Scrap Tiket Pesawat.xlsx"

    with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Semua Rute")

        # Sheet terpisah per destinasi
        for dest in df["destination"].unique():
            sub = df[df["destination"] == dest]
            sub.to_excel(writer, index=False, sheet_name=dest)

    print(f"✅  Selesai!  Total data: {len(df)} baris")
    print(f"📊  Excel : {excel_path}\n")


if __name__ == "__main__":
    asyncio.run(main())
