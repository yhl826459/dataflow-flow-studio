@echo off
setlocal
python -B "%~dp0open_amax.py" %*
set "result=%errorlevel%"
if not "%result%"=="0" pause
exit /b %result%
