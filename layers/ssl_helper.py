"""
Вспомогательные функции для HTTPS/SSL с самоподписанными сертификатами
"""

import os
import ssl
import logging
from pathlib import Path
from typing import Optional, Tuple
from datetime import datetime, timedelta


logger = logging.getLogger("SSL")


def generate_self_signed_cert(
    cert_file: str,
    key_file: str,
    common_name: str = "P2P Node",
    days_valid: int = 3650
) -> bool:
    """
    Генерация самоподписанного SSL сертификата

    Args:
        cert_file: путь к файлу сертификата
        key_file: путь к файлу приватного ключа
        common_name: Common Name для сертификата
        days_valid: количество дней действия (по умолчанию 10 лет)

    Returns:
        True если успешно сгенерирован
    """
    try:
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.backends import default_backend

        # Генерация приватного ключа
        private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
            backend=default_backend()
        )

        # Создание сертификата
        subject = issuer = x509.Name([
            x509.NameAttribute(NameOID.COUNTRY_NAME, "RU"),
            x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "Moscow"),
            x509.NameAttribute(NameOID.LOCALITY_NAME, "Moscow"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "P2P Network"),
            x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, "IT"),
            x509.NameAttribute(NameOID.COMMON_NAME, common_name),
        ])

        cert = x509.CertificateBuilder().subject_name(
            subject
        ).issuer_name(
            issuer
        ).public_key(
            private_key.public_key()
        ).serial_number(
            x509.random_serial_number()
        ).not_valid_before(
            datetime.utcnow()
        ).not_valid_after(
            datetime.utcnow() + timedelta(days=days_valid)
        ).add_extension(
            x509.SubjectAlternativeName([
                x509.DNSName("localhost"),
                x509.DNSName("*.local"),
                x509.IPAddress(__import__('ipaddress').IPv4Address("127.0.0.1")),
            ]),
            critical=False,
        ).sign(private_key, hashes.SHA256(), default_backend())

        # Сохранение приватного ключа
        with open(key_file, "wb") as f:
            f.write(private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption()
            ))

        # Сохранение сертификата
        with open(cert_file, "wb") as f:
            f.write(cert.public_bytes(serialization.Encoding.PEM))

        logger.info(f"Generated self-signed certificate: {cert_file}")
        logger.info(f"Private key saved to: {key_file}")
        logger.info(f"Certificate valid for {days_valid} days")

        return True

    except ImportError:
        logger.error("cryptography package not installed. Install with: pip install cryptography")
        return False
    except Exception as e:
        logger.error(f"Failed to generate certificate: {e}")
        return False


def ensure_certificates_exist(
    cert_file: str,
    key_file: str,
    common_name: str = "P2P Node"
) -> bool:
    """
    Проверить наличие сертификатов и создать если отсутствуют

    Args:
        cert_file: путь к файлу сертификата
        key_file: путь к файлу приватного ключа
        common_name: Common Name для сертификата

    Returns:
        True если сертификаты доступны
    """
    cert_path = Path(cert_file)
    key_path = Path(key_file)

    if cert_path.exists() and key_path.exists():
        logger.info(f"SSL certificates found: {cert_file}, {key_file}")
        return True

    logger.warning(f"SSL certificates not found, generating new ones...")
    return generate_self_signed_cert(cert_file, key_file, common_name)


def create_ssl_context(
    cert_file: str,
    key_file: str,
    verify_mode: bool = False
) -> Optional[ssl.SSLContext]:
    """
    Создать SSL контекст для HTTPS сервера

    Args:
        cert_file: путь к файлу сертификата
        key_file: путь к файлу приватного ключа
        verify_mode: проверять ли клиентские сертификаты

    Returns:
        SSLContext или None при ошибке
    """
    try:
        # Проверяем наличие файлов
        if not ensure_certificates_exist(cert_file, key_file):
            return None

        # Создание SSL контекста
        ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_context.load_cert_chain(cert_file, key_file)

        if verify_mode:
            ssl_context.verify_mode = ssl.CERT_REQUIRED
        else:
            ssl_context.verify_mode = ssl.CERT_NONE

        # Безопасные настройки
        ssl_context.minimum_version = ssl.TLSVersion.TLSv1_2
        ssl_context.set_ciphers('ECDHE+AESGCM:ECDHE+CHACHA20:DHE+AESGCM:DHE+CHACHA20:!aNULL:!MD5:!DSS')

        logger.info("SSL context created successfully")
        return ssl_context

    except Exception as e:
        logger.error(f"Failed to create SSL context: {e}")
        return None


def create_client_ssl_context(verify: bool = False) -> ssl.SSLContext:
    """
    Создать SSL контекст для HTTPS клиента

    Args:
        verify: проверять ли серверный сертификат

    Returns:
        SSLContext для клиента
    """
    ssl_context = ssl.create_default_context()

    if not verify:
        # Для самоподписанных сертификатов
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        logger.debug("Client SSL context created (verification disabled)")
    else:
        logger.debug("Client SSL context created (verification enabled)")

    return ssl_context


def get_certificate_info(cert_file: str) -> Optional[dict]:
    """
    Получить информацию о сертификате

    Args:
        cert_file: путь к файлу сертификата

    Returns:
        Словарь с информацией о сертификате или None
    """
    try:
        from cryptography import x509
        from cryptography.hazmat.backends import default_backend

        with open(cert_file, 'rb') as f:
            cert_data = f.read()

        cert = x509.load_pem_x509_certificate(cert_data, default_backend())

        return {
            "subject": cert.subject.rfc4514_string(),
            "issuer": cert.issuer.rfc4514_string(),
            "serial_number": cert.serial_number,
            "not_valid_before": cert.not_valid_before.isoformat(),
            "not_valid_after": cert.not_valid_after.isoformat(),
            "is_valid": datetime.utcnow() < cert.not_valid_after,
            "days_until_expiry": (cert.not_valid_after - datetime.utcnow()).days
        }

    except Exception as e:
        logger.error(f"Failed to read certificate info: {e}")
        return None
