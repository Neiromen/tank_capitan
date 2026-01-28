@echo off
if exist .venv\Scripts\activate.bat call .venv\Scripts\activate.bat
pip install pyinstaller --quiet
python -m PyInstaller --clean -y build.spec
if errorlevel 1 (
    echo Build failed
    exit /b 1
)
echo Done: dist\TankVoice.exe
pause
