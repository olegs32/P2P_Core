"""
Вспомогательные функции для HTTPS/SSL с Certificate Authority (CA)
"""

import os
import ssl
import logging
from pathlib import Path
from typing import Optional, Tuple
from datetime import datetime, timedelta


logger = logging.getLogger("SSL")


def generate_ca_certificate(
    ca_cert_file: str,
    ca_key_file: str,
    common_name: str = "P2P Network CA",
    days_valid: int = 3650
) -> bool:
    """
    Генерация Certificate Authority (CA)

    Args:
        ca_cert_file: путь к файлу CA сертификата
        ca_key_file: путь к файлу CA приватного ключа
        common_name: Common Name для CA
        days_valid: количество дней действия (по умолчанию 10 лет)

    Returns:
        True если успешно сгенерирован
    """
    try:
        from cryptography import x509
        from cryptography.x509.oid import NameOID, ExtensionOID
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.backends import default_backend

        # Генерация приватного ключа CA
        ca_private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=4096,  # Больший размер для CA
            backend=default_backend()
        )

        # Создание CA сертификата
        subject = issuer = x509.Name([
            x509.NameAttribute(NameOID.COUNTRY_NAME, "RU"),
            x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "Moscow"),
            x509.NameAttribute(NameOID.LOCALITY_NAME, "Moscow"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "P2P Network"),
            x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, "Certificate Authority"),
            x509.NameAttribute(NameOID.COMMON_NAME, common_name),
        ])

        ca_cert = x509.CertificateBuilder().subject_name(
            subject
        ).issuer_name(
            issuer
        ).public_key(
            ca_private_key.public_key()
        ).serial_number(
            x509.random_serial_number()
        ).not_valid_before(
            datetime.utcnow()
        ).not_valid_after(
            datetime.utcnow() + timedelta(days=days_valid)
        ).add_extension(
            x509.BasicConstraints(ca=True, path_length=0),
            critical=True,
        ).add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_cert_sign=True,
                crl_sign=True,
                key_encipherment=False,
                content_commitment=False,
                data_encipherment=False,
                key_agreement=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        ).add_extension(
            x509.SubjectKeyIdentifier.from_public_key(ca_private_key.public_key()),
            critical=False,
        ).sign(ca_private_key, hashes.SHA256(), default_backend())

        # Сохранение CA приватного ключа
        with open(ca_key_file, "wb") as f:
            f.write(ca_private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption()
            ))

        # Сохранение CA сертификата
        with open(ca_cert_file, "wb") as f:
            f.write(ca_cert.public_bytes(serialization.Encoding.PEM))

        logger.info(f"Generated CA certificate: {ca_cert_file}")
        logger.info(f"CA private key saved to: {ca_key_file}")
        logger.info(f"CA valid for {days_valid} days")

        return True

    except ImportError:
        logger.error("cryptography package not installed. Install with: pip install cryptography")
        return False
    except Exception as e:
        logger.error(f"Failed to generate CA certificate: {e}")
        return False


