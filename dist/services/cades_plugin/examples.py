"""
Примеры использования CAdES Plugin Service

Этот файл содержит примеры использования сервиса для различных сценариев.
"""

import asyncio
import base64
from pathlib import Path


# ==================== Пример 1: Импорт сертификата ====================

async def example_import_certificate(proxy):
    """
    Импорт сертификата из PKCS#12 файла
    """
    print("=" * 60)
    print("Пример 1: Импорт сертификата")
    print("=" * 60)

    # Читаем PFX файл
    pfx_path = Path("path/to/certificate.pfx")
    with open(pfx_path, 'rb') as f:
        pfx_data = f.read()

    # Кодируем в base64
    pfx_base64 = base64.b64encode(pfx_data).decode('utf-8')

    # Импортируем
    result = await proxy.cades_plugin.import_certificate(
        pfx_base64=pfx_base64,
        password="12345678"
    )

    if result['success']:
        print("✓ Сертификат импортирован успешно!")
        cert = result['certificate']
        print(f"  Субъект: {cert['subject_cn']}")
        print(f"  Отпечаток: {cert['thumbprint']}")
        print(f"  Действителен с: {cert['valid_from']}")
        print(f"  Действителен до: {cert['valid_to']}")
    else:
        print(f"✗ Ошибка импорта: {result['error']}")

    print()


# ==================== Пример 2: Список сертификатов ====================

async def example_list_certificates(proxy):
    """
    Получение списка всех сертификатов
    """
    print("=" * 60)
    print("Пример 2: Список сертификатов")
    print("=" * 60)

    result = await proxy.cades_plugin.list_certificates()

    if result['success']:
        print(f"Найдено сертификатов: {result['count']}\n")

        for i, cert in enumerate(result['certificates'], 1):
            print(f"{i}. {cert['subject_cn']}")
            print(f"   Отпечаток: {cert['thumbprint']}")
            print(f"   Издатель: {cert['issuer_cn']}")
            print(f"   Действителен: {cert['valid_from']} - {cert['valid_to']}")
            print(f"   Закрытый ключ: {'Да' if cert['has_private_key'] else 'Нет'}")
            print()
    else:
        print(f"✗ Ошибка: {result['error']}")

    print()


# ==================== Пример 3: Подпись текста ====================

async def example_sign_text(proxy, thumbprint: str):
    """
    Создание электронной подписи для текста
    """
    print("=" * 60)
    print("Пример 3: Подпись текста")
    print("=" * 60)

    # Текст для подписи
    text = "Важный документ, требующий подписи"
    text_base64 = base64.b64encode(text.encode('utf-8')).decode('utf-8')

    # Создаем подпись
    result = await proxy.cades_plugin.sign_data(
        data_base64=text_base64,
        thumbprint=thumbprint,
        password="12345678",
        detached=True
    )

    if result['success']:
        print("✓ Подпись создана успешно!")
        print(f"  Тип подписи: {result['signature_type']}")
        print(f"  Подписчик: {result['signer']}")
        print(f"  Длина подписи: {len(result['signature'])} символов")

        # Сохраняем подпись в файл
        signature_path = Path("signature.sig")
        with open(signature_path, 'w') as f:
            f.write(result['signature'])

        print(f"  Подпись сохранена в: {signature_path}")
    else:
        print(f"✗ Ошибка создания подписи: {result['error']}")

    print()


# ==================== Пример 4: Подпись файла ====================

async def example_sign_file(proxy, thumbprint: str):
    """
    Создание электронной подписи для файла
    """
    print("=" * 60)
    print("Пример 4: Подпись файла")
    print("=" * 60)

    # Читаем файл
    file_path = Path("document.pdf")
    with open(file_path, 'rb') as f:
        file_data = f.read()

    # Кодируем в base64
    file_base64 = base64.b64encode(file_data).decode('utf-8')

    # Создаем подпись
    result = await proxy.cades_plugin.sign_data(
        data_base64=file_base64,
        thumbprint=thumbprint,
        password="12345678",
        detached=True
    )

    if result['success']:
        print("✓ Файл подписан успешно!")
        print(f"  Исходный файл: {file_path}")
        print(f"  Подписчик: {result['signer']}")

        # Сохраняем подпись
        signature_path = file_path.with_suffix('.sig')
        with open(signature_path, 'w') as f:
            f.write(result['signature'])

        print(f"  Подпись сохранена в: {signature_path}")
    else:
        print(f"✗ Ошибка подписи файла: {result['error']}")

    print()


# ==================== Пример 5: Проверка подписи ====================

async def example_verify_signature(proxy):
    """
    Проверка электронной подписи
    """
    print("=" * 60)
    print("Пример 5: Проверка подписи")
    print("=" * 60)

    # Читаем исходные данные
    text = "Важный документ, требующий подписи"
    text_base64 = base64.b64encode(text.encode('utf-8')).decode('utf-8')

    # Читаем подпись
    signature_path = Path("signature.sig")
    with open(signature_path, 'r') as f:
        signature_base64 = f.read()

    # Проверяем подпись
    result = await proxy.cades_plugin.verify_signature(
        data_base64=text_base64,
        signature_base64=signature_base64
    )

    if result['success']:
        if result['is_valid']:
            print("✓ Подпись действительна!")
            sig_info = result['signature_info']
            print(f"  Подписчик: {sig_info['signer_name']}")
            print(f"  Отпечаток: {sig_info['signer_thumbprint']}")
            print(f"  Время подписи: {sig_info['signing_time']}")
            print(f"  Алгоритм: {sig_info['signature_type']}")
        else:
            print("✗ Подпись недействительна!")
            if result.get('error'):
                print(f"  Ошибка: {result['error']}")
    else:
        print(f"✗ Ошибка проверки: {result['error']}")

    print()


