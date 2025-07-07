"""
Authentication and authorization module for P2P Admin System
"""

import os
import time
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, List
from pathlib import Path

from jose import JWTError, jwt
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.backends import default_backend
from fastapi import HTTPException, Depends, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

logger = logging.getLogger(__name__)

# Security схема
security = HTTPBearer()


class P2PAuth:
    """Класс для управления авторизацией в P2P сети"""

    def __init__(self, secret_key: str = None, algorithm: str = "HS256"):
        self.secret_key = secret_key or os.getenv("AUTH_SECRET", "your-secret-key")
        self.algorithm = algorithm
        self.token_expire_minutes = 60
        self.trusted_nodes: Dict[str, dict] = {}
        self.revoked_tokens: List[str] = []

        # PKI компоненты
        self.private_key = None
        self.public_key = None
        self.certificate = None
        self.ca_certificates: List[x509.Certificate] = []

        # Инициализация PKI
        self._init_pki()

    def _init_pki(self):
        """Инициализация PKI компонентов"""
        cert_dir = Path("certs")

        # Проверка наличия сертификатов
        key_file = cert_dir / "node.key"
        cert_file = cert_dir / "node.crt"
        ca_file = cert_dir / "ca.crt"

        if key_file.exists() and cert_file.exists():
            # Загрузка существующих сертификатов
            self._load_certificates(key_file, cert_file, ca_file)
        else:
            # Генерация самоподписанного сертификата для тестирования
            logger.warning("No certificates found, generating self-signed certificate")
            self._generate_self_signed_cert(cert_dir)

    def _load_certificates(self, key_file: Path, cert_file: Path, ca_file: Path):
        """Загрузка сертификатов из файлов"""
        try:
            # Загрузка приватного ключа
            with open(key_file, "rb") as f:
                self.private_key = serialization.load_pem_private_key(
                    f.read(),
                    password=None,
                    backend=default_backend()
                )

            # Загрузка сертификата
            with open(cert_file, "rb") as f:
                self.certificate = x509.load_pem_x509_certificate(
                    f.read(),
                    backend=default_backend()
                )

            self.public_key = self.certificate.public_key()

            # Загрузка CA сертификатов
            if ca_file.exists():
                with open(ca_file, "rb") as f:
                    ca_cert = x509.load_pem_x509_certificate(
                        f.read(),
                        backend=default_backend()
                    )
                    self.ca_certificates.append(ca_cert)

            logger.info("Certificates loaded successfully")

        except Exception as e:
            logger.error(f"Failed to load certificates: {e}")
            raise

    def _generate_self_signed_cert(self, cert_dir: Path):
        """Генерация самоподписанного сертификата"""
        cert_dir.mkdir(parents=True, exist_ok=True)

        # Генерация ключей
        self.private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
            backend=default_backend()
        )
        self.public_key = self.private_key.public_key()

        # Создание сертификата
        subject = issuer = x509.Name([
            x509.NameAttribute(NameOID.COUNTRY_NAME, "US"),
            x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "Test"),
            x509.NameAttribute(NameOID.LOCALITY_NAME, "Test"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "P2P Admin System"),
            x509.NameAttribute(NameOID.COMMON_NAME, "localhost"),
        ])

        self.certificate = x509.CertificateBuilder().subject_name(
            subject
        ).issuer_name(
            issuer
        ).public_key(
            self.public_key
        ).serial_number(
            x509.random_serial_number()
        ).not_valid_before(
            datetime.utcnow()
        ).not_valid_after(
            datetime.utcnow() + timedelta(days=365)
        ).add_extension(
            x509.SubjectAlternativeName([
                x509.DNSName("localhost"),
                x509.DNSName("*.localhost"),
            ]),
            critical=False,
        ).sign(self.private_key, hashes.SHA256(), backend=default_backend())

        # Сохранение ключа
        with open(cert_dir / "node.key", "wb") as f:
            f.write(self.private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption()
            ))

        # Сохранение сертификата
        with open(cert_dir / "node.crt", "wb") as f:
            f.write(self.certificate.public_bytes(serialization.Encoding.PEM))

        logger.info("Self-signed certificate generated")

    def generate_token(self, node_id: str, additional_claims: dict = None) -> str:
        """Генерация JWT токена для узла"""
        expire = datetime.utcnow() + timedelta(minutes=self.token_expire_minutes)

        claims = {
            "sub": node_id,
            "exp": expire,
            "iat": datetime.utcnow(),
            "type": "node",
        }

        if additional_claims:
            claims.update(additional_claims)

        # Добавление отпечатка сертификата
        if self.certificate:
            fingerprint = self.certificate.fingerprint(hashes.SHA256()).hex()
            claims["cert_fingerprint"] = fingerprint

        token = jwt.encode(claims, self.secret_key, algorithm=self.algorithm)
        return token

    def verify_token(self, token: str) -> Optional[dict]:
        """Проверка JWT токена"""
        try:
            # Проверка на отозванные токены
            if token in self.revoked_tokens:
                return None

            # Декодирование токена
            payload = jwt.decode(token, self.secret_key, algorithms=[self.algorithm])

            # Проверка типа токена
            if payload.get("type") != "node":
                return None

            # Проверка отпечатка сертификата
            if "cert_fingerprint" in payload:
                # В реальной системе здесь нужна проверка против известных сертификатов
                pass

            return payload

        except JWTError as e:
            logger.debug(f"Token verification failed: {e}")
            return None

    def revoke_token(self, token: str):
        """Отзыв токена"""
        if token not in self.revoked_tokens:
            self.revoked_tokens.append(token)

            # Очистка старых токенов (истекших)
            self._cleanup_revoked_tokens()

    def _cleanup_revoked_tokens(self):
        """Очистка истекших отозванных токенов"""
        current_time = time.time()
        active_revoked = []

        for token in self.revoked_tokens:
            try:
                # Декодируем без проверки для получения exp
                payload = jwt.decode(token, options={"verify_signature": False})
                if payload.get("exp", 0) > current_time:
                    active_revoked.append(token)
            except:
                pass

        self.revoked_tokens = active_revoked

    def trust_node(self, node_id: str, certificate: x509.Certificate = None):
        """Добавление узла в доверенные"""
        self.trusted_nodes[node_id] = {
            "added_at": time.time(),
            "certificate": certificate
        }
        logger.info(f"Node {node_id} added to trusted nodes")

    def untrust_node(self, node_id: str):
        """Удаление узла из доверенных"""
        if node_id in self.trusted_nodes:
            del self.trusted_nodes[node_id]
            logger.info(f"Node {node_id} removed from trusted nodes")

    def is_trusted_node(self, node_id: str) -> bool:
        """Проверка, является ли узел доверенным"""
        return node_id in self.trusted_nodes

    def sign_message(self, message: bytes) -> bytes:
        """Подпись сообщения приватным ключом"""
        if not self.private_key:
            raise ValueError("Private key not available")

        signature = self.private_key.sign(
            message,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH
            ),
            hashes.SHA256()
        )
        return signature

    def verify_signature(self, message: bytes, signature: bytes, public_key) -> bool:
        """Проверка подписи сообщения"""
        try:
            public_key.verify(
                signature,
                message,
                padding.PSS(
                    mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=padding.PSS.MAX_LENGTH
                ),
                hashes.SHA256()
            )
            return True
        except Exception as e:
            logger.debug(f"Signature verification failed: {e}")
            return False

    def encrypt_for_node(self, node_id: str, data: bytes) -> Optional[bytes]:
        """Шифрование данных для конкретного узла"""
        node_info = self.trusted_nodes.get(node_id)
        if not node_info or not node_info.get("certificate"):
            return None

        try:
            certificate = node_info["certificate"]
            public_key = certificate.public_key()

            encrypted = public_key.encrypt(
                data,
                padding.OAEP(
                    mgf=padding.MGF1(algorithm=hashes.SHA256()),
                    algorithm=hashes.SHA256(),
                    label=None
                )
            )
            return encrypted
        except Exception as e:
            logger.error(f"Encryption failed: {e}")
            return None

    def decrypt_data(self, encrypted_data: bytes) -> Optional[bytes]:
        """Расшифровка данных приватным ключом"""
        if not self.private_key:
            return None

        try:
            decrypted = self.private_key.decrypt(
                encrypted_data,
                padding.OAEP(
                    mgf=padding.MGF1(algorithm=hashes.SHA256()),
                    algorithm=hashes.SHA256(),
                    label=None
                )
            )
            return decrypted
        except Exception as e:
            logger.error(f"Decryption failed: {e}")
            return None


