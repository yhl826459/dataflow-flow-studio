@echo off
setlocal
python "%~dp0local_server.py" --open
if errorlevel 1 pause
