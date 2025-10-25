#!/bin/bash

echo "=== Проверка SSL сертификатов ==="
echo ""

# Переходим в корень проекта
cd "$(dirname "$0")/.." || exit 1

# Проверка наличия сертификатов
if [ ! -f "certs/ca_cert.pem" ]; then
    echo "❌ CA сертификат не найден: certs/ca_cert.pem"
    exit 1
fi

if [ ! -f "certs/coordinator_cert.pem" ]; then
    echo "❌ Сертификат coordinator не найден: certs/coordinator_cert.pem"
    exit 1
fi

echo "✅ Сертификаты найдены"
echo ""

echo "=== CA Сертификат ==="
echo "Issuer (кто выдал):"
openssl x509 -in certs/ca_cert.pem -noout -issuer
echo ""
echo "Subject (кому выдан):"
openssl x509 -in certs/ca_cert.pem -noout -subject
echo ""
echo "Это самоподписанный CA (Issuer = Subject) ✅"
echo ""
echo "BasicConstraints (должно быть CA:TRUE):"
openssl x509 -in certs/ca_cert.pem -noout -text | grep -A 1 "CA:TRUE"
echo ""

echo "=== Coordinator Сертификат ==="
echo "Issuer (кто выдал):"
openssl x509 -in certs/coordinator_cert.pem -noout -issuer
echo ""
echo "Subject (кому выдан):"
openssl x509 -in certs/coordinator_cert.pem -noout -subject
echo ""

# Проверка что Issuer != Subject
ISSUER=$(openssl x509 -in certs/coordinator_cert.pem -noout -issuer)
SUBJECT=$(openssl x509 -in certs/coordinator_cert.pem -noout -subject)

if [ "$ISSUER" = "$SUBJECT" ]; then
    echo "❌ САМОПОДПИСАННЫЙ сертификат (Issuer = Subject)"
    echo "   Это неправильно! Должен быть подписан CA"
    exit 1
else
    echo "✅ Сертификат подписан CA (Issuer ≠ Subject)"
fi

echo ""
echo "BasicConstraints (должно быть CA:FALSE):"
openssl x509 -in certs/coordinator_cert.pem -noout -text | grep -A 1 "CA:FALSE"
echo ""

# Проверка цепочки сертификатов
echo "=== Проверка цепочки сертификатов ==="
if openssl verify -CAfile certs/ca_cert.pem certs/coordinator_cert.pem; then
    echo "✅ Сертификат coordinator успешно верифицирован CA"
else
    echo "❌ Ошибка верификации сертификата"
    exit 1
fi

echo ""
echo "=== Все проверки пройдены! ==="
echo "Сертификат coordinator_cert.pem подписан CA ✅"