def generate_signed_certificate(
    cert_file: str,
    key_file: str,
    ca_cert_file: str,
    ca_key_file: str,
    common_name: str,
    san_dns: list = None,
    san_ips: list = None,
    days_valid: int = 365
) -> bool:
    """
    Генерация сертификата подписанного CA

    Args:
        cert_file: путь к файлу сертификата
        key_file: путь к файлу приватного ключа
        ca_cert_file: путь к файлу CA сертификата
        ca_key_file: путь к файлу CA приватного ключа
        common_name: Common Name для сертификата
        san_dns: список DNS имен для SubjectAlternativeName
        san_ips: список IP адресов для SubjectAlternativeName
        days_valid: количество дней действия (по умолчанию 1 год)

    Returns:
        True если успешно сгенерирован
    """
    try:
        from cryptography import x509
        from cryptography.x509.oid import NameOID, ExtensionOID
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.backends import default_backend
        import ipaddress

        # Загрузка CA сертификата и ключа
        with open(ca_cert_file, "rb") as f:
            ca_cert_data = f.read()
            ca_cert = x509.load_pem_x509_certificate(ca_cert_data, default_backend())

        with open(ca_key_file, "rb") as f:
            ca_key_data = f.read()
            ca_private_key = serialization.load_pem_private_key(
                ca_key_data, password=None, backend=default_backend()
            )

        # Генерация приватного ключа для узла
        private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
            backend=default_backend()
        )

        # Создание субъекта
        subject = x509.Name([
            x509.NameAttribute(NameOID.COUNTRY_NAME, "RU"),
            x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "Moscow"),
            x509.NameAttribute(NameOID.LOCALITY_NAME, "Moscow"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "P2P Network"),
            x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, "IT"),
            x509.NameAttribute(NameOID.COMMON_NAME, common_name),
        ])

        # Подготовка SubjectAlternativeName
        san_list = []

        if san_dns:
            for dns in san_dns:
                san_list.append(x509.DNSName(dns))
        else:
            san_list.append(x509.DNSName("localhost"))
            san_list.append(x509.DNSName("*.local"))

        if san_ips:
            for ip in san_ips:
                san_list.append(x509.IPAddress(ipaddress.ip_address(ip)))
        else:
            san_list.append(x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")))

        # Создание сертификата
        cert = x509.CertificateBuilder().subject_name(
            subject
        ).issuer_name(
            ca_cert.subject
        ).public_key(
            private_key.public_key()
        ).serial_number(
            x509.random_serial_number()
        ).not_valid_before(
            datetime.utcnow()
        ).not_valid_after(
            datetime.utcnow() + timedelta(days=days_valid)
        ).add_extension(
            x509.SubjectAlternativeName(san_list),
            critical=False,
        ).add_extension(
            x509.BasicConstraints(ca=False, path_length=None),
            critical=True,
        ).add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_encipherment=True,
                key_cert_sign=False,
                crl_sign=False,
                content_commitment=False,
                data_encipherment=False,
                key_agreement=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        ).add_extension(
            x509.ExtendedKeyUsage([
                x509.oid.ExtendedKeyUsageOID.SERVER_AUTH,
                x509.oid.ExtendedKeyUsageOID.CLIENT_AUTH,
            ]),
            critical=False,
        ).sign(ca_private_key, hashes.SHA256(), default_backend())

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

        logger.info(f"Generated signed certificate: {cert_file}")
        logger.info(f"Private key saved to: {key_file}")
        logger.info(f"Certificate valid for {days_valid} days")
        logger.info(f"Signed by CA: {ca_cert_file}")

        return True

    except Exception as e:
        logger.error(f"Failed to generate signed certificate: {e}")
        return False


