@echo off
setlocal DisableDelayedExpansion
pushd "%~dp0" || exit /b 1
set "_fanuc_gui_python=%~dp0.venv\Scripts\pythonw.exe"
if defined FANUC_GUI_PYTHON set "_fanuc_gui_python=%FANUC_GUI_PYTHON%"
if not exist "%_fanuc_gui_python%" (
    echo Desktop Python not found. Run Install.cmd first, or set FANUC_GUI_PYTHON to your desktop environment's absolute pythonw.exe path.
    popd
    exit /b 1
)
start "" "%_fanuc_gui_python%" -B -m fanuc_workbench.app %*
set "_fanuc_exit=%errorlevel%"
popd
exit /b %_fanuc_exit%
