"""Тесты парсера certmgr.exe для CSP v4, v5 (EN), v5 (RU) форматов вывода."""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.certstool.service import CertsTool

_ct = CertsTool.__new__(CertsTool)

# --- CSP v4 (старый формат, английский) ---
CSP_V4_OUTPUT = """\
1-------
Issuer              : E=ca@example.com, CN=Test CA
Subject             : E=user@example.com, CN=User Name
Serial              : 0x1234567890ABCDEF
SHA1 Hash           : aabbccdd11223344556677889900aabbccdd1122
ValidFrom           : 01/01/2025 00:00:00 UTC
ValidTo             : 01/01/2027 00:00:00 UTC
Container           : HDIMAGE\\\\user_container
PrivateKey          : Yes
[ErrorCode: 0x00000000]
"""

# --- CSP v5 английский ---
CSP_V5_EN_OUTPUT = """\
1-------
Issuer              : E=ca@example.com, CN=Test CA v5
Subject             : E=user@example.com, CN=User V5
Serial              : 0x2033BE2395469E94EE176655634AC814
SHA1 Thumbprint     : f24852da206c37715c1a2bebe03e5f2d2ce52bac
SubjectKeyID        : 1850a782c06458556f2de7f94befea45faf859b0
Signature Algorithm : GOST R 34.10-2012
PublicKey Algorithm : GOST R 34.10-2012
Not valid before    : 15/09/2025 13:32:39 UTC
Not valid after     : 09/12/2026 13:32:39 UTC
PrivateKey Link     : Yes
Container           : REGISTRY\\\\v5_container
Provider Name       : Crypto-Pro GOST R 34.10-2012 CSP
Provider Info       : Provider Type: 80, Key Spec: 1, Flags: 0x0
2-------
Issuer              : E=root@example.com, CN=Root CA
Serial              : 0xAAAA000011112222
SHA1 Thumbprint     : 99887766554433221100ffeeddccbbaa99887766
Not valid before    : 01/01/2024 00:00:00 UTC
Not valid after     : 01/01/2030 00:00:00 UTC
PrivateKey Link     : No
Container           : HDIMAGE\\\\root_container
[ErrorCode: 0x00000000]
"""

# --- CSP v5 русский (chcp 1251) ---
CSP_V5_RU_OUTPUT = """\
1-------
Издатель            : E=ca@example.com, CN=Test CA
Субъект             : E=user@example.com, CN=User RU
Серийный номер      : 0x2033BE2395469E94EE176655634AC814
SHA1 отпечаток      : f24852da206c37715c1a2bebe03e5f2d2ce52bac
Идентификатор ключа : 1850a782c06458556f2de7f94befea45faf859b0
Алгоритм подписи    : GOST R 34.10-2012
Алгоритм откр. кл.  : GOST R 34.10-2012
Выдан               : 15/09/2025 13:32:39 UTC
Истекает            : 09/12/2026 13:32:39 UTC
Ссылка на ключ      : Yes
Контейнер           : REGISTRY\\\\ru_container
Имя провайдера      : Crypto-Pro GOST R 34.10-2012 CSP
Инфо о провайдере   : Provider Type: 80, Key Spec: 1, Flags: 0x0
Тип идентификации   : Personal presence
Назначение/EKU      : 1.3.6.1.5.5.7.3.2
2-------
Издатель            : E=root@example.com, CN=Root CA RU
Серийный номер      : 0xAAAA000011112222
SHA1 отпечаток      : 99887766554433221100ffeeddccbbaa99887766
Выдан               : 01/01/2024 00:00:00 UTC
Истекает            : 01/01/2030 00:00:00 UTC
Ссылка на ключ      : No
Контейнер           : FAT12\\D6C3B6D8\\sub.000\\28AE
[ErrorCode: 0x00000000]
"""


