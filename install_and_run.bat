@echo off
call "%~dp0Install.cmd" --launch %*
exit /b %errorlevel%
