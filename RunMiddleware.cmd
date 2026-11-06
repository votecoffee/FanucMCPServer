@echo off
setlocal DisableDelayedExpansion
if not defined FANUC_CLI_CONFIG if exist "%~dp0profile.json" set "FANUC_CLI_CONFIG=%~dp0profile.json"
if not defined FANUC_CLI_RESOURCE_DIR if exist "%~dp0profile.json" set "FANUC_CLI_RESOURCE_DIR=%~dp0..\Data\resources"
if defined FANUC_MIDDLEWARE_PYTHON goto configured
if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" -I -B "%~dp0scripts\fanuc_middleware.py" %*
    exit /b
)
where py.exe >nul 2>nul
if errorlevel 1 goto onpath
py.exe -3 -I -B "%~dp0scripts\fanuc_middleware.py" %*
exit /b %errorlevel%
:onpath
where python.exe >nul 2>nul
if errorlevel 1 goto missing
python.exe -I -B "%~dp0scripts\fanuc_middleware.py" %*
exit /b %errorlevel%
:configured
if not exist "%FANUC_MIDDLEWARE_PYTHON%" goto missing
"%FANUC_MIDDLEWARE_PYTHON%" -I -B "%~dp0scripts\fanuc_middleware.py" %*
exit /b %errorlevel%
:missing
echo {"ok":false,"error":{"code":"runtime_missing","message":"Set FANUC_MIDDLEWARE_PYTHON to a Python 3.11 or newer executable."}}
exit /b 69
