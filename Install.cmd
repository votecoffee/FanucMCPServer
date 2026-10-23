@echo off
setlocal
pushd "%~dp0" || exit /b 1
if defined FANUC_INSTALL_PYTHON (
    "%FANUC_INSTALL_PYTHON%" -B "%~dp0scripts\install_desktop.py" %*
) else (
    py -3 -B "%~dp0scripts\install_desktop.py" %*
)
set "_fanuc_exit=%errorlevel%"
popd
exit /b %_fanuc_exit%
