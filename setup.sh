#!/bin/bash
# Setup environment untuk scraper tiket.com
# Jalankan sekali: bash setup.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "📦 Membuat virtual environment..."
python3 -m venv .venv

echo "📦 Install dependensi..."
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet playwright pandas openpyxl

echo "🎭 Install Playwright browsers (Chromium)..."
.venv/bin/playwright install chromium
.venv/bin/playwright install-deps chromium 2>/dev/null || true

echo ""
echo "✅ Setup selesai!"
echo ""
echo "Untuk test manual:"
echo "  .venv/bin/python scrape_tiket.py"
echo ""
echo "Untuk jadwal otomatis setiap Senin jam 08:00:"
echo "  bash jadwal_cron.sh"
