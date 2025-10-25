#!/bin/bash
# Скрипт для генерации самоподписанных SSL сертификатов для P2P узлов

set -e

# Цвета для вывода
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${YELLOW}Генерация самоподписанных SSL сертификатов для P2P узлов${NC}"
echo ""

# Функция генерации сертификата
generate_cert() {
    local name=$1
    local cn=$2

    echo -e "${GREEN}Генерация сертификата для: ${name}${NC}"

    # Генерация приватного ключа
    openssl genrsa -out "${name}_key.pem" 2048

    # Генерация самоподписанного сертификата
    openssl req -new -x509 -key "${name}_key.pem" \
        -out "${name}_cert.pem" -days 3650 \
        -subj "/C=RU/ST=Moscow/L=Moscow/O=P2P Network/OU=IT/CN=${cn}"

    echo -e "${GREEN}  ✓ Создан ключ: ${name}_key.pem${NC}"
    echo -e "${GREEN}  ✓ Создан сертификат: ${name}_cert.pem${NC}"
    echo ""
}

# Генерация сертификатов для coordinator
generate_cert "coordinator" "coordinator-1"

# Генерация сертификатов для worker
generate_cert "worker" "worker-1"

echo -e "${GREEN}Все сертификаты успешно сгенерированы!${NC}"
echo ""
echo "Сертификаты действительны 10 лет (3650 дней)"
echo ""
echo "Файлы:"
echo "  - coordinator_cert.pem, coordinator_key.pem"
echo "  - worker_cert.pem, worker_key.pem"
echo ""
echo -e "${YELLOW}ВАЖНО: Храните ключи в безопасности!${NC}"
