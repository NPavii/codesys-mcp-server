@echo off
rem Generated-style runner kept for manual use (the MCP server writes its own copy).
if "%CODESYS_EXE%"=="" set "CODESYS_EXE=C:\Program Files\CODESYS\CODESYS\Common\CODESYS.exe"
if "%CODESYS_PROFILE%"=="" set "CODESYS_PROFILE=CODESYS V3.5 SP21"
"%CODESYS_EXE%" --noUI --profile="%CODESYS_PROFILE%" --runscript="%~dp0bridge_daemon.py"