def test_csp_v4_parser():
    certs = _ct._parse_certificate_list(CSP_V4_OUTPUT)
    assert len(certs) >= 1, f"Expected >= 1 cert, got {len(certs)}"

    first = list(certs.values())[0]
    assert first['Thumbprint'] == 'aabbccdd11223344556677889900aabbccdd1122'
    assert first['ValidFrom'] == '01/01/2025 00:00:00 UTC'
    assert first['ValidTo'] == '01/01/2027 00:00:00 UTC'
    assert first['Subject_CN'] == 'User Name'
    assert first['Container'] == 'user_container'
    assert first['ContainerType'] == 'HDIMAGE'

    print("[OK] CSP v4 parser test passed")


def test_csp_v5_en_parser():
    certs = _ct._parse_certificate_list(CSP_V5_EN_OUTPUT)
    assert len(certs) == 2, f"Expected 2 certs, got {len(certs)}"

    first = list(certs.values())[0]
    assert first['Thumbprint'] == 'f24852da206c37715c1a2bebe03e5f2d2ce52bac'
    assert first['ValidFrom'] == '15/09/2025 13:32:39 UTC'
    assert first['ValidTo'] == '09/12/2026 13:32:39 UTC'
    assert first['Subject_CN'] == 'User V5'
    assert first['Container'] == 'v5_container'
    assert first['ContainerType'] == 'REGISTRY'
    assert 'SHA1 Thumbprint' in first
    assert 'PrivateKey Link' in first
    assert 'Provider Name' in first

    # Root CA (no Subject)
    second = list(certs.values())[1]
    assert 'Subject' in second  # fallback to Issuer
    assert second['Container'] == 'root_container'
    assert second['ContainerType'] == 'HDIMAGE'

    print("[OK] CSP v5 EN parser test passed")


def test_csp_v5_ru_parser():
    certs = _ct._parse_certificate_list(CSP_V5_RU_OUTPUT)
    assert len(certs) == 2, f"Expected 2 certs, got {len(certs)}"

    first = list(certs.values())[0]

    # Проверяем что русские имена полей маппятся на английские
    assert 'Issuer' in first, f"Issuer missing, got keys: {list(first.keys())[:10]}"
    assert 'Subject' in first, f"Subject missing"
    assert 'Serial' in first, f"Serial missing"
    assert 'Thumbprint' in first, f"Thumbprint missing"
    assert first['Thumbprint'] == 'f24852da206c37715c1a2bebe03e5f2d2ce52bac'

    assert 'ValidFrom' in first, f"ValidFrom missing"
    assert 'ValidTo' in first, f"ValidTo missing"
    assert first['ValidFrom'] == '15/09/2025 13:32:39 UTC'
    assert first['ValidTo'] == '09/12/2026 13:32:39 UTC'

    assert first['Container'] == 'ru_container', f"Container: {first.get('Container')}"
    assert first['ContainerType'] == 'REGISTRY'
    assert first['Subject_CN'] == 'User RU'
    assert first['Issuer_CN'] == 'Test CA'
    assert 'PrivateKey Link' in first
    assert 'Provider Name' in first
    assert 'Extended Key Usage' in first

    # Root CA (нет Субъект) — FAT12
    second = list(certs.values())[1]
    assert 'Subject' in second  # fallback to Issuer
    assert second['ContainerType'] == 'FAT12'
    # FAT12 container path is kept as-is (with single backslashes) — это полный путь
    assert 'D6C3B6D8' in second['Container']

    print("[OK] CSP v5 RU parser test passed")


def test_extract_error_code():
    assert CertsTool._extract_error_code('[ErrorCode: 0x00000000]') == '0x00000000'
    assert CertsTool._extract_error_code('ErrorCode : 0x80090010') == '0x80090010'
    assert CertsTool._extract_error_code('no error here') == ''
    print("[OK] Extract error code test passed")


def test_extract_container():
    assert CertsTool._extract_container('Контейнер           : REGISTRY\\\\name123') == 'REGISTRY\\\\name123'
    assert CertsTool._extract_container('Контейнер           : FAT12\\path\\name') == 'FAT12\\path\\name'
    assert CertsTool._extract_container('no container here') == ''
    print("[OK] Extract container test passed")


if __name__ == '__main__':
    test_csp_v4_parser()
    test_csp_v5_en_parser()
    test_csp_v5_ru_parser()
    test_extract_error_code()
    test_extract_container()
    print("\nAll tests passed!")
