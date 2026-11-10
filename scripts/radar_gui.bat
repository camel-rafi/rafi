@echo off
rem RaFi 레이더 실시간 탐지 화면. 더블클릭하면 뜬다 (pip install -e ".[radar]" 먼저 한 번)
chcp 65001 >nul
cd /d "%~dp0.."
python -m rafi.radar.gui
if errorlevel 1 pause
