#!/bin/bash

echo "=== Очистка старых сертификатов ==="

# Переходим в корень проекта
cd "$(dirname "$0")/.." || exit 1

# Удаляем старые сертификаты (и .pem и .cer/.key форматы)
echo "Удаляем старые сертификаты..."
rm -vf *.pem *.cer *.key
rm -vf certs/*.pem certs/*.cer certs/*.key
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
