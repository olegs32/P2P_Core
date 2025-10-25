#!/bin/bash
# Скрипт для генерации CA и подписанных SSL сертификатов для P2P узлов

set -e

# Цвета для вывода
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}============================================${NC}"
echo -e "${BLUE}  P2P Network - CA Certificate Generator  ${NC}"
echo -e "${BLUE}============================================${NC}"
echo ""

# Директория для сертификатов
CERTS_DIR="certs"
mkdir -p "$CERTS_DIR"

# Функция генерации CA
generate_ca() {
    echo -e "${YELLOW}Генерация Certificate Authority (CA)...${NC}"

    python3 << 'EOF'
from layers.ssl_helper import generate_ca_certificate

success = generate_ca_certificate(
    ca_cert_file="certs/ca_cert.cer",
    ca_key_file="certs/ca_key.key",
    common_name="P2P Network CA"
)

if success:
    print("✓ CA успешно создан")
else:
    print("✗ Ошибка создания CA")
    exit(1)
EOF

    echo -e "${GREEN}  ✓ CA Certificate: certs/ca_cert.cer${NC}"
    echo -e "${GREEN}  ✓ CA Private Key: certs/ca_key.key${NC}"
    echo ""
}

# Функция генерации подписанного сертификата
generate_signed_cert() {
    local name=$1
    local cn=$2

    echo -e "${YELLOW}Генерация сертификата для: ${name}${NC}"

    python3 << EOF
from layers.ssl_helper import generate_signed_certificate

success = generate_signed_certificate(
    cert_file="certs/${name}_cert.cer",
    key_file="certs/${name}_key.key",
    ca_cert_file="certs/ca_cert.cer",
    ca_key_file="certs/ca_key.key",
    common_name="${cn}",
    san_dns=["localhost", "*.local", "${name}"],
    san_ips=["127.0.0.1"]
)

if success:
    print("✓ Сертификат успешно создан")
else:
    print("✗ Ошибка создания сертификата")
    exit(1)
EOF

    echo -e "${GREEN}  ✓ Certificate: certs/${name}_cert.cer${NC}"
    echo -e "${GREEN}  ✓ Private Key: certs/${name}_key.key${NC}"
    echo ""
}

# Проверка наличия Python и необходимых библиотек
echo -e "${YELLOW}Проверка зависимостей...${NC}"
python3 -c "import cryptography" 2>/dev/null || {
    echo -e "${YELLOW}Установка cryptography...${NC}"
    pip3 install cryptography
}
echo -e "${GREEN}  ✓ Зависимости готовы${NC}"
echo ""

# Генерация CA
if [ ! -f "certs/ca_cert.cer" ] || [ ! -f "certs/ca_key.key" ]; then
    generate_ca
else
    echo -e "${GREEN}  ✓ CA уже существует${NC}"
    echo ""
fi

# Генерация сертификатов для узлов
echo -e "${BLUE}Генерация сертификатов для узлов:${NC}"
echo ""

# Coordinator
if [ ! -f "certs/coordinator_cert.cer" ]; then
    generate_signed_cert "coordinator" "coordinator-1"
else
    echo -e "${GREEN}  ✓ Сертификат coordinator уже существует${NC}"
    echo ""
fi

# Worker
if [ ! -f "certs/worker_cert.cer" ]; then
    generate_signed_cert "worker" "worker-1"
else
    echo -e "${GREEN}  ✓ Сертификат worker уже существует${NC}"
    echo ""
fi

echo -e "${BLUE}============================================${NC}"
echo -e "${GREEN}Все сертификаты успешно сгенерированы!${NC}"
echo -e "${BLUE}============================================${NC}"
echo ""
echo "Структура сертификатов:"
echo "  certs/"
echo "  ├── ca_cert.cer           (CA сертификат - распространять на все узлы)"
echo "  ├── ca_key.key            (CA ключ - ХРАНИТЬ В БЕЗОПАСНОСТИ!)"
echo "  ├── coordinator_cert.cer  (Сертификат координатора)"
echo "  ├── coordinator_key.key   (Ключ координатора)"
echo "  ├── worker_cert.cer       (Сертификат worker)"
echo "  └── worker_key.key        (Ключ worker)"
echo ""
echo -e "${YELLOW}ВАЖНО:${NC}"
echo "  1. CA сертификат (ca_cert.cer) должен быть скопирован на все узлы"
echo "  2. CA ключ (ca_key.key) храните в безопасности!"
echo "  3. Каждый узел использует свой cert/key + общий ca_cert.cer"
echo ""
echo -e "${GREEN}Сертификаты действительны:${NC}"
echo "  - CA: 10 лет"
echo "  - Node certificates: 1 год"
echo ""

# Показываем информацию о CA
echo -e "${BLUE}Информация о CA:${NC}"
openssl x509 -in certs/ca_cert.cer -noout -subject -issuer -dates 2>/dev/null || echo "  (используйте openssl для просмотра)"
echo ""
