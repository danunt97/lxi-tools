@echo off
chcp 65001 >nul
cd /d "%~dp0"
:menu
cls
echo === Hantek DSO5102P ===
echo.
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
if "%wahl%"=="1" python dso5000p.py info
if "%wahl%"=="2" python dso5000p.py screenshot
if "%wahl%"=="3" python dso5000p.py capture
if "%wahl%"=="4" python dso5000p.py plot
if "%wahl%"=="5" python dso5000p.py live
if "%wahl%"=="6" python dso5000p.py monitor
if "%wahl%"=="7" python dso5000p.py key autoset
if "%wahl%"=="8" python dso5000p.py key runstop
if "%wahl%"=="0" exit /b 0
echo.
pause
goto menu
