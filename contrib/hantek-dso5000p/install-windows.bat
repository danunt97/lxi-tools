@echo off
chcp 65001 >nul
echo === Hantek DSO5000P Tool - Installation ===
echo.
where python >nul 2>nul
if errorlevel 1 (
    echo Python wurde nicht gefunden.
    echo Bitte Python von https://www.python.org/downloads/ installieren
    echo und beim Setup "Add python.exe to PATH" anhaken. Danach diese Datei erneut starten.
    pause
    exit /b 1
)
python -m pip install --upgrade pyusb libusb-package matplotlib
if errorlevel 1 (
    echo Installation fehlgeschlagen, siehe Meldungen oben.
    pause
    exit /b 1
)
echo.
echo Fertig. Jetzt noch den USB-Treiber mit Zadig auf WinUSB umstellen (siehe README),
echo danach "start-windows.bat" doppelklicken.
pause
