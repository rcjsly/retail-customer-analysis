@echo off
cd /d "%~dp0"
python src/analyze.py
if errorlevel 1 goto failed
python src/enrich_analysis.py
if errorlevel 1 goto failed
python src/finalize_report.py
if errorlevel 1 goto failed
echo Analysis completed. Open results/dashboard.html
pause
exit /b 0
:failed
echo Analysis failed. Review errors above.
pause
exit /b 1
