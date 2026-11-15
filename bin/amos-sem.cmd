@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\amos_pipeline.ps1" %*
exit /b %ERRORLEVEL%
