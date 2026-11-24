@echo off
setlocal
call "%~dp0flow-studio\open-amax.cmd" %*
exit /b %errorlevel%
