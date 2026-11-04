@echo off
REM Hentikan semua proses Odoo (launcher venv + proses Python aslinya).
REM Dipakai kalau Ctrl+C di terminal start-odoo.bat tidak berhasil.
powershell -NoProfile -Command ^
  "$p = Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -like '*odoo-bin*' };" ^
  "if (-not $p) { Write-Host 'Odoo tidak sedang berjalan.'; exit 0 };" ^
  "$p | ForEach-Object { Write-Host ('Menghentikan PID ' + $_.ProcessId); Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue };" ^
  "Write-Host 'Odoo sudah berhenti.'"
