@echo off
setlocal

if exist ".venv\Scripts\activate.bat" (
    call ".venv\Scripts\activate.bat"
)

if not exist ".env" (
    echo .env was not found. Run setup.bat, then add your Discord bot token to .env.
    exit /b 1
)

findstr /R /C:"^DISCORD_TOKEN=..*" ".env" >nul
if errorlevel 1 (
    echo DISCORD_TOKEN is missing in .env.
    exit /b 1
)

findstr /R /C:"^DISCORD_TOKEN=your_bot_token_here$" ".env" >nul
if not errorlevel 1 (
    echo DISCORD_TOKEN is still set to the placeholder in .env.
    exit /b 1
)

python bot.py
endlocal
