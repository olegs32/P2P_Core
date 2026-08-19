import datetime
import subprocess
import os
from pathlib import Path

from cryptography import x509
from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509 import load_pem_x509_certificate
from cryptography.hazmat.primitives.serialization import load_pem_private_key, pkcs12

# =====================================================================
# ЧАСТЬ 1: ГЕНЕРАЦИЯ СЕРТИФИКАТА CODE SIGNING (БЕЗ ИЗМЕНЕНИЙ)
# =====================================================================
pfx_password = b"00000000"
temp_pfx_path = "temp_dev_bundle.pfx"
os.makedirs('signed', exist_ok=True)
SIGN = Path('sign')


def cert_generate_from_ca():
    print("1. Загрузка корневого CA...")
    with open("ca_cert.pem", "rb") as f:
        ca_cert = load_pem_x509_certificate(f.read())

    with open("ca_key.pem", "rb") as f:
        ca_key = load_pem_private_key(f.read(), password=None)

    print("2. Генерация закрытого ключа для кода...")
    dev_private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048
    )

    subject = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "Trusted_Software"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Dev_Python"),
    ])

    dev_cert_builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(dev_private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.now(datetime.timezone.utc))
        .not_valid_after(datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=365))
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CODE_SIGNING]),
            critical=True
        )
    )

    dev_certificate = dev_cert_builder.sign(ca_key, hashes.SHA256())
    print("Сертификат разработчика успешно создан.")

    # =====================================================================
    # ЧАСТЬ 2: СБОРКА PFX И ПОДПИСЬ ЧЕРЕЗ OSSLSIGNCODE
    # =====================================================================

    print("3. Экспорт ключей в формат PFX...")
    pfx_data = pkcs12.serialize_key_and_certificates(
        name=b"code_sign",
        key=dev_private_key,
        cert=dev_certificate,
        cas=[ca_cert],
        encryption_algorithm=serialization.BestAvailableEncryption(pfx_password)
    )

    temp_pfx_path = "temp_dev_bundle.pfx"
    with open(temp_pfx_path, "wb") as f:
        f.write(pfx_data)


def sign_exe(file: Path, out: Path):
    if not os.path.exists(SIGN / 'temp_dev_bundle.pfx'):
        print('PFX not found, generating')
        cert_generate_from_ca()


    file = Path(file).resolve()
    out = Path(out).resolve()
    filename = file.name

    print(f"Запуск подписи {filename}")

    # Папка, где лежит утилита подписи (tools/sign)
    tools_dir = Path('./sign').resolve()
    # print(tools_dir)

    # Файл PFX создается в папке tools/sign (убедитесь, что в части кода с созданием PFX путь такой же!)
    # temp_pfx_name = "temp_dev_bundle.pfx"

    # Будущий путь к подписанному файлу
    final_output_path = out / f'signed_{filename}'
    if final_output_path.exists():
        final_output_path.unlink()
    ossl_executable = (tools_dir / "osslsigncode.exe").resolve()
    # Точно такой же чистый массив аргументов, как в работающем коде
    cmd = [
        str(ossl_executable), "sign",
        "-pkcs12", temp_pfx_path,  # Относительное имя PFX
        "-pass", pfx_password.decode(),
        "-h", "sha256",
        "-in", str(file),  # Полный путь к исходному EXE
        "-out", str(final_output_path)  # Полный путь к итоговому EXE
    ]

    print("Выполняемая команда:", cmd)
    try:
        # Секрет успеха: параметр cwd=tools_dir
        # Переносим контекст выполнения процесса внутрь папки 'tools/sign'
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
            cwd=tools_dir  # Процесс запустится ТАК, будто все файлы лежат рядом с ним
        )
        print("Утилита успешно завершила работу:")
        print(result.stdout)
        print(f"\nУспех! Файл '{final_output_path}' успешно создан и подписан.")
    except subprocess.CalledProcessError as e:
        print("Ошибка при выполнении osslsigncode:")
        print(e.stderr)

