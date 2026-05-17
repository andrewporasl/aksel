@echo off
setlocal

where py >nul 2>nul
if %errorlevel%==0 (
    set "PYTHON_CMD=py -3"
) else (
    where python >nul 2>nul
    if %errorlevel%==0 (
        set "PYTHON_CMD=python"
    ) else (
        echo Python was not found. Install Python 3.10 or newer, then run this script again.
        exit /b 1
    )
)

%PYTHON_CMD% -m venv .venv
if not exist ".venv\Scripts\activate.bat" (
    echo Virtual environment was created, but the activation script was not found.
    exit /b 1
)

call ".venv\Scripts\activate.bat"
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

where ffmpeg >nul 2>nul
if %errorlevel%==0 (
    echo ffmpeg found.
) else (
    echo Warning: ffmpeg was not found on PATH. Install ffmpeg before running media commands.
)

if not exist ".env" (
    copy ".env.example" ".env" >nul
    echo Created .env from .env.example.
)

echo Setup complete. Edit .env with your Discord bot token, then run launch.bat.
endlocal
