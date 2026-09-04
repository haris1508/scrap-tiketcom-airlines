#!/usr/bin/env python3
"""
Hitung rata-rata harga tiket per bulan dari seluruh data scraping
Filter: domestik & direct flight saja
"""

import re
from pathlib import Path
from datetime import datetime

import pandas as pd

DIR = Path(__file__).parent / "hasil_scraping"

# Destinasi domestik
DOMESTIK = {"BPN", "DPS", "KNO", "MES", "PKU", "SUB", "UPG", "YIA", "JOG"}

# Mapping nama bandara ke kota (untuk normalisasi)
NORMALISASI = {"MES": "KNO", "JOG": "YIA"}

# Jarak great-circle (km) Jakarta (CGK) → bandara tujuan, untuk hitung Rp/km
JARAK_KM = {"BPN": 1258, "DPS": 983, "KNO": 1387, "PKU": 933,
            "SUB": 691, "UPG": 1432, "YIA": 424}


def parse_traveloka(path: Path) -> pd.DataFrame:
    """Parse format CSV Traveloka — auto-detect separator & format tanggal."""
    # Auto-detect separator dengan baca 1 baris pertama
    with open(path, encoding="utf-8") as fh:
        head = fh.readline()
    sep = ";" if head.count(";") > head.count(",") else ","

    df = pd.read_csv(path, sep=sep, encoding="utf-8", on_bad_lines="skip")
    if not {"Date", "Price", "Schedule"}.issubset(df.columns):
        return pd.DataFrame()
    if df.empty:
        return pd.DataFrame()

    df["harga"] = (
        df["Price"].astype(str)
        .str.replace(r"[^\d]", "", regex=True)
        .replace("", "0").astype(int)
    )
    df["destinasi"] = df["Schedule"].astype(str).str[-3:].str.upper()
    df["direct"]    = df["Schedule"].astype(str).str.contains("Direct", case=False, na=False)

    # Coba beberapa format tanggal
    td = pd.to_datetime(df["Date"], format="%d/%m/%y", errors="coerce")
    td = td.fillna(pd.to_datetime(df["Date"], format="%d-%m-%Y", errors="coerce"))
    td = td.fillna(pd.to_datetime(df["Date"], format="%Y-%m-%d", errors="coerce"))
    td = td.fillna(pd.to_datetime(df["Date"], dayfirst=True, errors="coerce"))
    df["travel_date"] = td

    return df[["travel_date", "destinasi", "direct", "harga"]]


def parse_tiketcom(path: Path) -> pd.DataFrame:
    """Parse format CSV tiket.com (comma)."""
    df = pd.read_csv(path, encoding="utf-8-sig", on_bad_lines="skip")
    if "destination" not in df.columns:
        return pd.DataFrame()

    df = df.rename(columns={
        "destination": "destinasi",
        "harga_angka": "harga",
    })

    # Direct flight: tergantung kolom yang ada
    if "duration" in df.columns:
        df["direct"] = df["duration"].astype(str).str.contains("Langsung", case=False, na=False)
    else:
        # Format historis ringkas — sudah pre-filtered direct flight
        df["direct"] = True

    # Travel date: dari kolom langsung, atau hitung dari scrape_date + h_plus
    if "travel_date" in df.columns:
        df["travel_date"] = pd.to_datetime(df["travel_date"], errors="coerce")
    elif "scrape_date" in df.columns and "h_plus" in df.columns:
        sd     = pd.to_datetime(df["scrape_date"], errors="coerce")
        days   = pd.to_numeric(df["h_plus"].astype(str).str.replace(r"[^\d]", "", regex=True), errors="coerce")
        df["travel_date"] = sd + pd.to_timedelta(days, unit="D")
    else:
        df["travel_date"] = pd.NaT

    return df[["travel_date", "destinasi", "direct", "harga"]]


# -----------------------------------------------------------------------------
# Load semua file
# -----------------------------------------------------------------------------
frames = []
for f in sorted(DIR.glob("*.csv")):
    try:
        if "traveloka" in f.name.lower():
            df = parse_traveloka(f)
            src = "traveloka"
        else:
            df = parse_tiketcom(f)
            src = "tiket.com"
        if not df.empty:
            df["source"] = src
            df["file"]   = f.name
            frames.append(df)
    except Exception as e:
        print(f"⚠  Gagal baca {f.name}: {e}")

