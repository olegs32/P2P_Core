"""
Вспомогательные функции для HTTPS/SSL с Certificate Authority (CA)
"""

import os
import ssl
import logging
from pathlib import Path
from typing import Optional, Tuple
from datetime import datetime, timedelta
import io


logger = logging.getLogger("SSL")


# === Вспомогательные функции для работы с хранилищем ===

def _get_storage_manager(context=None):
    """
    Получить storage manager из контекста

    Args:
        context: P2PApplicationContext

    Returns:
        P2PStorageManager или None
    """
    if context is None:
        return None

    try:
        from layers.storage_manager import get_storage_manager
        return get_storage_manager(context)
    except:
        return None


def _read_cert_bytes(cert_file: str, context=None) -> Optional[bytes]:
    """
    Читать сертификат ТОЛЬКО из защищенного хранилища

    Args:
        cert_file: путь к сертификату (например, certs/ca_cert.cer)
        context: P2PApplicationContext для доступа к storage_manager

    Returns:
        байты сертификата или None

    Raises:
        RuntimeError: если хранилище недоступно или сертификат не найден
    """
    storage = _get_storage_manager(context)

    if not storage:
        import traceback
        print(traceback.format_exc())
        raise RuntimeError("Secure storage is not available - cannot read certificates")

    try:
        cert_name = Path(cert_file).name
        cert_data = storage.read_cert(cert_name)
        logger.debug(f"Certificate loaded from secure storage: {cert_name}")
        return cert_data
    except FileNotFoundError:
        logger.error(f"Certificate not found in secure storage: {cert_name}")
        return None
    except Exception as e:
        logger.error(f"Failed to read from secure storage: {e}")
        raise RuntimeError(f"Failed to read certificate from secure storage: {e}")


def _write_cert_bytes(cert_file: str, cert_data: bytes, context=None) -> tuple[bool, str]:
    """
    Записать сертификат ТОЛЬКО в защищенное хранилище

    Args:
        cert_file: путь к сертификату
        cert_data: байты сертификата
        context: P2PApplicationContext для доступа к storage_manager

    Returns:
        (success, location) - успешность и место сохранения ("storage")

    Raises:
        RuntimeError: если хранилище недоступно
    """
    storage = _get_storage_manager(context)

    if not storage:
        raise RuntimeError("Secure storage is not available - cannot write certificates")

    try:
        cert_name = Path(cert_file).name
        storage.write_cert(cert_name, cert_data)
        logger.debug(f"Certificate saved to secure storage: {cert_name}")
        return True, "storage"
    except Exception as e:
        logger.error(f"Failed to write to secure storage: {e}")
        raise RuntimeError(f"Failed to write certificate to secure storage: {e}")


def _cert_exists(cert_file: str, context=None) -> bool:
    """
    Проверить существование сертификата ТОЛЬКО в защищенном хранилище

    Args:
        cert_file: путь к сертификату
        context: P2PApplicationContext для доступа к storage_manager

    Returns:
        True если существует
    """
    storage = _get_storage_manager(context)


    if not storage:
        return False

    try:
        cert_name = Path(cert_file).name
        return storage.exists(f"certs/{cert_name}")
    except:
        return False


