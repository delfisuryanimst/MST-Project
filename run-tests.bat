@echo off
REM Run the unit tests of the MST modules in a separate database (odoo17_test), so odoo17 is not touched.
REM The first run creates that database (a few minutes); later runs only update the modules.
REM   run-tests.bat                          all modules
REM   run-tests.bat mst17_order_calculator   one module (or several, separated by commas)
setlocal enabledelayedexpansion
cd /d C:\odoo-dev

set MODULES=mst17_receipt_monitor,mst17_delivery_monitor,mst17_sales_delivery,mst17_order_calculator,mst17_sales_journey,mst17_stock_card
if not "%~1"=="" set MODULES=%~1
set TAGS=
for %%m in (%MODULES%) do set TAGS=!TAGS!,/%%m
set TAGS=%TAGS:~1%
set DB=odoo17_test
set LOG=C:\odoo-dev\test-results.log

REM Local stand-ins for live fields (allowed warehouses, SKU values) are installed in the test database only.
set PGPASSWORD=odoo
pgsql\bin\psql.exe -h localhost -U odoo -d %DB% -c "select 1" >nul 2>&1
if errorlevel 1 (
    echo Membuat database %DB% ...
    set ACTION=-i local_allowed_warehouse,local_sku_values,%MODULES%
) else (
    set ACTION=-u %MODULES%
)

if exist %LOG% del %LOG%
echo Menjalankan test: %MODULES%
odoo\venv\Scripts\python.exe odoo\odoo-bin -c odoo.conf -d %DB% !ACTION! --without-demo=all --test-enable --test-tags %TAGS% --stop-after-init --http-port=8071 --logfile=%LOG%

echo.
echo ===== Hasil (detail di %LOG%) =====
findstr /C:"tests.stats" /C:"FAIL: " /C:"ERROR: " /C:"tests.result" %LOG%
REM Keeps the window open after a double-click, so the result can be read.
pause
endlocal
