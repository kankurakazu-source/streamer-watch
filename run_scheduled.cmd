@echo off
REM Gajige scheduled runner (ASCII only - .cmd is read in the system codepage).
REM Runs article_generator.py (collect -> write article -> publish HTML -> build X post -> email),
REM then commits the generated site content and pushes to main so Cloudflare Pages auto-deploys.
REM Output (stdout/stderr) is saved to output\scheduled_<timestamp>.log
chcp 65001 >nul

cd /d C:\ClaudeCode\streamer_watch

REM locale-independent sortable timestamp (yyyyMMdd_HHmmss)
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set TS=%%i
set LOG=output\scheduled_%TS%.log

echo === scheduled run start: %DATE% %TIME% ===> "%LOG%"
".venv\Scripts\python.exe" article_generator.py >> "%LOG%" 2>&1

echo. >> "%LOG%"
echo --- auto-deploy: git add/commit/push --- >> "%LOG%"
git add site data/history.sqlite3 >> "%LOG%" 2>&1
git commit -m "auto: scheduled article %TS%" >> "%LOG%" 2>&1
if %ERRORLEVEL%==0 (
  git push origin main >> "%LOG%" 2>&1
  echo push exit=%ERRORLEVEL% >> "%LOG%"
) else (
  echo [INFO] nothing to commit ^(no new article^) - skip push >> "%LOG%"
)

echo === scheduled run end: %DATE% %TIME% === >> "%LOG%"