def generate_self_signed_cert(
    cert_file: str,
    key_file: str,
    common_name: str = "P2P Node",
    days_valid: int = 3650
) -> bool:
    """
    Генерация самоподписанного SSL сертификата (legacy, не рекомендуется)

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


def ensure_ca_exists(ca_cert_file: str, ca_key_file: str) -> bool:
    """
    Проверить наличие CA и создать если отсутствует

    Args:
        ca_cert_file: путь к файлу CA сертификата
        ca_key_file: путь к файлу CA приватного ключа

    Returns:
        True если CA доступен
    """
    ca_cert_path = Path(ca_cert_file)
    ca_key_path = Path(ca_key_file)

    if ca_cert_path.exists() and ca_key_path.exists():
        logger.info(f"CA certificate found: {ca_cert_file}")
        return True

    logger.warning("CA not found, generating new CA...")
    return generate_ca_certificate(ca_cert_file, ca_key_file)


def ensure_certificates_exist(
    cert_file: str,
    key_file: str,
    common_name: str = "P2P Node",
    ca_cert_file: str = None,
    ca_key_file: str = None
) -> bool:
    """
    Проверить наличие сертификатов и создать если отсутствуют

    Args:
        cert_file: путь к файлу сертификата
        key_file: путь к файлу приватного ключа
        common_name: Common Name для сертификата
        ca_cert_file: путь к CA сертификату (если используется CA)
        ca_key_file: путь к CA ключу (если используется CA)

    Returns:
        True если сертификаты доступны
    """
    cert_path = Path(cert_file)
    key_path = Path(key_file)

    if cert_path.exists() and key_path.exists():
        logger.info(f"SSL certificates found: {cert_file}, {key_file}")
        return True

    logger.warning(f"SSL certificates not found, generating new ones...")

    # Если указан CA, генерируем подписанный сертификат
    if ca_cert_file and ca_key_file:
        # Убедимся что CA существует
        if not ensure_ca_exists(ca_cert_file, ca_key_file):
            logger.error("Failed to ensure CA exists")
            return False

        return generate_signed_certificate(
            cert_file, key_file, ca_cert_file, ca_key_file, common_name
        )
    else:
        # Иначе генерируем самоподписанный
        return generate_self_signed_cert(cert_file, key_file, common_name)


def create_ssl_context(
    cert_file: str,
    key_file: str,
    verify_mode: bool = False,
    ca_cert_file: str = None
) -> Optional[ssl.SSLContext]:
    """
    Создать SSL контекст для HTTPS сервера

    Args:
        cert_file: путь к файлу сертификата
        key_file: путь к файлу приватного ключа
        verify_mode: проверять ли клиентские сертификаты
        ca_cert_file: путь к CA сертификату для верификации клиентов

    Returns:
        SSLContext или None при ошибке
    """
    try:
        # Проверяем наличие файлов
        if not Path(cert_file).exists() or not Path(key_file).exists():
            logger.error(f"Certificate files not found: {cert_file}, {key_file}")
            return None

        # Создание SSL контекста
        ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_context.load_cert_chain(cert_file, key_file)

        if verify_mode and ca_cert_file:
            ssl_context.verify_mode = ssl.CERT_REQUIRED
            ssl_context.load_verify_locations(cafile=ca_cert_file)
            logger.info(f"Client certificate verification enabled with CA: {ca_cert_file}")
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


def create_client_ssl_context(verify: bool = True, ca_cert_file: str = None) -> ssl.SSLContext:
    """
    Создать SSL контекст для HTTPS клиента

    Args:
        verify: проверять ли серверный сертификат
        ca_cert_file: путь к CA сертификату для верификации сервера

    Returns:
        SSLContext для клиента
    """
    ssl_context = ssl.create_default_context()

    if verify and ca_cert_file:
        # Загружаем CA сертификат для верификации
        ssl_context.load_verify_locations(cafile=ca_cert_file)
        ssl_context.check_hostname = True
        ssl_context.verify_mode = ssl.CERT_REQUIRED
        logger.debug(f"Client SSL context created with CA verification: {ca_cert_file}")
    elif not verify:
        # Отключаем верификацию (не рекомендуется для production)
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        logger.warning("Client SSL context created WITHOUT verification (insecure)")
    else:
        # Используем системные CA
        logger.debug("Client SSL context created with system CA verification")

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
            "days_until_expiry": (cert.not_valid_after - datetime.utcnow()).days,
            "is_ca": _is_ca_certificate(cert)
        }

    except Exception as e:
        logger.error(f"Failed to read certificate info: {e}")
        return None


def _is_ca_certificate(cert) -> bool:
    """Проверить является ли сертификат CA сертификатом"""
    try:
        from cryptography.x509.oid import ExtensionOID
        basic_constraints = cert.extensions.get_extension_for_oid(
            ExtensionOID.BASIC_CONSTRAINTS
        )
        return basic_constraints.value.ca
    except:
        return False
