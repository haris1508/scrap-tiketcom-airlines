#!/bin/bash
# Daftarkan cron job untuk scraping setiap Senin jam 08:00 WIB (01:00 UTC)
# Jalankan: bash jadwal_cron.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="$SCRIPT_DIR/.venv/bin/python"
SCRAPER="$SCRIPT_DIR/scrape_tiket.py"
LOG="$SCRIPT_DIR/scraper.log"

# Cron expression: setiap Senin (1) jam 08:00 WIB = 01:00 UTC
CRON_EXPR="0 1 * * 1"
CRON_CMD="$CRON_EXPR $PYTHON $SCRAPER >> $LOG 2>&1"

echo "📅 Mendaftarkan cron job..."
echo "   Jadwal : Setiap Senin jam 08:00 WIB"
echo "   Script : $SCRAPER"
echo "   Log    : $LOG"
echo ""

# Tambahkan ke crontab (hindari duplikat)
( crontab -l 2>/dev/null | grep -v "scrape_tiket.py" ; echo "$CRON_CMD" ) | crontab -

echo "✅ Cron job berhasil didaftarkan!"
echo ""
echo "Cek cron aktif:"
crontab -l | grep scrape_tiket
echo ""
echo "Hapus cron (jika perlu):"
echo "  crontab -l | grep -v scrape_tiket | crontab -"
