@echo off
REM ===========================================================================
REM Wicked Warm - one-click launcher
REM Starts the Python forecast service and the Node web server, each in its own
REM window, then opens the app in your browser. Close those two windows to stop
REM the servers. Run this from anywhere by double-clicking it.
REM ===========================================================================

REM Always operate from this script's own folder so .env (with your API keys)
REM is found no matter where the launcher is invoked from.
cd /d "%~dp0"

echo.
echo  Wicked Warm - starting up...
echo  Project folder: %cd%
echo.

REM 1) Python forecast engine (port 8000) in its own window.
echo  [1/3] Starting the forecast engine (Python, port 8000)...
start "Wicked Warm - Python service" cmd /k "py -m energy_cost_predictor.service"

REM 2) Node web server (port 3000) in its own window.
echo  [2/3] Starting the web server (Node, port 3000)...
start "Wicked Warm - Node server" cmd /k "node src/server.js"

REM 3) Give both a few seconds to bind, then open the app.
echo  [3/3] Opening the app in your browser...
timeout /t 5 /nobreak >nul
start "" "http://127.0.0.1:3000/home/index.html"

echo.
echo  Done. Two server windows opened:
echo    - "Wicked Warm - Python service"
echo    - "Wicked Warm - Node server"
echo  Leave them OPEN while using the app. Close them to stop the servers.
echo.
echo  App URL:  http://127.0.0.1:3000/home/index.html
echo  Prices :  http://127.0.0.1:3000/price-tracker/price-tracker.html
echo.
pause
