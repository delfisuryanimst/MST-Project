@echo off
REM Pemakaian:
REM   upgrade-module.bat                  -> upgrade quality_control, lalu start Odoo
REM   upgrade-module.bat nama_modul       -> upgrade modul lain
REM   upgrade-module.bat a,b              -> upgrade beberapa modul (pisah koma, tanpa spasi)
REM   upgrade-module.bat quality_control --dev=xml    -> argumen tambahan diteruskan ke Odoo
setlocal

set MODULE=%1
if "%MODULE%"=="" set MODULE=quality_control
shift

REM Pastikan PostgreSQL jalan
"C:\odoo-dev\pgsql\bin\pg_ctl.exe" -D "C:\odoo-dev\pgdata" status >nul 2>&1
if errorlevel 1 "C:\odoo-dev\pgsql\bin\pg_ctl.exe" -D "C:\odoo-dev\pgdata" -l "C:\odoo-dev\pgdata\postgres.log" start

REM Matikan Odoo yang sedang jalan di port 8069 (kalau ada)
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8069 " ^| findstr LISTENING') do taskkill /PID %%p /F >nul 2>&1

echo === Upgrade modul: %MODULE% ===
"C:\odoo-dev\odoo\venv\Scripts\python.exe" "C:\odoo-dev\odoo\odoo-bin" -c "C:\odoo-dev\odoo.conf" -d odoo17 -u %MODULE% --stop-after-init --logfile=
if errorlevel 1 (
    echo.
    echo *** Upgrade GAGAL, lihat error di atas. Odoo tidak dijalankan. ***
    exit /b 1
)

echo === Upgrade selesai, menjalankan Odoo ===
"C:\odoo-dev\odoo\venv\Scripts\python.exe" "C:\odoo-dev\odoo\odoo-bin" -c "C:\odoo-dev\odoo.conf" -d odoo17 --logfile= %2 %3 %4 %5 %6 %7 %8 %9
