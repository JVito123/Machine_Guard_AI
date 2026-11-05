@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" launch.py %*
  goto selesai
)
where py >nul 2>nul
if not errorlevel 1 (
  py -3 launch.py %*
  goto selesai
)
where python >nul 2>nul
if not errorlevel 1 (
  python launch.py %*
  goto selesai
)
echo Python tidak ditemukan. Pasang Python 3.10 atau lebih baru, lalu jalankan file ini lagi.
:selesai
pause