# ==================== Пример 6: Получение публичного ключа ====================

async def example_get_public_key(proxy, thumbprint: str):
    """
    Получение публичного ключа сертификата
    """
    print("=" * 60)
    print("Пример 6: Получение публичного ключа")
    print("=" * 60)

    result = await proxy.cades_plugin.get_public_key(
        thumbprint=thumbprint,
        password="12345678"
    )

    if result['success']:
        print("✓ Публичный ключ получен!")

        # Сохраняем в файл
        cert_path = Path("certificate.pem")
        with open(cert_path, 'w') as f:
            f.write(result['certificate_pem'])

        print(f"  Сертификат сохранен в: {cert_path}")
        print("\nПервые строки сертификата:")
        print(result['certificate_pem'][:200] + "...")
    else:
        print(f"✗ Ошибка: {result['error']}")

    print()


# ==================== Пример 7: Удаление сертификата ====================

async def example_delete_certificate(proxy, thumbprint: str):
    """
    Удаление сертификата из хранилища
    """
    print("=" * 60)
    print("Пример 7: Удаление сертификата")
    print("=" * 60)

    print(f"Удаление сертификата: {thumbprint}")

    result = await proxy.cades_plugin.delete_certificate(
        thumbprint=thumbprint
    )

    if result['success']:
        print("✓ Сертификат удален успешно!")
        print(f"  {result['message']}")
    else:
        print(f"✗ Ошибка удаления: {result['error']}")

    print()


# ==================== Пример 8: Статистика ====================

async def example_get_statistics(proxy):
    """
    Получение статистики использования сервиса
    """
    print("=" * 60)
    print("Пример 8: Статистика сервиса")
    print("=" * 60)

    result = await proxy.cades_plugin.get_statistics()

    if result['success']:
        stats = result['statistics']
        print("Статистика:")
        print(f"  Всего сертификатов: {stats['total_certificates']}")
        print(f"  Подписей создано: {stats['total_signatures_created']}")
        print(f"  Подписей проверено: {stats['total_signatures_verified']}")
        print(f"  Время работы: {stats['service_uptime']} сек")
        print(f"  Версия сервиса: {stats['service_version']}")
    else:
        print(f"✗ Ошибка: {result['error']}")

    print()


# ==================== Пример 9: Пакетная обработка ====================

async def example_batch_signing(proxy, thumbprint: str):
    """
    Пакетная подпись нескольких документов
    """
    print("=" * 60)
    print("Пример 9: Пакетная подпись документов")
    print("=" * 60)

    documents = [
        "Документ №1",
        "Документ №2",
        "Документ №3"
    ]

    results = []

    for i, doc in enumerate(documents, 1):
        print(f"Подписание документа {i}/{len(documents)}...")

        doc_base64 = base64.b64encode(doc.encode('utf-8')).decode('utf-8')

        result = await proxy.cades_plugin.sign_data(
            data_base64=doc_base64,
            thumbprint=thumbprint,
            password="12345678",
            detached=True
        )

        results.append((doc, result))

    print("\nРезультаты:")
    for doc, result in results:
        status = "✓" if result['success'] else "✗"
        print(f"{status} {doc}: {result.get('signature_type', result.get('error'))}")

    print()


# ==================== Пример 10: Проверка срока действия ====================

async def example_check_certificate_validity(proxy):
    """
    Проверка срока действия сертификатов
    """
    print("=" * 60)
    print("Пример 10: Проверка срока действия")
    print("=" * 60)

    from datetime import datetime

    result = await proxy.cades_plugin.list_certificates()

    if result['success']:
        now = datetime.utcnow()

        for cert in result['certificates']:
            valid_to = datetime.fromisoformat(cert['valid_to'].replace('Z', '+00:00'))
            days_left = (valid_to - now).days

            status = "✓ Действителен" if days_left > 0 else "✗ Истек"
            print(f"{status} - {cert['subject_cn']}")
            print(f"   Осталось дней: {days_left}")

            if 0 < days_left < 30:
                print(f"   ⚠️  Внимание! Сертификат истекает через {days_left} дней!")

            print()
    else:
        print(f"✗ Ошибка: {result['error']}")

    print()


# ==================== Главная функция ====================

async def run_all_examples():
    """
    Запустить все примеры

    Примечание: Требует запущенный P2P Core с сервисом cades_plugin
    """
    # TODO: Получить proxy объект из вашего P2P контекста
    # proxy = context.proxy или similar

    print("CAdES Plugin - Примеры использования")
    print()

    # Получаем список сертификатов для использования в примерах
    # result = await proxy.cades_plugin.list_certificates()
    # if result['success'] and result['count'] > 0:
    #     thumbprint = result['certificates'][0]['thumbprint']
    # else:
    #     print("Нет сертификатов в хранилище. Запустите example_import_certificate сначала.")
    #     return

    # Запускаем примеры
    # await example_import_certificate(proxy)
    # await example_list_certificates(proxy)
    # await example_sign_text(proxy, thumbprint)
    # await example_sign_file(proxy, thumbprint)
    # await example_verify_signature(proxy)
    # await example_get_public_key(proxy, thumbprint)
    # await example_get_statistics(proxy)
    # await example_batch_signing(proxy, thumbprint)
    # await example_check_certificate_validity(proxy)
    # await example_delete_certificate(proxy, thumbprint)

    print("Все примеры выполнены!")


if __name__ == "__main__":
    print("Этот файл содержит примеры использования.")
    print("Запустите примеры через ваш P2P сервис с доступом к proxy.")
