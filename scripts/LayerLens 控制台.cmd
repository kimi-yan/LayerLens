@echo off
rem Opens the LayerLens control panel: start/stop the service and see its status.
start "" powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -STA -File "%~dp0control-panel.ps1"
