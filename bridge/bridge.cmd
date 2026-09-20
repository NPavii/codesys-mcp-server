@echo off
rem One-shot bridge run (cold start, ~20-90 s). Prefers env vars, falls back to defaults.
if "%CODESYS_EXE%"=="" set "CODESYS_EXE=C:\Program Files\CODESYS\CODESYS\Common\CODESYS.exe"
if "%CODESYS_PROFILE%"=="" set "CODESYS_PROFILE=CODESYS V3.5 SP21"
"%CODESYS_EXE%" --noUI --profile="%CODESYS_PROFILE%" --runscript="%~dp0bridge.py"
