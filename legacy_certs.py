import os
import re
import subprocess
from pathlib import Path
from typing import Dict, Tuple, Union, Optional, List
from dataclasses import dataclass
import logging

# Настройка логирования
logging.basicConfig(level=logging.DEBUG, format='%(levelname)s:%(name)s:%(message)s')
logger = logging.getLogger(__name__)


@dataclass
class CertOperationResult:
    """Результат операции с сертификатом"""
    success: bool
    pfx_error: str = '0x00000000'
    cer_error: str = '0x00000000'
    password_error: str = '0x00000000'
    container: str = ''

    def __bool__(self):
        return self.success


class CertificateManager:
    """Менеджер для работы с сертификатами"""

    def __init__(self, csp_path: str = '.\\'):
        self.csp_path = Path(csp_path)
        self._validate_csp_path()

    def _validate_csp_path(self):
        """Проверяет существование пути к CSP утилитам"""
        if not self.csp_path.exists():
            raise FileNotFoundError(f"CSP path not found: {self.csp_path}")

        required_tools = ['certmgr.exe', 'csptest.exe']
        missing_tools = [tool for tool in required_tools
                         if not (self.csp_path / tool).exists()]

        if missing_tools:
            logger.warning(f"Missing CSP tools: {missing_tools}")

    def _run_command(self, command: str) -> str:
        """Безопасное выполнение команды"""
        try:
            # Устанавливаем кодировку для корректного отображения русского текста
            subprocess.run("chcp 1251", shell=True, capture_output=True)

            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                encoding='cp1251'
            )

            if result.returncode != 0:
                logger.error(f"Command failed: {command}")
                logger.error(f"Error: {result.stderr}")

            return result.stdout

        except Exception as e:
            logger.error(f"Error executing command: {e}")
            return ""

    def _extract_error_code(self, output: str) -> str:
        """Извлекает код ошибки из вывода команды"""
        for line in output.split('\n'):
            if "ErrorCode" in line:
                return line.split(':')[1].strip().replace(']', '')
        return '0x00000000'

    def _extract_container(self, output: str) -> str:
        """Извлекает имя контейнера из вывода команды"""
        for line in output.split('\n'):
            if "Container" in line:
                return line.split(':')[1].strip()
        return ""

    def deploy_certificate(self, pfx_path: str, cer_path: str,
                           pin: str = "00000000") -> CertOperationResult:
        """
        Развертывает сертификат и ключ

        Args:
            pfx_path: Путь к PFX файлу
            cer_path: Путь к CER файлу
            pin: PIN-код для PFX файла

        Returns:
            CertOperationResult: Результат операции
        """
        logger.info(f"Deploying certificate: PFX={pfx_path}, CER={cer_path}")

        # Проверяем существование файлов
        if not Path(pfx_path).exists():
            logger.error(f"PFX file not found: {pfx_path}")
            return CertOperationResult(success=False)

        if not Path(cer_path).exists():
            logger.error(f"CER file not found: {cer_path}")
            return CertOperationResult(success=False)

        result = CertOperationResult(success=False)

        # Устанавливаем PFX
        pfx_cmd = (f'"{self.csp_path / "certmgr.exe"}" -install -store uMy '
                   f'-file "{pfx_path}" -pfx -silent -keep_exportable -pin {pin}')

        pfx_output = self._run_command(pfx_cmd)
        print(pfx_output)
        result.pfx_error = self._extract_error_code(pfx_output)
        result.container = self._extract_container(pfx_output)

        logger.info(f"PFX install result: {result.pfx_error}, Container: {result.container}")

        if result.pfx_error != '0x00000000' or not result.container:
            logger.error("Failed to install PFX certificate")
            return result

        # Устанавливаем CER
        cer_cmd = (f'"{self.csp_path / "certmgr.exe"}" -install -store uMy '
                   f'-file "{cer_path}" -certificate -container "{result.container}" '
                   f'-silent -inst_to_cont')

        cer_output = self._run_command(cer_cmd)
        result.cer_error = self._extract_error_code(cer_output)

        logger.info(f"CER install result: {result.cer_error}")

        if result.cer_error != '0x00000000':
            logger.error("Failed to install CER certificate")
            return result

        # Меняем пароль контейнера
        passwd_cmd = (f'"{self.csp_path / "csptest.exe"}" -passwd '
                      f'-container "{result.container}" -change {pin}')

        passwd_output = self._run_command(passwd_cmd)
        result.password_error = self._extract_error_code(passwd_output)

        logger.info(f"Password change result: {result.password_error}")

        # Проверяем общий результат
        result.success = all([
            result.pfx_error == '0x00000000',
            result.cer_error == '0x00000000',
            result.password_error == '0x00000000'
        ])

        if result.success:
            logger.info("Certificate deployment successful")
        else:
            logger.error(f"Certificate deployment failed: {result}")

        return result

    def list_certificates(self) -> Dict[int, Dict[str, str]]:
        """
        Возвращает список установленных сертификатов

        Returns:
            Dict: Словарь с информацией о сертификатах
        """
        logger.info("Listing certificates")

        list_cmd = f'"{self.csp_path / "certmgr.exe"}" -list'
        output = self._run_command(list_cmd)

        if not output.strip():
            logger.warning("No output from certmgr.exe -list command")
            return {}

        logger.debug(f"Raw output length: {len(output)} characters")
        logger.debug(f"First 200 chars: {output[:200]}")

        return self._parse_certificate_list(output)

    def _parse_certificate_list(self, output: str) -> Dict[int, Dict[str, str]]:
        """Парсит список сертификатов"""
        certificates = {}

        for index, cert_block in enumerate(output.split('-------')):
            if ' : ' not in cert_block or cert_block.strip() == '':
                continue

            cert_info = {}

            # Парсим основные поля
            for line in cert_block.split('\n'):
                line = re.sub(r'  +', ' ', line.strip())
                if ' : ' in line:  # Проверяем именно разделитель ' : '
                    parts = line.split(' : ', 1)  # Разделяем только по первому вхождению
                    if len(parts) == 2:  # Убеждаемся, что получили 2 части
                        key, value = parts
                        cert_info[key.strip()] = value.strip()
                    else:
                        logger.warning(f"Skipping malformed line: {line}")

            # Парсим Subject детально
            if 'Subject' in cert_info:
                try:
                    subject_parts = cert_info['Subject'].split(', ')
                    for part in subject_parts:
                        if '=' in part:
                            key_value = part.split('=', 1)
                            if len(key_value) == 2:
                                key, value = key_value
                                cert_info[f"Subject_{key.strip()}"] = value.strip()
                except Exception as e:
                    logger.warning(f"Error parsing Subject: {e}")

            if cert_info:  # Добавляем только если есть данные
                sub_cn = (cert_info['Subject_CN'] if 'Subject_CN' in cert_info else "-")
                certificates[f"{index}_{sub_cn}"] = cert_info

        logger.info(f"Found {len(certificates)} certificates")
        return certificates

    def find_certificate_by_subject(self, subject_pattern: str) -> Optional[Dict[str, str]]:
        """
        Находит сертификат по паттерну в Subject

        Args:
            subject_pattern: Паттерн для поиска в Subject

        Returns:
            Dict или None: Информация о найденном сертификате
        """
        certificates = self.list_certificates()

        for cert_info in certificates.values():
            if 'Subject' in cert_info and subject_pattern.lower() in cert_info['Subject'].lower():
                return cert_info

        return None

    def find_certificates_by_subject(self, subject_pattern: str) -> Optional[List[Dict[str, str]]]:
        """
        Находит сертификаты по паттерну в Subject

        Args:
            subject_pattern: Паттерн для поиска в Subject

        Returns:
            List: Информация о найденном сертификате
        """
        certificates = self.list_certificates()
        result = []
        for cert_info in certificates.values():
            if 'Subject' in cert_info and subject_pattern.lower() in cert_info['Subject'].lower():
                result.append(cert_info)

        return result

    def export_certificate_pfx(self, container_name: str, output_path: str,
                               password: str = "00000000") -> bool | str:
        """
        Экспортирует сертификат с закрытым ключом в PFX файл

        Args:
            container_name: Имя контейнера
            output_path: Путь для сохранения PFX файла
            password: Пароль для PFX файла

        Returns:
            bool: True если экспорт успешен
        """
        logger.info(f"Exporting PFX: container={container_name}, output={output_path}")

        export_cmd = (f'"{self.csp_path / "certmgr.exe"}" -export '
                      f'-container "{container_name}" -dest "{output_path}" '
                      f'-pfx -keep_exportable -pin {password}')

        output = self._run_command(export_cmd)
        error_code = self._extract_error_code(output)

        success = error_code == '0x00000000'

        if success:
            logger.info(f"PFX export successful: {output_path}")
            # Проверяем, что файл действительно создан
            if not Path(output_path).exists():
                logger.error(f"PFX file was not created: {output_path}")
                return False
            else:
                with open(output_path, 'rb') as f:
                    return f.read()
        else:
            logger.error(f"PFX export failed: {error_code}")
            logger.debug(f"Export output: {output}")
            return False

    def export_certificate_cer(self, container_name: str = None,
                               thumbprint: str = None, output_path: str = None) -> bool | str:
        """
        Экспортирует открытую часть сертификата в CER файл

        Args:
            container_name: Имя контейнера (один из параметров обязателен)
            thumbprint: Отпечаток сертификата (альтернатива container_name)
            output_path: Путь для сохранения CER файла

        Returns:
            bool: True если экспорт успешен
        """
        if not container_name and not thumbprint:
            logger.error("Either container_name or thumbprint must be provided")
            return False

        if not output_path:
            logger.error("Output path must be provided")
            return False

        logger.info(f"Exporting CER: container={container_name}, thumbprint={thumbprint}, output={output_path}")

        # Для экспорта CER используем -dest вместо -file
        if container_name:
            export_cmd = (f'"{self.csp_path / "certmgr.exe"}" -export '
                          f'-container "{container_name}" -dest "{output_path}"')
        else:
            export_cmd = (f'"{self.csp_path / "certmgr.exe"}" -export '
                          f'-thumbprint "{thumbprint}" -dest "{output_path}"')

        output = self._run_command(export_cmd)
        error_code = self._extract_error_code(output)

        success = error_code == '0x00000000'

        if success:
            logger.info(f"CER export successful: {output_path}")
            # Проверяем, что файл действительно создан
            if not Path(output_path).exists():
                logger.error(f"CER file was not created: {output_path}")
                return False
            else:
                with open(output_path, 'rb') as f:
                    return f.read()
        else:
            logger.error(f"CER export failed: {error_code}")
            logger.debug(f"Export output: {output}")

        return False

    def export_certificate_by_subject(self, subject_pattern: str,
                                      output_pfx: str = None, output_cer: str = None,
                                      password: str = "00000000", use_alternative: bool = False) -> Dict[str, bool]:
        """
        Находит и экспортирует сертификат по паттерну Subject

        Args:
            subject_pattern: Паттерн для поиска в Subject
            output_pfx: Путь для PFX файла (опционально)
            output_cer: Путь для CER файла (опционально)
            password: Пароль для PFX файла
            use_alternative: Использовать альтернативный метод экспорта

        Returns:
            Dict[str, bool]: Результаты экспорта {'pfx': bool, 'cer': bool}
        """
        results = {'pfx': False, 'cer': False}

        # Находим сертификат
        cert_info = self.find_certificate_by_subject(subject_pattern)
        if not cert_info:
            logger.error(f"Certificate not found by subject pattern: {subject_pattern}")
            return results

        container_name = cert_info.get('Container', '')
        thumbprint = cert_info.get('Thumbprint', '')

        logger.info(f"Found certificate: Subject={cert_info.get('Subject', 'N/A')}")
        logger.info(f"Container: {container_name}")
        logger.info(f"Thumbprint: {thumbprint}")

        if not container_name and not thumbprint:
            logger.error("Certificate has no container name or thumbprint")
            return results

        # Экспортируем PFX если указан путь
        if output_pfx:
            if container_name:
                results['pfx'] = self.export_certificate_pfx(container_name, output_pfx, password)
            else:
                logger.warning("Cannot export PFX without container name")

        # Экспортируем CER если указан путь
        if output_cer:
            results['cer'] = self.export_certificate_cer(
                container_name=container_name,
                thumbprint=thumbprint,
                output_path=output_cer
            )

        return results

    def export_certificates_by_subject(self, subject_pattern: str, password: str = "00000000",
                                       use_alternative: bool = False) -> List[Dict[str, bool]]:
        """
        Находит и экспортирует сертификат по паттерну Subject

        Args:
            subject_pattern: Паттерн для поиска в Subject
            password: Пароль для PFX файла
            use_alternative: Использовать альтернативный метод экспорта

        Returns:
            Dict[str, bool]: Результаты экспорта {'pfx': bool, 'cer': bool}
        """
        results = []

        # Находим сертификат
        certs_info = self.find_certificates_by_subject(subject_pattern)
        for index, cert_info in enumerate(certs_info):
            if not cert_info:
                logger.error(f"Certificate not found by subject pattern: {subject_pattern}")
                results.append({'pfx': False, 'cer': False})
                continue

            container_name = cert_info.get('Container', '')
            subject_cn = cert_info.get('Subject_CN', '')
            thumbprint = cert_info.get('Thumbprint', '')
            name = re.sub('\\\\', f'_{subject_cn}_', container_name, )

            logger.info(f"Found certificate: Subject={cert_info.get('Subject', 'N/A')}")
            logger.info(f"Container: {container_name}")
            logger.info(f"Thumbprint: {thumbprint}")

            if not container_name and not thumbprint:
                logger.error("Certificate has no container name or thumbprint")
                results.append({'pfx': False, 'cer': False})
                continue

            print(results, index)
            result = {}
            # Экспортируем PFX если указан путь
            if container_name:
                result['pfx'] = self.export_certificate_pfx(container_name, f"{name}.pfx", password)
            else:
                logger.warning("Cannot export PFX without container name")

            # Экспортируем CER если указан путь
            result['cer'] = self.export_certificate_cer(
                container_name=container_name,
                thumbprint=thumbprint,
                output_path=f"{name}.cer"
            )
            results.append(result)

        return results

    def get_certificate_info(self, container_name: str) -> Optional[Dict[str, str]]:
        """
        Получает подробную информацию о сертификате по имени контейнера

        Args:
            container_name: Имя контейнера

        Returns:
            Dict или None: Информация о сертификате
        """
        certificates = self.list_certificates()

        for cert_info in certificates.values():
            if cert_info.get('Container', '') == container_name:
                return cert_info

        return None

    def get_certmgr_help(self) -> str:
        """
        Получает справку по командам certmgr.exe для отладки

        Returns:
            str: Справка по командам
        """
        help_cmd = f'"{self.csp_path / "certmgr.exe"}" -help'
        return self._run_command(help_cmd)

    def export_certificate_alternative(self, container_name: str = None,
                                       thumbprint: str = None,
                                       output_path: str = None,
                                       export_type: str = "cer",
                                       password: str = "00000000") -> bool:
        """
        Альтернативный метод экспорта с разными вариантами команд

        Args:
            container_name: Имя контейнера
            thumbprint: Отпечаток сертификата
            output_path: Путь для сохранения
            export_type: Тип экспорта ("cer" или "pfx")
            password: Пароль для PFX

        Returns:
            bool: True если экспорт успешен
        """
        if not container_name and not thumbprint:
            logger.error("Either container_name or thumbprint must be provided")
            return False

        if not output_path:
            logger.error("Output path must be provided")
            return False

        logger.info(f"Alternative export: type={export_type}, container={container_name}, output={output_path}")

        # Пробуем разные варианты команд экспорта
        commands_to_try = []

        if export_type.lower() == "pfx" and container_name:
            # Варианты команд для PFX
            commands_to_try = [
                f'"{self.csp_path / "certmgr.exe"}" -export -container "{container_name}" -dest "{output_path}" -pfx -pin {password}',
                f'"{self.csp_path / "certmgr.exe"}" -export -pfx -container "{container_name}" -dest "{output_path}" -pin {password}',
                f'"{self.csp_path / "certmgr.exe"}" -export -container "{container_name}" -file "{output_path}" -pfx -pin {password}',
            ]
        elif export_type.lower() == "cer":
            # Варианты команд для CER
            if container_name:
                commands_to_try = [
                    f'"{self.csp_path / "certmgr.exe"}" -export -container "{container_name}" -dest "{output_path}"',
                    f'"{self.csp_path / "certmgr.exe"}" -export -cer -container "{container_name}" -dest "{output_path}"',
                    f'"{self.csp_path / "certmgr.exe"}" -export -container "{container_name}" -file "{output_path}"',
                    f'"{self.csp_path / "certmgr.exe"}" -export -cer -container "{container_name}" -file "{output_path}"',
                ]
            else:  # thumbprint
                commands_to_try = [
                    f'"{self.csp_path / "certmgr.exe"}" -export -thumbprint "{thumbprint}" -dest "{output_path}"',
                    f'"{self.csp_path / "certmgr.exe"}" -export -cer -thumbprint "{thumbprint}" -dest "{output_path}"',
                    f'"{self.csp_path / "certmgr.exe"}" -export -thumbprint "{thumbprint}" -file "{output_path}"',
                ]

        # Пробуем команды по очереди
        for cmd in commands_to_try:
            logger.debug(f"Trying command: {cmd}")
            output = self._run_command(cmd)
            error_code = self._extract_error_code(output)

            if error_code == '0x00000000':
                logger.info(f"Export successful with command: {cmd}")
                # Проверяем, что файл создан
                if Path(output_path).exists():
                    return True
                else:
                    logger.warning(f"Command succeeded but file not found: {output_path}")
            else:
                logger.debug(f"Command failed with error: {error_code}")
                logger.debug(f"Output: {output}")

        logger.error(f"All export commands failed for {export_type}")
        return False


