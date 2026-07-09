@echo off
REM Gajige scheduled runner (ASCII only - .cmd is read in the system codepage).
REM Arg %1 = mode: "primary" (default) or "retry".
REM   primary: always generate 2 articles (2 categories).
REM   retry  : article_generator skips if a run already succeeded within the last 70 min
REM            (used by the +1h retry tasks so a recovered primary is not duplicated).
REM After generation, commits site content and pushes to main (Cloudflare Pages auto-deploy).
chcp 65001 >nul

cd /d C:\ClaudeCode\streamer_watch

set MODE=%1
if "%MODE%"=="" set MODE=primary

REM locale-independent sortable timestamp (yyyyMMdd_HHmmss)
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set TS=%%i
set LOG=output\scheduled_%TS%_%MODE%.log

echo === scheduled run start (%MODE%): %DATE% %TIME% ===> "%LOG%"
".venv\Scripts\python.exe" article_generator.py --count 2 --mode %MODE% >> "%LOG%" 2>&1

echo. >> "%LOG%"
echo --- auto-deploy: git add/commit/push --- >> "%LOG%"
git add site data/history.sqlite3 >> "%LOG%" 2>&1
git commit -m "auto: scheduled articles %TS% (%MODE%)" >> "%LOG%" 2>&1
if %ERRORLEVEL%==0 (
  git push origin main >> "%LOG%" 2>&1
  echo push exit=%ERRORLEVEL% >> "%LOG%"
) else (
  echo [INFO] nothing to commit - skip push >> "%LOG%"
)

echo === scheduled run end: %DATE% %TIME% === >> "%LOG%"
