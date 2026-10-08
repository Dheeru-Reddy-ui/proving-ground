@echo off
rem Starts the Proving Ground device agent (docs/RUNBOOK.md).
rem It runs from the repository so the agent uses this checkout's SDK manifest, locator maps and .env.
cd /d "%~dp0\..\.."
uv run pg agent run
