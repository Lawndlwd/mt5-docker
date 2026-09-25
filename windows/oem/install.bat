@echo off
REM Run once by dockur/windows at the end of the unattended Windows install
REM (as the auto-logged-in admin user). Output goes to C:\OEM\install.log.
powershell -NoProfile -ExecutionPolicy Bypass -File "C:\OEM\install.ps1"
