#!/bin/bash
# Быстрый запуск сборки P2P Core через PyInstaller

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_ROOT" || exit 1

echo "🔧 P2P Core - PyInstaller Build"
echo "================================"
echo ""

# Проверяем наличие Python и PyInstaller
if ! command -v python3 &> /dev/null; then
    echo "❌ Python3 не найден. Установите Python 3.7+"
    exit 1
fi

if ! python3 -c "import PyInstaller" 2>/dev/null; then
    echo "⚠️  PyInstaller не установлен. Устанавливаю..."
    pip install pyinstaller
fi

# Запускаем build скрипт с переданными аргументами
python3 scripts/build_p2p.py "$@"
