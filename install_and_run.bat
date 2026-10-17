@echo off
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
    py -3 -m venv .venv
)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip uninstall --yes fanuc-workbench
if errorlevel 1 exit /b 1
python -m pip install -e .
if errorlevel 1 exit /b 1
python run.py
endlocal
