@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Install dependencies first. See README.md.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m streamlit run streamlit_demo.py --server.address 127.0.0.1
pause