# Dependency для FastAPI
async def get_current_node(
        credentials: HTTPAuthorizationCredentials = Depends(security)
) -> str:
    """Получение текущего авторизованного узла"""

    # Получение auth из app state
    from fastapi import Request
    from starlette.requests import Request as StarletteRequest

    # Хак для получения request в зависимости
    import inspect
    frame = inspect.currentframe()
    while frame:
        if 'request' in frame.f_locals and isinstance(frame.f_locals['request'], StarletteRequest):
            request = frame.f_locals['request']
            break
        frame = frame.f_back

    if not frame:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    auth: P2PAuth = request.app.state.auth

    # Проверка токена
    token = credentials.credentials
    payload = auth.verify_token(token)

    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    node_id = payload.get("sub")
    if not node_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return node_id


# Опциональная зависимость для публичных эндпоинтов
async def get_optional_node(
        credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)
) -> Optional[str]:
    """Получение узла если токен предоставлен"""
    if not credentials:
        return None

    try:
        return await get_current_node(credentials)
    except HTTPException:
        return None


# Декоратор для требования доверенного узла
def require_trusted_node(func):
    """Декоратор требующий доверенный узел"""

    async def wrapper(*args, current_node: str = Depends(get_current_node), **kwargs):
        # Получение auth из request
        request = kwargs.get('request')
        if request and hasattr(request.app.state, 'auth'):
            auth: P2PAuth = request.app.state.auth

            if not auth.is_trusted_node(current_node):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Node is not trusted"
                )

        return await func(*args, current_node=current_node, **kwargs)

    return wrapper