def generate_ca_certificate(
    ca_cert_file: str,
    ca_key_file: str,
    common_name: str = "P2P Network CA",
    days_valid: int = 3650,
    context=None
) -> bool:
    """
    Генерация Certificate Authority (CA)

    Args:
        ca_cert_file: путь к файлу CA сертификата
        ca_key_file: путь к файлу CA приватного ключа
        common_name: Common Name для CA
        days_valid: количество дней действия (по умолчанию 10 лет)
        context: P2PApplicationContext для доступа к storage_manager

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
            x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "Desert"),
            x509.NameAttribute(NameOID.LOCALITY_NAME, "Mountain"),
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
        ).add_extension(
            # Authority Key Identifier - для самоподписанного CA ссылается на себя
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_private_key.public_key()),
            critical=False,
        ).sign(ca_private_key, hashes.SHA256(), default_backend())

        # Сохранение CA приватного ключа
        ca_key_data = ca_private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption()
        )
        _write_cert_bytes(ca_key_file, ca_key_data, context)

        # Сохранение CA сертификата
        ca_cert_data = ca_cert.public_bytes(serialization.Encoding.PEM)
        _write_cert_bytes(ca_cert_file, ca_cert_data, context)

        logger.info(f"Generated CA certificate (saved to secure storage)")
        logger.info(f"  CA cert: {ca_cert_file}")
        logger.info(f"  CA key: {ca_key_file}")
        logger.info(f"  Valid for {days_valid} days")

        return True

    except ImportError:
        logger.error("cryptography package not installed. Install with: pip install cryptography")
        return False
    except Exception as e:
        logger.error(f"Failed to generate CA certificate: {e}")
        return False


def generate_signed_certificate(
    cert_file,
    key_file,
    ca_cert_file: str,
    ca_key_file: str,
    common_name: str,
    san_dns: list = None,
    san_ips: list = None,
    days_valid: int = 365,
    temp: bool = False,
    context=None
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
        temp: указание записи во временный файл
        context: P2PApplicationContext для доступа к storage_manager

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
        print('context', context)
        # Загрузка CA сертификата и ключа
        ca_cert_data = _read_cert_bytes(ca_cert_file, context)
        if not ca_cert_data:
            raise FileNotFoundError(f"CA certificate not found: {ca_cert_file}")
        ca_cert = x509.load_pem_x509_certificate(ca_cert_data, default_backend())

        ca_key_data = _read_cert_bytes(ca_key_file, context)
        if not ca_key_data:
            raise FileNotFoundError(f"CA private key not found: {ca_key_file}")
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
            x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "Desert"),
            x509.NameAttribute(NameOID.LOCALITY_NAME, "System"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "P2P Network"),
            x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, "IT"),
            x509.NameAttribute(NameOID.COMMON_NAME, common_name),
        ])

        # Подготовка SubjectAlternativeName
        san_list = []

        if san_dns:
            for dns in san_dns:
                san_list.append(x509.DNSName(dns))
                san_list.append(x509.DNSName("localhost"))
        else:
            san_list.append(x509.DNSName("localhost"))
            san_list.append(x509.DNSName("*.local"))

        if san_ips:
            for ip in san_ips:
                san_list.append(x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")))
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
        ).add_extension(
            # Subject Key Identifier для самого сертификата
            x509.SubjectKeyIdentifier.from_public_key(private_key.public_key()),
            critical=False,
        ).add_extension(
            # Authority Key Identifier - ссылка на CA который подписал
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_private_key.public_key()),
            critical=False,
        ).sign(ca_private_key, hashes.SHA256(), default_backend())

        # Сохранение приватного ключа
        key_data = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption()
        )

        # Сохранение сертификата
        cert_data = cert.public_bytes(serialization.Encoding.PEM)
        if temp:
            # with open(cert_file, 'rb') as f:
            cert_file.write(cert_data.decode('utf-8'))
            # with open(key_file, 'rb') as f:
            key_file.write(key_data.decode('utf-8'))

        else:
            _write_cert_bytes(key_file, key_data, context)
            _write_cert_bytes(cert_file, cert_data, context)

        logger.info(f"Generated signed certificate (saved to secure storage)")
        logger.info(f"  Node cert: {cert_file}")
        logger.info(f"  Node key: {key_file}")
        logger.info(f"  Valid for {days_valid} days")
        logger.info(f"  Signed by CA: {ca_cert_file}")

        return True

    except Exception as e:
        import traceback

        logger.error(f"Failed to generate signed certificate: {e}")
        print(traceback.format_exc())
        return False


def ensure_ca_exists(ca_cert_file: str, ca_key_file: str, context=None) -> bool:
    """
    Проверить наличие CA и создать если отсутствует

    Args:
        ca_cert_file: путь к файлу CA сертификата
        ca_key_file: путь к файлу CA приватного ключа
        context: P2PApplicationContext для доступа к storage_manager

    Returns:
        True если CA доступен
    """
    if _cert_exists(ca_cert_file, context) and _cert_exists(ca_key_file, context):
        logger.info(f"CA certificate found: {ca_cert_file}")
        return True

    logger.warning("CA not found, generating new CA...")
    return generate_ca_certificate(ca_cert_file, ca_key_file, context=context)


def ensure_certificates_exist(
    cert_file: str,
    key_file: str,
    common_name: str = "P2P Node",
    ca_cert_file: str = None,
    ca_key_file: str = None,
    context=None
) -> bool:
    """
    Проверить наличие сертификатов и создать если отсутствуют

    ВАЖНО: Self-signed сертификаты больше не поддерживаются.
    Все сертификаты должны быть подписаны CA.

    Args:
        cert_file: путь к файлу сертификата
        key_file: путь к файлу приватного ключа
        common_name: Common Name для сертификата
        ca_cert_file: путь к CA сертификату (ОБЯЗАТЕЛЬНО)
        ca_key_file: путь к CA ключу (ОБЯЗАТЕЛЬНО)

    Returns:
        True если сертификаты доступны

    Raises:
        RuntimeError: если CA параметры не предоставлены или сертификат не может быть сгенерирован
    """
    if _cert_exists(cert_file, context) and _cert_exists(key_file, context):
        logger.info(f"SSL certificates found: {cert_file}, {key_file}")
        return True

    logger.warning(f"SSL certificates not found")
    logger.debug(f"Certificate generation parameters:")
    logger.debug(f"  cert_file: {cert_file}")
    logger.debug(f"  key_file: {key_file}")
    logger.debug(f"  common_name: {common_name}")
    logger.debug(f"  ca_cert_file: {ca_cert_file}")
    logger.debug(f"  ca_key_file: {ca_key_file}")

    # Проверяем что CA параметры предоставлены
    if not ca_cert_file or not ca_cert_file.strip() or not ca_key_file or not ca_key_file.strip():
        logger.error("CA parameters not provided - cannot generate certificate")
        logger.error("Self-signed certificates are no longer supported")
        logger.error("For coordinator: ensure CA certificate is generated")
        logger.error("For worker: request certificate from coordinator via /internal/cert-request")
        raise RuntimeError(
            "Cannot generate certificate without CA. "
            "Self-signed certificates are not supported. "
            "Workers should request certificates from coordinator."
        )

    # Генерируем подписанный CA сертификат
    logger.info(f"CA parameters provided, will generate CA-signed certificate")
    logger.info(f"  CA cert: {ca_cert_file}")
    logger.info(f"  CA key: {ca_key_file}")

    # Убедимся что CA существует
    if not ensure_ca_exists(ca_cert_file, ca_key_file, context=context):
        logger.error("Failed to ensure CA exists")
        raise RuntimeError("CA certificate does not exist and could not be created")

    import platform, socket
    my_name = platform.node()
    my_ip = socket.gethostbyname(my_name)

    success = generate_signed_certificate(
        cert_file, key_file, ca_cert_file, ca_key_file, common_name,
        san_ips=[my_ip], san_dns=[my_name], context=context
    )

    if not success:
        raise RuntimeError("Failed to generate CA-signed certificate")

    return True


class ServerSSLContext:
    """
    Управляемый SSL контекст для сервера с автоматическим управлением памятью

    Использует:
    - Linux: memfd_create для хранения сертификатов только в RAM
    - Windows: безопасные временные файлы с ограниченными правами доступа

    Временные файлы удаляются при завершении работы сервера.
    """

    def __init__(self, context=None):
        """
        Args:
            context: P2PApplicationContext для доступа к storage_manager
        """
        self.context = context
        self.ssl_context: Optional[ssl.SSLContext] = None
        self.cert_fd: Optional[int] = None
        self.key_fd: Optional[int] = None
        self.cert_temp_path: Optional[str] = None
        self.key_temp_path: Optional[str] = None
        self.cert_path: Optional[str] = None  # Путь для uvicorn
        self.key_path: Optional[str] = None   # Путь для uvicorn
        self._initialized = False
        self._use_memfd = hasattr(os, 'memfd_create')  # Доступно только на Linux 3.17+

    def create(self, cert_file: str, key_file: str, verify_mode: bool = False,
               ca_cert_file: str = None) -> ssl.SSLContext:
        """
        Создать SSL контекст из защищенного хранилища

        Args:
            cert_file: путь к файлу сертификата в хранилище
            key_file: путь к файлу приватного ключа в хранилище
            verify_mode: проверять ли клиентские сертификаты
            ca_cert_file: путь к CA сертификату для верификации клиентов

        Returns:
            SSLContext готовый к использованию

        Raises:
            RuntimeError: если не удалось создать контекст
        """
        if self._initialized:
            raise RuntimeError("SSL context already initialized")

        try:
            # Проверяем наличие файлов в защищенном хранилище
            if not _cert_exists(cert_file, self.context) or not _cert_exists(key_file, self.context):
                raise RuntimeError(f"Certificate files not found in secure storage: {cert_file}, {key_file}")

            # Загружаем сертификаты из защищенного хранилища в память
            cert_data = _read_cert_bytes(cert_file, self.context)
            key_data = _read_cert_bytes(key_file, self.context)

            if not cert_data or not key_data:
                raise RuntimeError(f"Failed to read certificates from secure storage")

            if self._use_memfd:
                # Linux: используем memfd_create (только RAM, не диск)
                self.cert_path, self.key_path = self._create_memfd_files(cert_data, key_data)
                logger.debug("Certificate chain loaded from memory (via memfd)")
            else:
                # Windows/другие ОС: используем безопасные временные файлы
                self.cert_path, self.key_path = self._create_temp_files(cert_data, key_data)
                logger.debug("Certificate chain loaded from secure temporary files")

            # Создаем SSL контекст
            self.ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            self.ssl_context.load_cert_chain(self.cert_path, self.key_path)

            if verify_mode and ca_cert_file:
                self.ssl_context.verify_mode = ssl.CERT_REQUIRED

                # Загружаем CA сертификат из памяти (cadata работает везде)
                ca_data = _read_cert_bytes(ca_cert_file, self.context)
                if ca_data:
                    self.ssl_context.load_verify_locations(cadata=ca_data.decode('utf-8'))
                    logger.info(f"Client certificate verification enabled with CA from secure storage")
                else:
                    raise RuntimeError(f"Failed to load CA certificate from secure storage")
            else:
                self.ssl_context.verify_mode = ssl.CERT_NONE

            # Безопасные настройки
            self.ssl_context.minimum_version = ssl.TLSVersion.TLSv1_2
            self.ssl_context.set_ciphers('ECDHE+AESGCM:ECDHE+CHACHA20:DHE+AESGCM:DHE+CHACHA20:!aNULL:!MD5:!DSS')

            self._initialized = True
            logger.info("SSL context created successfully from secure storage")
            return self.ssl_context

        except Exception as e:
            # Очистка если что-то пошло не так
            self.cleanup()
            raise RuntimeError(f"Failed to create SSL context: {e}")

    def _create_memfd_files(self, cert_data: bytes, key_data: bytes) -> tuple:
        """Создать файлы в памяти через memfd_create (Linux)"""
        self.cert_fd = os.memfd_create("server_cert", 0)
        os.write(self.cert_fd, cert_data)
        cert_path = f"/proc/self/fd/{self.cert_fd}"

        self.key_fd = os.memfd_create("server_key", 0)
        os.write(self.key_fd, key_data)
        key_path = f"/proc/self/fd/{self.key_fd}"

        return cert_path, key_path

    def _create_temp_files(self, cert_data: bytes, key_data: bytes) -> tuple:
        """Создать безопасные временные файлы (Windows/кросс-платформенно)"""
        import tempfile
        import stat

        # Создаем временный файл для сертификата
        cert_fd, self.cert_temp_path = tempfile.mkstemp(prefix='ssl_cert_', suffix='.pem')
        try:
            # Ограничиваем права доступа (только текущий пользователь)
            os.chmod(self.cert_temp_path, stat.S_IRUSR | stat.S_IWUSR)
            os.write(cert_fd, cert_data)
        finally:
            os.close(cert_fd)

        # Создаем временный файл для ключа
        key_fd, self.key_temp_path = tempfile.mkstemp(prefix='ssl_key_', suffix='.pem')
        try:
            # Ограничиваем права доступа (только текущий пользователь)
            os.chmod(self.key_temp_path, stat.S_IRUSR | stat.S_IWUSR)
            os.write(key_fd, key_data)
        finally:
            os.close(key_fd)

        logger.debug(f"Temporary cert files created: {self.cert_temp_path}, {self.key_temp_path}")
        logger.warning("Using temporary files for SSL (Windows mode) - files will be deleted on shutdown")

        return self.cert_temp_path, self.key_temp_path

    def get_cert_path(self) -> str:
        """
        Получить путь к файлу сертификата для uvicorn

        Returns:
            Путь к файлу сертификата (memfd или temp file)

        Raises:
            RuntimeError: если контекст не инициализирован
        """
        if not self._initialized or not self.cert_path:
            raise RuntimeError("SSL context not initialized")
        return self.cert_path

    def get_key_path(self) -> str:
        """
        Получить путь к файлу ключа для uvicorn

        Returns:
            Путь к файлу ключа (memfd или temp file)

        Raises:
            RuntimeError: если контекст не инициализирован
        """
        if not self._initialized or not self.key_path:
            raise RuntimeError("SSL context not initialized")
        return self.key_path

    def cleanup(self):
        """Очистить ресурсы (закрыть file descriptors / удалить временные файлы)"""
        # Linux: закрываем memfd дескрипторы
        if self.cert_fd is not None:
            try:
                os.close(self.cert_fd)
            except:
                pass
            self.cert_fd = None

        if self.key_fd is not None:
            try:
                os.close(self.key_fd)
            except:
                pass
            self.key_fd = None

        # Windows: удаляем временные файлы
        if self.cert_temp_path is not None:
            try:
                os.unlink(self.cert_temp_path)
                logger.debug(f"Temporary cert file deleted: {self.cert_temp_path}")
            except:
                pass
            self.cert_temp_path = None

        if self.key_temp_path is not None:
            try:
                os.unlink(self.key_temp_path)
                logger.debug(f"Temporary key file deleted: {self.key_temp_path}")
            except:
                pass
            self.key_temp_path = None

        self.ssl_context = None
        self.cert_path = None
        self.key_path = None
        self._initialized = False
        logger.debug("SSL context cleaned up")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.cleanup()
        return False


def create_ssl_context(
    cert_file: str,
    key_file: str,
    verify_mode: bool = False,
    ca_cert_file: str = None
) -> Optional[ssl.SSLContext]:
    """
    Создать SSL контекст для HTTPS сервера из защищенного хранилища (без записи на диск)

    ВНИМАНИЕ: Эта функция создает временные memfd дескрипторы которые закрываются сразу.
    Для долгоживущих серверов (uvicorn) используйте ServerSSLContext класс!

    Args:
        cert_file: путь к файлу сертификата
        key_file: путь к файлу приватного ключа
        verify_mode: проверять ли клиентские сертификаты
        ca_cert_file: путь к CA сертификату для верификации клиентов

    Returns:
        SSLContext или None при ошибке
    """
    try:
        ctx = ServerSSLContext()
        return ctx.create(cert_file, key_file, verify_mode, ca_cert_file)
    except Exception as e:
        logger.error(f"Failed to create SSL context: {e}")
        return None


def create_client_ssl_context(verify: bool = True, ca_cert_file: str = None, context=None) -> ssl.SSLContext:
    """
    Создать SSL контекст для HTTPS клиента из защищенного хранилища (без записи на диск)

    Args:
        verify: проверять ли серверный сертификат
        ca_cert_file: путь к CA сертификату для верификации сервера
        context: P2PApplicationContext для доступа к storage_manager

    Returns:
        SSLContext для клиента
    """
    ssl_context = ssl.create_default_context()

    if verify and ca_cert_file:
        # Загружаем CA сертификат из защищенного хранилища в память
        ca_data = _read_cert_bytes(ca_cert_file, context)
        if ca_data:
            # load_verify_locations поддерживает cadata для загрузки CA из памяти
            ssl_context.load_verify_locations(cadata=ca_data.decode('utf-8'))
            ssl_context.check_hostname = True
            ssl_context.verify_mode = ssl.CERT_REQUIRED
            logger.debug(f"Client SSL context created with CA verification from secure storage")
        else:
            logger.error(f"Failed to load CA certificate from secure storage: {ca_cert_file}")
            raise RuntimeError(f"Cannot create client SSL context without CA certificate")
    elif not verify:
        # Отключаем верификацию (не рекомендуется для production)
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        logger.warning("Client SSL context created WITHOUT verification (insecure)")
    else:
        # Используем системные CA
        logger.debug("Client SSL context created with system CA verification")

    return ssl_context


def get_certificate_info(cert_file: str, context) -> Optional[dict]:
    """
    Получить информацию о сертификате

    Args:
        cert_file: путь к файлу сертификата
        context: App context

    Returns:
        Словарь с информацией о сертификате или None
    """
    try:
        from cryptography import x509
        from cryptography.hazmat.backends import default_backend

        cert_data = _read_cert_bytes(cert_file, context)
        if not cert_data:
            return None

        cert = x509.load_pem_x509_certificate(cert_data, default_backend())

        return {
            "subject": cert.subject.rfc4514_string(),
            "issuer": cert.issuer.rfc4514_string(),
            "serial_number": cert.serial_number,
            "not_valid_before": cert.not_valid_before.isoformat(),
            "not_valid_after": cert.not_valid_after.isoformat(),
            "is_valid": datetime.utcnow() < cert.not_valid_after,
            "days_until_expiry": (cert.not_valid_after - datetime.utcnow()).days,
            "is_ca": _is_ca_certificate(cert),
            "ips": cert.extensions
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


def get_certificate_fingerprint(cert_file: str, context) -> Optional[str]:
    """
    Получить SHA256 отпечаток сертификата

    Args:
        cert_file: путь к файлу сертификата
        context: App context

    Returns:
        Hex-строка с отпечатком или None
    """
    try:
        from cryptography import x509
        from cryptography.hazmat.backends import default_backend
        from cryptography.hazmat.primitives import hashes

        cert_data = _read_cert_bytes(cert_file, context)
        if not cert_data:
            return None

        cert = x509.load_pem_x509_certificate(cert_data, default_backend())
        fingerprint = cert.fingerprint(hashes.SHA256())

        return fingerprint.hex()

    except Exception as e:
        logger.error(f"Failed to get certificate fingerprint: {e}")
        return None


def get_certificate_san(cert_file: str, context) -> Tuple[list, list]:
    """
    Получить IP адреса и DNS имена из SubjectAlternativeName сертификата

    Args:
        cert_file: путь к файлу сертификата
        context: App context

    Returns:
        Tuple из (список IP адресов, список DNS имен)
    """
    try:
        from cryptography import x509
        from cryptography.hazmat.backends import default_backend
        from cryptography.x509.oid import ExtensionOID

        cert_data = _read_cert_bytes(cert_file, context)
        if not cert_data:
            return [], []

        cert = x509.load_pem_x509_certificate(cert_data, default_backend())

        try:
            san_ext = cert.extensions.get_extension_for_oid(ExtensionOID.SUBJECT_ALTERNATIVE_NAME)
            san = san_ext.value

            ips = [str(ip.value) for ip in san.get_values_for_type(x509.IPAddress)]
            dns_names = [str(dns.value) for dns in san.get_values_for_type(x509.DNSName)]

            return ips, dns_names

        except x509.ExtensionNotFound:
            return [], []

    except Exception as e:
        logger.error(f"Failed to get certificate SAN: {e}")
        return [], []


def get_current_network_info() -> Tuple[list, str]:
    """
    Получить текущие IP адреса и hostname машины

    Returns:
        Tuple из (список IP адресов, hostname)
    """
    import socket
    import platform

    try:
        # Получаем hostname
        hostname = platform.node()

        # Получаем все IP адреса (исключая loopback)
        ip_addresses = []

        # Получаем IP адреса через socket
        try:
            # Получаем IP адрес по hostname
            primary_ip = socket.gethostbyname(hostname)
            if primary_ip and primary_ip != '127.0.0.1':
                ip_addresses.append(primary_ip)
        except:
            pass

        # Пробуем получить все сетевые интерфейсы
        try:
            import netifaces
            for interface in netifaces.interfaces():
                addrs = netifaces.ifaddresses(interface)
                if netifaces.AF_INET in addrs:
                    for addr_info in addrs[netifaces.AF_INET]:
                        ip = addr_info.get('addr')
                        if ip and ip != '127.0.0.1' and not ip.startswith('169.254'):
                            if ip not in ip_addresses:
                                ip_addresses.append(ip)
        except ImportError:
            # netifaces не установлен, используем альтернативный метод
            try:
                # Создаем временный сокет для получения IP
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.connect(("8.8.8.8", 80))
                local_ip = s.getsockname()[0]
                s.close()

                if local_ip and local_ip != '127.0.0.1' and local_ip not in ip_addresses:
                    ip_addresses.append(local_ip)
            except:
                pass

        # Всегда добавляем localhost в конец
        if '127.0.0.1' not in ip_addresses:
            ip_addresses.append('127.0.0.1')

        logger.debug(f"Current network info - hostname: {hostname}, IPs: {ip_addresses}")
        return ip_addresses, hostname

    except Exception as e:
        logger.error(f"Failed to get current network info: {e}")
        return ['127.0.0.1'], 'localhost'


def needs_certificate_renewal(cert_file: str, ca_cert_file: str = None, context=None) -> Tuple[bool, str]:
    """
    Проверить нужно ли обновление сертификата

    Проверяет:
    - Существование сертификата
    - Изменение IP адресов или hostname
    - Срок действия сертификата

    Args:
        cert_file: путь к файлу сертификата
        ca_cert_file: путь к CA сертификату (опционально)
        context: App context

    Returns:
        Tuple из (нужно ли обновление, причина)
    """
    # Проверяем существование сертификата
    if not _cert_exists(cert_file):
        return True, "certificate_not_found"

    try:
        from cryptography import x509
        from cryptography.hazmat.backends import default_backend

        # Загружаем сертификат
        cert_data = _read_cert_bytes(cert_file, context)
        if not cert_data:
            return True, "certificate_not_found"

        cert = x509.load_pem_x509_certificate(cert_data, default_backend())

        # Проверяем срок действия (обновляем за 30 дней до истечения)
        days_until_expiry = (cert.not_valid_after - datetime.utcnow()).days
        if days_until_expiry < 30:
            return True, f"expiring_soon_{days_until_expiry}_days"

        # Получаем текущие IP и hostname
        current_ips, current_hostname = get_current_network_info()

        # Получаем IP и DNS из сертификата
        cert_ips, cert_dns = get_certificate_san(cert_file, context)

        # Проверяем совпадение IP адресов (исключая localhost)
        current_ips_set = set([ip for ip in current_ips if ip != '127.0.0.1'])
        cert_ips_set = set([ip for ip in cert_ips if ip != '127.0.0.1'])

        if current_ips_set != cert_ips_set:
            logger.info(f"IP addresses changed: cert has {cert_ips_set}, current {current_ips_set}")
            return True, "ip_address_changed"

        # Проверяем hostname
        if current_hostname not in cert_dns:
            logger.info(f"Hostname changed: cert has {cert_dns}, current {current_hostname}")
            return True, "hostname_changed"

        # Все проверки пройдены
        return False, "valid"

    except Exception as e:
        logger.error(f"Failed to check certificate renewal: {e}")
        return True, f"check_error_{str(e)}"


def generate_challenge() -> str:
    """
    Генерация уникального challenge для ACME-подобной валидации

    Returns:
        Hex-строка с уникальным challenge
    """
    import secrets
    return secrets.token_hex(32)


async def request_ca_cert_from_coordinator(
    coordinator_url: str,
    context = None
) -> Optional[str]:
    """
    Запросить CA сертификат от координатора (ACME-like)

    Эта функция используется воркером при первом запуске для получения
    CA сертификата, необходимого для верификации HTTPS соединений.

    Args:
        coordinator_url: URL координатора (например, "https://coord:8001" или "coord:8001")
        context: контекст приложения для доступа к storage_manager

    Returns:
        CA certificate в PEM формате или None при ошибке
    """
    import httpx

    try:
        # Формируем HTTPS URL для координатора
        if '://' not in coordinator_url:
            # Если не указан протокол, пробуем сначала HTTPS
            https_url = f"https://{coordinator_url}"
        else:
            # Заменяем http на https если нужно
            https_url = coordinator_url.replace("http://", "https://")

        timeout = httpx.Timeout(15.0)

        logger.info(f"Requesting CA certificate from coordinator: {https_url}/internal/ca-cert")

        # Используем HTTPS БЕЗ верификации, т.к. у нас еще нет CA сертификата
        # Это безопасно для получения публичного CA сертификата
        async with httpx.AsyncClient(timeout=timeout, verify=False) as client:
            response = await client.get(f"{https_url}/internal/ca-cert")

            logger.info(f"CA certificate request response: {response.status_code}")

            if response.status_code == 200:
                result = response.json()
                ca_cert_pem = result.get("ca_certificate")

                if ca_cert_pem:
                    logger.info("Successfully received CA certificate from coordinator")
                    return ca_cert_pem
                else:
                    logger.error("Invalid response from coordinator: missing ca_certificate")
                    return None
            else:
                logger.error(f"CA certificate request failed: {response.status_code}")
                try:
                    error_detail = response.json()
                    logger.error(f"Error details: {error_detail}")
                except:
                    logger.error(f"Response text: {response.text[:500]}")
                return None

    except httpx.ConnectError as e:
        logger.error(f"Failed to connect to coordinator: {e}")
        logger.error(f"  URL: {https_url}/internal/ca-cert")
        logger.error(f"  Make sure coordinator is running and accessible")
        return None
    except httpx.TimeoutException as e:
        logger.error(f"Request timeout while contacting coordinator: {e}")
        return None
    except Exception as e:
        logger.error(f"Failed to request CA certificate from coordinator: {e}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        return None


async def request_certificate_from_coordinator(
    node_id: str,
    coordinator_url: str,
    challenge: str,
    ip_addresses: list,
    dns_names: list,
    old_cert_fingerprint: str = None,
    ca_cert_file: str = None,
    challenge_port: int = None
) -> Tuple[Optional[str], Optional[str]]:
    """
    Запросить новый сертификат от координатора

    Args:
        node_id: идентификатор узла
        coordinator_url: URL координатора (например, "https://coord:8001")
        challenge: уникальный challenge для валидации
        ip_addresses: список IP адресов для сертификата
        dns_names: список DNS имен для сертификата
        old_cert_fingerprint: отпечаток старого сертификата (если обновление)
        ca_cert_file: путь к CA сертификату для верификации
        challenge_port: порт для валидации challenge (если отличается от основного)

    Returns:
        Tuple из (certificate_pem, private_key_pem) или (None, None) при ошибке
    """
    import httpx

    try:
        # Формируем запрос
        request_data = {
            "node_id": node_id,
            "challenge": challenge,
            "ip_addresses": ip_addresses,
            "dns_names": dns_names,
        }

        if old_cert_fingerprint:
            request_data["old_cert_fingerprint"] = old_cert_fingerprint

        if challenge_port:
            request_data["challenge_port"] = challenge_port

        # Формируем HTTPS URL для координатора
        if '://' not in coordinator_url:
            # Если не указан протокол, добавляем https://
            https_url = f"https://{coordinator_url}"
        else:
            # Заменяем http на https если нужно
            https_url = coordinator_url.replace("http://", "https://")

        timeout = httpx.Timeout(30.0)

        # Используем HTTPS с CA верификацией
        verify_param = False  # По умолчанию без верификации

        if ca_cert_file and Path(ca_cert_file).exists():
            # Используем CA сертификат для верификации
            verify_param = str(Path(ca_cert_file).resolve())
            logger.info(f"Using CA certificate for verification: {verify_param}")
        else:
            logger.warning("No CA certificate available, using HTTPS without verification")

        logger.info(f"Sending certificate request to: {https_url}/internal/cert-request")
        logger.debug(f"Request data: node_id={node_id}, IPs={ip_addresses}, DNS={dns_names}")

        async with httpx.AsyncClient(timeout=timeout, verify=verify_param) as client:
            response = await client.post(
                f"{https_url}/internal/cert-request",
                json=request_data
            )

            logger.info(f"Certificate request response: {response.status_code}")

            if response.status_code == 200:
                result = response.json()
                print(result)
                certificate_pem = result.get("certificate")
                private_key_pem = result.get("private_key")

                if certificate_pem and private_key_pem:
                    logger.info(f"Successfully received certificate from coordinator")
                    return certificate_pem, private_key_pem
                else:
                    logger.error("Invalid response from coordinator: missing certificate or key")
                    return None, None
            else:
                logger.error(f"Certificate request failed: {response.status_code}")
                try:
                    error_detail = response.json()
                    logger.error(f"Error details: {error_detail}")
                except:
                    logger.error(f"Response text: {response.text[:500]}")
                return None, None

    except httpx.ConnectError as e:
        logger.error(f"Failed to connect to coordinator: {e}")
        logger.error(f"  URL: {https_url}/internal/cert-request")
        logger.error(f"  Make sure coordinator is running and accessible")
        return None, None
    except httpx.TimeoutException as e:
        logger.error(f"Request timeout while contacting coordinator: {e}")
        return None, None
    except Exception as e:
        logger.error(f"Failed to request certificate from coordinator: {e}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        return None, None


def save_certificate_and_key(cert_pem: str, key_pem: str, cert_file: str, key_file: str, context=None) -> bool:
    """
    Сохранить сертификат и ключ ТОЛЬКО в защищенное хранилище

    Args:
        cert_pem: PEM-форматированный сертификат
        key_pem: PEM-форматированный приватный ключ
        cert_file: путь для сохранения сертификата
        key_file: путь для сохранения ключа
        context: контекст приложения для доступа к storage_manager

    Returns:
        True если успешно сохранено

    Raises:
        RuntimeError: если защищенное хранилище недоступно
    """
    try:
        # Конвертируем в байты
        cert_data = cert_pem.encode('utf-8') if isinstance(cert_pem, str) else cert_pem
        key_data = key_pem.encode('utf-8') if isinstance(key_pem, str) else key_pem

        # Сохраняем сертификат ТОЛЬКО в защищенное хранилище
        _write_cert_bytes(cert_file, cert_data, context)

        # Сохраняем ключ ТОЛЬКО в защищенное хранилище
        _write_cert_bytes(key_file, key_data, context)

        logger.info(f"Certificate and key saved to secure storage: {cert_file}, {key_file}")
        return True

    except Exception as e:
        logger.error(f"Failed to save certificate and key: {e}")
        raise

