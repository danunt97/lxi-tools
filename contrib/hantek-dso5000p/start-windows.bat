@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PY=python
if exist "%~dp0python-path.txt" set /p PY=<"%~dp0python-path.txt"
:menu
cls
echo === Hantek DSO5102P ===
echo.
echo  G  Grafische Oberflaeche starten
echo  1  Einstellungen anzeigen
echo  2  Screenshot speichern (PNG)
echo  3  Kurven als CSV speichern
echo  4  Kurve anzeigen
echo  5  Live-Ansicht
echo  6  Messwerte loggen (jede Sekunde, Strg+C beendet)
echo  7  AUTOSET druecken
echo  8  RUN/STOP druecken
echo  0  Beenden
echo.
set /p wahl=Auswahl: 
if /i "%wahl%"=="G" start "" "%PY%" dso5000p_gui.py
if "%wahl%"=="1" "%PY%" dso5000p.py info
if "%wahl%"=="2" "%PY%" dso5000p.py screenshot
if "%wahl%"=="3" "%PY%" dso5000p.py capture
if "%wahl%"=="4" "%PY%" dso5000p.py plot
if "%wahl%"=="5" "%PY%" dso5000p.py live
if "%wahl%"=="6" "%PY%" dso5000p.py monitor
if "%wahl%"=="7" "%PY%" dso5000p.py key autoset
if "%wahl%"=="8" "%PY%" dso5000p.py key runstop
if "%wahl%"=="0" exit /b 0
echo.
pause
goto menu
