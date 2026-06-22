#!/usr/bin/env python3
"""
Scraper tiket pesawat tiket.com
Jadwal  : Jalankan manual via Claude
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
DAYS_AHEAD = list(range(4, 89, 7))
MAX_RETRY  = 3
OUTPUT_DIR = Path(__file__).parent / "hasil_scraping"

# Urutan & nama kolom final (dipakai untuk CSV bertahap maupun Excel akhir)
COL_ORDER = [
    "scrape_date", "scrape_time", "destination", "h_plus", "travel_date",
    "airline", "depTime", "arrTime", "depCode", "arrCode", "duration", "price", "priceNum",
]
RENAME = {
    "depTime": "jam_berangkat", "arrTime": "jam_tiba",
    "depCode": "bandara_asal",  "arrCode": "bandara_tujuan",
    "price"  : "harga_display", "priceNum": "harga_angka",
}


def to_final_df(flights: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(flights)
    df = df[[c for c in COL_ORDER if c in df.columns]]
    return df.rename(columns=RENAME)


def append_csv(flights: list[dict], csv_path: Path) -> None:
    """Tulis hasil satu rute/tanggal ke CSV segera (append) — anti kehilangan data."""
    if not flights:
        return
    df = to_final_df(flights)
    header = not csv_path.exists()
    df.to_csv(csv_path, mode="a", header=header, index=False, encoding="utf-8-sig")


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
    await page.goto(url, wait_until="domcontentloaded", timeout=60_000)

    try:
        await page.wait_for_selector('[class*="FlightCard_card__"]', timeout=30_000)
    except Exception:
        raise RuntimeError(f"Tidak ada hasil: {dest_code} {date_str}")

    for _ in range(5):
        await page.keyboard.press("End")
        await page.wait_for_timeout(800)

    flights = await page.evaluate("""
        () => {
            // tiket.com memakai CSS-module hashed: cocokkan PREFIX class yang stabil
            // (suffix hash mis. __FFuMp berubah tiap deploy, jadi jangan diandalkan).
            const cards = document.querySelectorAll('[class*="FlightCard_card__"]');
            const results = [];
            const reTime = /^\\d{1,2}:\\d{2}$/;
            const reCode = /^[A-Z]{3}$/;

            cards.forEach(card => {
                const leaves = [];
                card.querySelectorAll('*').forEach(e => {
                    if (e.children.length === 0) {
                        const t = (e.textContent || '').trim();
                        if (t) leaves.push({ c: (typeof e.className === 'string' ? e.className : ''), t });
                    }
                });
                const has = (l, p) => l.c.includes(p);

                // Maskapai: utamakan img alt, fallback ke teks b2 (bukan lowEmphasis/regular/kode)
                let airline = card.querySelector('img[alt]')?.alt?.trim() || '';
                if (!airline) {
                    const a = leaves.find(l => has(l, 'Text_size_b2__')
                        && !has(l, 'Text_variant_lowEmphasis__') && !has(l, 'Text_weight_regular__')
                        && !reCode.test(l.t) && l.t.length > 2);
                    airline = a ? a.t : '';
                }

                // Jam: h3 bold berformat HH:MM
                const times = leaves
                    .filter(l => has(l, 'Text_size_h3__') && has(l, 'Text_weight_bold__') && reTime.test(l.t))
                    .map(l => l.t);

                // Kode bandara: b2 regular berformat 3 huruf kapital
                const codes = leaves
                    .filter(l => has(l, 'Text_size_b2__') && has(l, 'Text_weight_regular__')
                        && !has(l, 'Text_variant_lowEmphasis__') && reCode.test(l.t))
                    .map(l => l.t);

                // Durasi + status transit → gabung jadi "1j 30m Langsung"
                const durEl  = leaves.find(l => has(l, 'Text_variant_lowEmphasis__') && /^\\d+j/.test(l.t));
                const stopEl = leaves.find(l => has(l, 'Text_variant_lowEmphasis__') && /langsung|transit/i.test(l.t));
                const duration = [durEl ? durEl.t : '', stopEl ? stopEl.t : ''].filter(Boolean).join(' ').trim();

                // Harga: elemen variant_price (harga utama, sudah termasuk "IDR ...")
                let price = '', priceNum = null;
                const priceEl = leaves.find(l => has(l, 'Text_variant_price__'));
                if (priceEl) {
                    price = priceEl.t;
                    priceNum = parseInt(price.replace(/[^\\d]/g, ''), 10) || null;
                }

                if (times[0] && price) {
                    results.push({
                        airline,
                        depTime  : times[0] || '',
                        arrTime  : times[1] || '',
                        depCode  : codes[0] || '',
                        arrCode  : codes[1] || '',
                        duration,
                        price, priceNum,
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
    for attempt in range(1, MAX_RETRY + 1):
        try:
            flights = await scrape_page(page, url, dest_code, date_str, days)
            return flights, True
        except Exception:
            if attempt < MAX_RETRY:
                print(f"⚠ retry {attempt}/{MAX_RETRY-1}... ", end="", flush=True)
                await asyncio.sleep(3 * attempt)
            else:
                return [], False
    return [], False


async def main():
    # Tanggal acuan: default hari ini, bisa di-override via argumen (format YYYY-MM-DD)
    # Contoh: python scrape_tiket.py 2026-06-01
    if len(sys.argv) > 1:
        today = datetime.strptime(sys.argv[1], "%Y-%m-%d")
        print(f"\n📌 Tanggal acuan di-set manual: {today.strftime('%A, %d %B %Y')}")
    else:
        today = datetime.now()

    print(f"\n{'='*60}")
    print(f"  Scraping tiket.com — {today.strftime('%A, %d %B %Y %H:%M')}")
    print(f"  Tanggal target: {len(DAYS_AHEAD)} Jumat (H+4 s/d H+88)")
    print(f"{'='*60}\n")

    OUTPUT_DIR.mkdir(exist_ok=True)
    all_flights: list[dict] = []
    results_log: list[dict] = []

    ts         = today.strftime("%y%m%d")
    excel_path = OUTPUT_DIR / f"{ts}-Scrap Tiket Pesawat.xlsx"
    csv_path   = OUTPUT_DIR / f"{ts}-Scrap Tiket Pesawat.csv"

    # Resume: kalau CSV hari ini sudah ada (run sebelumnya terputus), lewati
    # kombinasi rute+tanggal yang sudah berhasil di-scrape.
    done: set[tuple[str, str]] = set()
    if csv_path.exists():
        try:
            prev = pd.read_csv(csv_path, encoding="utf-8-sig")
            if {"destination", "travel_date"}.issubset(prev.columns):
                done = set(zip(prev["destination"].astype(str), prev["travel_date"].astype(str)))
                print(f"♻  Melanjutkan run sebelumnya — {len(done)} tanggal sudah ada, akan dilewati.\n")
        except Exception:
            pass

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox"],
        )

        for dest_code, dest_info in ROUTES.items():
            print(f"🛫  Jakarta → {dest_code} ({dest_info['label']})")

            for days in DAYS_AHEAD:
                travel_date = today + timedelta(days=days)
                date_str    = travel_date.strftime("%Y-%m-%d")
                url         = build_url(dest_info, date_str)

                if (dest_code, date_str) in done:
                    print(f"    📅 H+{days:2d}  ({date_str}) ... ⏭  sudah ada (skip)")
                    results_log.append({
                        "destination": dest_code, "h_plus": f"H+{days}",
                        "travel_date": date_str, "status": "⏭ SKIP", "jumlah": 0,
                    })
                    continue

                print(f"    📅 H+{days:2d}  ({date_str}) ... ", end="", flush=True)

                # Context baru tiap request — fingerprint berubah, anti rate-limit
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

                flights, ok = await scrape_with_retry(page, url, dest_code, date_str, days)

                await ctx.close()   # tutup context setiap selesai

                if ok:
                    all_flights.extend(flights)
                    append_csv(flights, csv_path)   # simpan segera per tanggal
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

                # Delay random lebih panjang antar request (10-18 detik)
                await asyncio.sleep(random.uniform(10, 18))

            print()

        await browser.close()

    # Summary
    total  = len(results_log)
    sukses = sum(1 for r in results_log if "OK" in r["status"])
    gagal  = total - sukses

    print(f"\n{'='*60}")
    print(f"  SUMMARY")
    print(f"{'='*60}")
    print(f"  Total  : {total} | ✅ Sukses: {sukses} | ❌ Gagal: {gagal}")
    if gagal:
        for r in results_log:
            if "GAGAL" in r["status"]:
                print(f"    - {r['destination']} {r['h_plus']} ({r['travel_date']})")
    print(f"{'='*60}\n")

    if not csv_path.exists():
        print("⚠  Tidak ada data yang berhasil di-scrape.")
        sys.exit(1)

    # Excel dibangun dari CSV bertahap — selalu lengkap, termasuk hasil resume
    df = pd.read_csv(csv_path, encoding="utf-8-sig")
    df.to_excel(excel_path, index=False, sheet_name="Semua Rute")

    print(f"✅  Total data : {len(df)} baris")
    print(f"📊  Excel      : {excel_path}")
    print(f"📄  CSV        : {csv_path}\n")

    if gagal > 0:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
