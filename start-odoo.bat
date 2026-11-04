@echo off
REM Start PostgreSQL (portable) jika belum jalan
"C:\odoo-dev\pgsql\bin\pg_ctl.exe" -D "C:\odoo-dev\pgdata" status >nul 2>&1
if errorlevel 1 "C:\odoo-dev\pgsql\bin\pg_ctl.exe" -D "C:\odoo-dev\pgdata" -l "C:\odoo-dev\pgdata\postgres.log" start

REM Start Odoo 17 (pakai Python di venv)
"C:\odoo-dev\odoo\venv\Scripts\python.exe" "C:\odoo-dev\odoo\odoo-bin" -c "C:\odoo-dev\odoo.conf" -d odoo17 --logfile= %*