if not frames:
    print("Tidak ada data!")
    raise SystemExit(1)

df = pd.concat(frames, ignore_index=True)
print(f"Total baris mentah     : {len(df):,}")

# -----------------------------------------------------------------------------
# Bersihkan & filter
# -----------------------------------------------------------------------------
df = df.dropna(subset=["travel_date", "destinasi", "harga"])
df["destinasi"] = df["destinasi"].astype(str).str.upper().replace(NORMALISASI)

# Filter: domestik + direct (tanpa filter harga)
df = df[df["destinasi"].isin({"BPN", "DPS", "KNO", "PKU", "SUB", "UPG", "YIA"})]
df = df[df["direct"] == True]
df = df[df["harga"] > 0]   # buang baris harga 0/kosong saja

print(f"Setelah filter         : {len(df):,}  (domestik + direct)")

# -----------------------------------------------------------------------------
# Group by bulan (travel_date) & destinasi
# -----------------------------------------------------------------------------
df["bulan"] = df["travel_date"].dt.to_period("M").astype(str)

# Pivot: bulan vs destinasi → rata-rata harga
pivot = df.pivot_table(
    index="bulan",
    columns="destinasi",
    values="harga",
    aggfunc="mean",
).round(0).astype("Int64")

# Tambah kolom rata-rata keseluruhan per bulan
pivot["RATA_RATA_SEMUA"] = pivot.mean(axis=1).round(0).astype("Int64")

# Tambah baris jumlah data per bulan
jumlah_per_bulan = df.groupby("bulan").size().rename("JUMLAH_DATA")

# Long format juga untuk referensi
long_avg = (
    df.groupby(["bulan", "destinasi"])
    .agg(rata_rata_harga=("harga", "mean"),
         minimum_harga =("harga", "min"),
         maksimum_harga=("harga", "max"),
         jumlah_data   =("harga", "size"))
    .round(0).astype({"rata_rata_harga":"Int64","minimum_harga":"Int64","maksimum_harga":"Int64"})
    .reset_index()
)

# Rp/km flight-level (sadar-volume): tiap penerbangan = harga ÷ jarak rute,
# lalu dirata-rata per bulan (rute ramai berpengaruh lebih besar)
df["rp_per_km"] = df["harga"] / df["destinasi"].map(JARAK_KM)
rpkm_bulan = (
    df.dropna(subset=["rp_per_km"])
    .groupby("bulan")
    .agg(rata_rata_rp_per_km=("rp_per_km", "mean"),
         jumlah_data        =("rp_per_km", "size"))
    .round({"rata_rata_rp_per_km": 0})
    .astype({"rata_rata_rp_per_km": "Int64"})
    .reset_index()
)

# -----------------------------------------------------------------------------
# Output
# -----------------------------------------------------------------------------
ts          = datetime.now().strftime("%y%m%d")
excel_path  = DIR / f"{ts}-Rata-rata Harga per Bulan.xlsx"
csv_path    = DIR / f"{ts}-Rata-rata Harga per Bulan.csv"

with pd.ExcelWriter(excel_path, engine="openpyxl") as w:
    pivot.to_excel(w, sheet_name="Pivot Bulan x Rute")
    long_avg.to_excel(w, sheet_name="Detail per Bulan-Rute", index=False)
    jumlah_per_bulan.to_excel(w, sheet_name="Jumlah Data per Bulan")
    rpkm_bulan.to_excel(w, sheet_name="Rp per km per Bulan", index=False)

long_avg.to_csv(csv_path, index=False, encoding="utf-8-sig")
rpkm_csv = DIR / f"{ts}-Rp per km per Bulan.csv"
rpkm_bulan.to_csv(rpkm_csv, index=False, encoding="utf-8-sig")

print(f"\n📊 Pivot tabel (Rp):")
print(pivot.to_string())
print(f"\n📏 Rata-rata Rp/km per bulan (sadar-volume):")
print(rpkm_bulan.tail(12).to_string(index=False))
print(f"\n✅ Excel : {excel_path}")
print(f"✅ CSV   : {csv_path}")
