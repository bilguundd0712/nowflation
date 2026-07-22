@echo off
REM refresh.bat - rebuild nowflation.mn from the live NSO weekly price survey.
REM
REM Standalone on purpose: this does NOT touch run_cockpit.bat. To fold it into the
REM daily cockpit run, add this one line to run_cockpit.bat after the mn_nowcast.py step:
REM     call "%%~dp0nowflation\refresh.bat"
REM
REM Exit code 0 = index.html rewritten. Non-zero = nothing was written and the previous
REM page is left in place, which is the correct behaviour: a stale page that says it is
REM stale beats a fresh-looking page built on a failed fetch.

setlocal
cd /d "%~dp0"

echo [nowflation] building from data.1212.mn ...
python build_nowflation.py
if errorlevel 1 (
  echo [nowflation] BUILD FAILED - index.html left untouched
  exit /b 1
)

echo [nowflation] ok - %~dp0index.html
endlocal
exit /b 0