# Пример использования
def main():
    """Пример использования класса CertificateManager"""
    try:
        # Создаем менеджер сертификатов
        cert_manager = CertificateManager('.\\')

        # Развертываем сертификат
        # result = cert_manager.deploy_certificate('cert.pfx', 'cert.cer')
        # if result:
        #     print("Сертификат успешно установлен")
        # else:
        #     print(f"Ошибка установки: {result}")

        # Получаем список сертификатов
        certificates = cert_manager.list_certificates()
        for cert_id, cert_info in certificates.items():
            print(f"\nСертификат {cert_id}:")
            for key, value in cert_info.items():
                print(f"  {key}: {value}")

        # Поиск сертификата
        # found_cert = cert_manager.find_certificate_by_subject("васина")
        # if found_cert:
        #     print(f"Найден сертификат: {found_cert['Subject']}")

        # Примеры экспорта сертификатов:

        # 0. Получаем справку по certmgr.exe для отладки
        # help_info = cert_manager.get_certmgr_help()
        # print("CertMgr Help:")
        # print(help_info)

        # 1. Экспорт по имени контейнера (исправленная версия)
        # success = cert_manager.export_certificate_pfx(
        #     container_name="HDIMAGE\\test_container",
        #     output_path="exported_cert.pfx",
        #     password="12345678"
        # )
        # print(f"PFX экспорт: {'успешно' if success else 'неудачно'}")

        # success = cert_manager.export_certificate_cer(
        #     container_name="HDIMAGE\\test_container",
        #     output_path="exported_cert.cer"
        # )
        # print(f"CER экспорт: {'успешно' if success else 'неудачно'}")

        # 2. Альтернативный экспорт (пробует разные варианты команд)
        # success = cert_manager.export_certificate_alternative(
        #     container_name="Васина Ольга Сергеевна 422111242",
        #     output_path="alternative_export.cer",
        #     export_type="cer"
        # )
        # print(f"Альтернативный CER экспорт: {'успешно' if success else 'неудачно'}")

        # 3. Экспорт по паттерну Subject (теперь с альтернативным методом)
        results = cert_manager.export_certificates_by_subject(
            subject_pattern="васина",
            password="87654321",
            use_alternative=False  # Используем альтернативный метод
        )
        print(f"Экспорт по Subject - {results}")

        # 4. Получение информации о сертификате
        # cert_info = cert_manager.get_certificate_info("HDIMAGE\\test_container")
        # if cert_info:
        #     print(f"Информация о сертификате: {cert_info}")

    except Exception as e:
        logger.error(f"Error in main: {e}")


if __name__ == "__main__":
    main()
