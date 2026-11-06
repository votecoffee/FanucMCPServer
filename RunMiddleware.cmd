@echo off
setlocal DisableDelayedExpansion
if defined FANUC_MIDDLEWARE_PYTHON goto configured
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
