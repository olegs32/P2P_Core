@echo off
REM Быстрый запуск сборки P2P Core через PyInstaller (Windows)

setlocal
set SCRIPT_DIR=%~dp0
set PROJECT_ROOT=%SCRIPT_DIR%..

cd /d "%PROJECT_ROOT%" || exit /b 1

echo 🔧 P2P Core - PyInstaller Build
echo ================================
echo.

REM Проверяем наличие Python
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo ❌ Python не найден. Установите Python 3.7+
    exit /b 1
)

REM Проверяем PyInstaller
python -c "import PyInstaller" 2>nul
if %errorlevel% neq 0 (
    echo ⚠️  PyInstaller не установлен. Устанавливаю...
    pip install pyinstaller
)

REM Запускаем build скрипт с переданными аргументами
python scripts\build_p2p.py %*
