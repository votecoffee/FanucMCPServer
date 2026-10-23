@echo off
setlocal DisableDelayedExpansion
pushd "%~dp0" || exit /b 1
if defined FANUC_INSTALL_PYTHON (
    "%FANUC_INSTALL_PYTHON%" -B "%~dp0scripts\install_plugin.py" %*
) else (
    py -3 -B "%~dp0scripts\install_plugin.py" %*
)
set "_fanuc_exit=%errorlevel%"
popd
exit /b %_fanuc_exit%
