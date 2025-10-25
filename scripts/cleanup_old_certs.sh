#!/bin/bash

echo "=== Очистка старых сертификатов ==="

# Переходим в корень проекта
cd "$(dirname "$0")/.." || exit 1

# Удаляем старые самоподписанные сертификаты
echo "Удаляем старые .pem файлы..."
rm -vf *.pem
rm -vf certs/*.pem
rm -vrf certs/

echo ""
echo "=== Старые сертификаты удалены ==="
echo ""
echo "Выберите один из вариантов:"
echo ""
echo "1. Автоматическая генерация при запуске:"
echo "   python p2p.py --config config/coordinator.yaml"
echo "   При запуске сертификаты будут созданы автоматически с CA"
echo ""
echo "2. Ручная генерация сейчас:"
echo "   ./scripts/generate_ca_certs.sh"
echo "   Сгенерирует CA и сертификаты для coordinator и worker"
echo ""
