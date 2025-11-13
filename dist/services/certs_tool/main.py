import os
import re
import subprocess
import asyncio
from pathlib import Path
from typing import Dict, Tuple, Union, Optional, List, Any
from dataclasses import dataclass, asdict
import logging
from layers.service import BaseService, service_method


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

    def to_dict(self):
        return asdict(self)


class LegacyCertsServiceError(Exception):
    """Базовое исключение сервиса управления сертификатами"""
    pass


class CertificateDeploymentError(LegacyCertsServiceError):
    """Ошибка развертывания сертификата"""
    pass


class CertificateExportError(LegacyCertsServiceError):
    """Ошибка экспорта сертификата"""
    pass


class LegacyCertsService(BaseService):
    """
    Сервис управления legacy сертификатами CSP

    Обеспечивает:
    - Развертывание сертификатов из PFX/CER файлов
    - Экспорт сертификатов в PFX/CER формат
    - Поиск и листинг установленных сертификатов
    - Управление контейнерами ключей
    """

    SERVICE_NAME = "legacy_certs"

    def __init__(self, service_name: str = "legacy_certs", proxy_client=None, csp_path: str = None):
        super().__init__(service_name, proxy_client)
        # Если путь не указан, используем директорию сервиса
        if csp_path is None:
            self.csp_path = Path(__file__).parent
        else:
            self.csp_path = Path(csp_path)

    async def initialize(self):
        """Инициализация сервиса"""
        self.logger.info("Initializing Legacy Certs Service")
        self._validate_csp_path()
        self.logger.info("Legacy Certs Service initialized")

    async def cleanup(self):
        """Очистка ресурсов при остановке"""
        self.logger.info("Legacy Certs Service cleanup")

    def _validate_csp_path(self):
        """Проверяет существование пути к CSP утилитам"""
        if not self.csp_path.exists():
            self.logger.warning(f"CSP path not found: {self.csp_path}")
            return

        required_tools = ['certmgr.exe', 'csptest.exe']
        missing_tools = [tool for tool in required_tools
                         if not (self.csp_path / tool).exists()]

        if missing_tools:
            self.logger.warning(f"Missing CSP tools: {missing_tools}")

    async def _run_command_async(self, command: str) -> str:
        """Асинхронное выполнение команды"""
        try:
            # Устанавливаем кодировку для корректного отображения русского текста
            await asyncio.create_subprocess_shell(
                "chcp 1251",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            stdout, stderr = await process.communicate()

            # Пробуем декодировать с cp1251
            try:
                output = stdout.decode('cp1251')
            except UnicodeDecodeError:
                output = stdout.decode('utf-8', errors='ignore')

            if process.returncode != 0:
                self.logger.error(f"Command failed: {command}")
                try:
                    error_msg = stderr.decode('cp1251')
                except UnicodeDecodeError:
                    error_msg = stderr.decode('utf-8', errors='ignore')
                self.logger.error(f"Error: {error_msg}")

            return output

        except Exception as e:
            self.logger.error(f"Error executing command: {e}")
            return ""

    def _run_command(self, command: str) -> str:
        """Синхронное выполнение команды"""
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
                self.logger.error(f"Command failed: {command}")
                self.logger.error(f"Error: {result.stderr}")

            return result.stdout

        except Exception as e:
            self.logger.error(f"Error executing command: {e}")
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

    @service_method(description="Deploy certificate and key from PFX and CER files", public=True)
    async def deploy_certificate(self, pfx_path: str, cer_path: str,
                                  pin: str = "00000000") -> Dict:
        """
        Развертывает сертификат и ключ

        Args:
            pfx_path: Путь к PFX файлу
            cer_path: Путь к CER файлу
            pin: PIN-код для PFX файла

        Returns:
            Dict: Результат операции
        """
        self.logger.info(f"Deploying certificate: PFX={pfx_path}, CER={cer_path}")

        # Проверяем существование файлов
        if not Path(pfx_path).exists():
            self.logger.error(f"PFX file not found: {pfx_path}")
            raise CertificateDeploymentError(f"PFX file not found: {pfx_path}")

        if not Path(cer_path).exists():
            self.logger.error(f"CER file not found: {cer_path}")
            raise CertificateDeploymentError(f"CER file not found: {cer_path}")

        result = CertOperationResult(success=False)

        # Устанавливаем PFX
        pfx_cmd = (f'"{self.csp_path / "certmgr.exe"}" -install -store uMy '
                   f'-file "{pfx_path}" -pfx -silent -keep_exportable -pin {pin}')

        pfx_output = await self._run_command_async(pfx_cmd)
        result.pfx_error = self._extract_error_code(pfx_output)
        result.container = self._extract_container(pfx_output)

        self.logger.info(f"PFX install result: {result.pfx_error}, Container: {result.container}")

        if result.pfx_error != '0x00000000' or not result.container:
            self.logger.error("Failed to install PFX certificate")
            return result.to_dict()

        # Устанавливаем CER
        cer_cmd = (f'"{self.csp_path / "certmgr.exe"}" -install -store uMy '
                   f'-file "{cer_path}" -certificate -container "{result.container}" '
                   f'-silent -inst_to_cont')

        cer_output = await self._run_command_async(cer_cmd)
        result.cer_error = self._extract_error_code(cer_output)

        self.logger.info(f"CER install result: {result.cer_error}")

        if result.cer_error != '0x00000000':
            self.logger.error("Failed to install CER certificate")
            return result.to_dict()

        # Меняем пароль контейнера
        passwd_cmd = (f'"{self.csp_path / "csptest.exe"}" -passwd '
                      f'-container "{result.container}" -change {pin}')

        passwd_output = await self._run_command_async(passwd_cmd)
        result.password_error = self._extract_error_code(passwd_output)

        self.logger.info(f"Password change result: {result.password_error}")

        # Проверяем общий результат
        result.success = all([
            result.pfx_error == '0x00000000',
            result.cer_error == '0x00000000',
            result.password_error == '0x00000000'
        ])

        if result.success:
            self.logger.info("Certificate deployment successful")
        else:
            self.logger.error(f"Certificate deployment failed: {result}")

        return result.to_dict()

    @service_method(description="Get list of installed certificates", public=True)
    async def list_certificates(self) -> Dict[str, Dict[str, str]]:
        """
        Возвращает список установленных сертификатов

        Returns:
            Dict: Словарь с информацией о сертификатах
        """
        self.logger.info("Listing certificates")

        list_cmd = f'"{self.csp_path / "certmgr.exe"}" -list'
        output = await self._run_command_async(list_cmd)

        if not output.strip():
            self.logger.warning("No output from certmgr.exe -list command")
            return {}

        self.logger.debug(f"Raw output length: {len(output)} characters")

        return self._parse_certificate_list(output)

    def _parse_certificate_list(self, output: str) -> Dict[str, Dict[str, str]]:
        """Парсит список сертификатов"""
        certificates = {}

        for index, cert_block in enumerate(output.split('-------')):
            if ' : ' not in cert_block or cert_block.strip() == '':
                continue

            cert_info = {}

            # Парсим основные поля
            for line in cert_block.split('\n'):
                line = re.sub(r'  +', ' ', line.strip())
                if ' : ' in line:
                    parts = line.split(' : ', 1)
                    if len(parts) == 2:
                        key, value = parts
                        cert_info[key.strip()] = value.strip()
                    else:
                        self.logger.warning(f"Skipping malformed line: {line}")

            # Log all raw fields for debugging
            self.logger.debug(f"Certificate {index} raw fields: {list(cert_info.keys())}")

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
                    self.logger.warning(f"Error parsing Subject: {e}")

            # Парсим Issuer детально
            if 'Issuer' in cert_info:
                try:
                    issuer_parts = cert_info['Issuer'].split(', ')
                    for part in issuer_parts:
                        if '=' in part:
                            key_value = part.split('=', 1)
                            if len(key_value) == 2:
                                key, value = key_value
                                cert_info[f"Issuer_{key.strip()}"] = value.strip()
                except Exception as e:
                    self.logger.warning(f"Error parsing Issuer: {e}")

            # Normalize field names - try different variants
            # Thumbprint может называться SHA1 Hash, SHA1, Hash, Thumbprint, Отпечаток
            if 'SHA1 Hash' in cert_info and 'Thumbprint' not in cert_info:
                cert_info['Thumbprint'] = cert_info['SHA1 Hash']
            elif 'SHA1' in cert_info and 'Thumbprint' not in cert_info:
                cert_info['Thumbprint'] = cert_info['SHA1']
            elif 'Hash' in cert_info and 'Thumbprint' not in cert_info:
                cert_info['Thumbprint'] = cert_info['Hash']
            elif 'Отпечаток' in cert_info and 'Thumbprint' not in cert_info:
                cert_info['Thumbprint'] = cert_info['Отпечаток']

            if cert_info:
                sub_cn = (cert_info['Subject_CN'] if 'Subject_CN' in cert_info else "-")
                certificates[f"{index}_{sub_cn}"] = cert_info
                # Log final parsed info
                self.logger.debug(f"Certificate {index}: CN={sub_cn}, Thumbprint={cert_info.get('Thumbprint', 'N/A')}")

        self.logger.info(f"Found {len(certificates)} certificates")
        return certificates

    @service_method(description="Find certificate by pattern in Subject field", public=True)
    async def find_certificate_by_subject(self, subject_pattern: str) -> Optional[Dict[str, str]]:
        """
        Находит сертификат по паттерну в Subject

        Args:
            subject_pattern: Паттерн для поиска в Subject

        Returns:
            Dict или None: Информация о найденном сертификате
        """
        certificates = await self.list_certificates()

        for cert_info in certificates.values():
            if 'Subject' in cert_info and subject_pattern.lower() in cert_info['Subject'].lower():
                return cert_info

        return None

    @service_method(description="Find all certificates by pattern in Subject field", public=True)
    async def find_certificates_by_subject(self, subject_pattern: str) -> List[Dict[str, str]]:
        """
        Находит сертификаты по паттерну в Subject

        Args:
            subject_pattern: Паттерн для поиска в Subject

        Returns:
            List: Информация о найденных сертификатах
        """
        certificates = await self.list_certificates()
        result = []
        for cert_info in certificates.values():
            if 'Subject' in cert_info and subject_pattern.lower() in cert_info['Subject'].lower():
                result.append(cert_info)

        return result

    @service_method(description="Export certificate with private key to PFX file", public=True)
    async def export_certificate_pfx(self, container_name: str, output_path: str,
                                     password: str = "00000000") -> Union[bool, bytes]:
        """
        Экспортирует сертификат с закрытым ключом в PFX файл

        Args:
            container_name: Имя контейнера
            output_path: Путь для сохранения PFX файла
            password: Пароль для PFX файла

        Returns:
            Union[bool, bytes]: True/False или bytes данные PFX файла
        """
        self.logger.info(f"Exporting PFX: container={container_name}, output={output_path}")

        export_cmd = (f'"{self.csp_path / "certmgr.exe"}" -export '
                      f'-container "{container_name}" -dest "{output_path}" '
                      f'-pfx -keep_exportable -pin {password}')

        output = await self._run_command_async(export_cmd)
        error_code = self._extract_error_code(output)

        success = error_code == '0x00000000'

        if success:
            self.logger.info(f"PFX export successful: {output_path}")
            # Проверяем, что файл действительно создан
            if not Path(output_path).exists():
                self.logger.error(f"PFX file was not created: {output_path}")
                return False
            else:
                with open(output_path, 'rb') as f:
                    return f.read()
        else:
            self.logger.error(f"PFX export failed: {error_code}")
            self.logger.debug(f"Export output: {output}")
            return False

    @service_method(description="Export public certificate to CER file", public=True)
    async def export_certificate_cer(self, container_name: str = None,
                                     thumbprint: str = None, output_path: str = None) -> Union[bool, bytes]:
        """
        Экспортирует открытую часть сертификата в CER файл

        Args:
            container_name: Имя контейнера (один из параметров обязателен)
            thumbprint: Отпечаток сертификата (альтернатива container_name)
            output_path: Путь для сохранения CER файла

        Returns:
            Union[bool, bytes]: True/False или bytes данные CER файла
        """
        if not container_name and not thumbprint:
            self.logger.error("Either container_name or thumbprint must be provided")
            raise CertificateExportError("Either container_name or thumbprint must be provided")

        if not output_path:
            self.logger.error("Output path must be provided")
            raise CertificateExportError("Output path must be provided")

        self.logger.info(f"Exporting CER: container={container_name}, thumbprint={thumbprint}, output={output_path}")

        if container_name:
            export_cmd = (f'"{self.csp_path / "certmgr.exe"}" -export '
                          f'-container "{container_name}" -dest "{output_path}"')
        else:
            export_cmd = (f'"{self.csp_path / "certmgr.exe"}" -export '
                          f'-thumbprint "{thumbprint}" -dest "{output_path}"')

        output = await self._run_command_async(export_cmd)
        error_code = self._extract_error_code(output)

        success = error_code == '0x00000000'

        if success:
            self.logger.info(f"CER export successful: {output_path}")
            if not Path(output_path).exists():
                self.logger.error(f"CER file was not created: {output_path}")
                return False
            else:
                with open(output_path, 'rb') as f:
                    return f.read()
        else:
            self.logger.error(f"CER export failed: {error_code}")
            self.logger.debug(f"Export output: {output}")

        return False

    @service_method(description="Find and export certificate by Subject pattern", public=True)
    async def export_certificate_by_subject(self, subject_pattern: str,
                                            output_pfx: str = None, output_cer: str = None,
                                            password: str = "00000000") -> Dict[str, bool]:
        """
        Находит и экспортирует сертификат по паттерну Subject

        Args:
            subject_pattern: Паттерн для поиска в Subject
            output_pfx: Путь для PFX файла (опционально)
            output_cer: Путь для CER файла (опционально)
            password: Пароль для PFX файла

        Returns:
            Dict[str, bool]: Результаты экспорта {'pfx': bool, 'cer': bool}
        """
        results = {'pfx': False, 'cer': False}

        # Находим сертификат
        cert_info = await self.find_certificate_by_subject(subject_pattern)
        if not cert_info:
            self.logger.error(f"Certificate not found by subject pattern: {subject_pattern}")
            return results

        container_name = cert_info.get('Container', '')
        thumbprint = cert_info.get('Thumbprint', '')

        self.logger.info(f"Found certificate: Subject={cert_info.get('Subject', 'N/A')}")
        self.logger.info(f"Container: {container_name}")
        self.logger.info(f"Thumbprint: {thumbprint}")

        if not container_name and not thumbprint:
            self.logger.error("Certificate has no container name or thumbprint")
            return results

        # Экспортируем PFX если указан путь
        if output_pfx:
            if container_name:
                results['pfx'] = await self.export_certificate_pfx(container_name, output_pfx, password)
            else:
                self.logger.warning("Cannot export PFX without container name")

        # Экспортируем CER если указан путь
        if output_cer:
            results['cer'] = await self.export_certificate_cer(
                container_name=container_name,
                thumbprint=thumbprint,
                output_path=output_cer
            )

        return results

    @service_method(description="Find and export all certificates by Subject pattern", public=True)
    async def export_certificates_by_subject(self, subject_pattern: str,
                                             password: str = "00000000") -> List[Dict[str, Union[bool, bytes]]]:
        """
        Находит и экспортирует сертификаты по паттерну Subject

        Args:
            subject_pattern: Паттерн для поиска в Subject
            password: Пароль для PFX файла

        Returns:
            List[Dict]: Результаты экспорта для каждого сертификата
        """
        results = []

        # Находим сертификаты
        certs_info = await self.find_certificates_by_subject(subject_pattern)
        for index, cert_info in enumerate(certs_info):
            if not cert_info:
                self.logger.error(f"Certificate not found by subject pattern: {subject_pattern}")
                results.append({'pfx': False, 'cer': False})
                continue

            container_name = cert_info.get('Container', '')
            subject_cn = cert_info.get('Subject_CN', '')
            thumbprint = cert_info.get('Thumbprint', '')
            name = re.sub('\\\\', f'_{subject_cn}_', container_name)

            self.logger.info(f"Found certificate: Subject={cert_info.get('Subject', 'N/A')}")
            self.logger.info(f"Container: {container_name}")
            self.logger.info(f"Thumbprint: {thumbprint}")

            if not container_name and not thumbprint:
                self.logger.error("Certificate has no container name or thumbprint")
                results.append({'pfx': False, 'cer': False})
                continue

            result = {}
            # Экспортируем PFX
            if container_name:
                result['pfx'] = await self.export_certificate_pfx(container_name, f"{name}.pfx", password)
            else:
                self.logger.warning("Cannot export PFX without container name")

            # Экспортируем CER
            result['cer'] = await self.export_certificate_cer(
                container_name=container_name,
                thumbprint=thumbprint,
                output_path=f"{name}.cer"
            )
            results.append(result)

        return results

    @service_method(description="Get detailed certificate information by container name", public=True)
    async def get_certificate_info(self, container_name: str) -> Optional[Dict[str, str]]:
        """
        Получает подробную информацию о сертификате по имени контейнера

        Args:
            container_name: Имя контейнера

        Returns:
            Dict или None: Информация о сертификате
        """
        certificates = await self.list_certificates()

        for cert_info in certificates.values():
            if cert_info.get('Container', '') == container_name:
                return cert_info

        return None

    @service_method(description="Delete certificate by thumbprint", public=True)
    async def delete_certificate(self, thumbprint: str) -> Dict[str, Any]:
        """
        Удаляет сертификат по отпечатку (thumbprint)

        Args:
            thumbprint: Отпечаток сертификата для удаления

        Returns:
            Dict: Результат операции удаления
        """
        try:
            if not thumbprint or thumbprint.strip() == "":
                self.logger.error("Thumbprint is empty or invalid")
                return {
                    "success": False,
                    "error": "Thumbprint is required"
                }

            self.logger.info(f"Deleting certificate with thumbprint: {thumbprint}")

            delete_cmd = (f'"{self.csp_path / "certmgr.exe"}" -delete '
                          f'-thumbprint "{thumbprint}"')

            self.logger.debug(f"Running command: {delete_cmd}")

            output = await self._run_command_async(delete_cmd)

            self.logger.debug(f"Delete command output: {output}")

            error_code = self._extract_error_code(output)

            success = error_code == '0x00000000'

            if success:
                self.logger.info(f"Certificate deleted successfully: {thumbprint}")
                return {
                    "success": True,
                    "message": "Certificate deleted successfully"
                }
            else:
                self.logger.error(f"Failed to delete certificate: {error_code}, output: {output}")
                return {
                    "success": False,
                    "error": f"Failed to delete certificate: {error_code}",
                    "error_code": error_code,
                    "output": output
                }

        except Exception as e:
            self.logger.error(f"Error deleting certificate: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }

    @service_method(description="Install certificate from base64 PFX data", public=True)
    async def install_pfx_from_base64(self, pfx_base64: str, password: str, filename: str = "cert.pfx") -> Dict[str, Any]:
        """
        Устанавливает сертификат из base64-кодированных PFX данных

        Args:
            pfx_base64: Base64-кодированные данные PFX файла
            password: Пароль для PFX файла
            filename: Имя файла (для логирования)

        Returns:
            Dict: Результат операции установки
        """
        import base64
        import tempfile

        try:
            self.logger.info(f"Installing certificate from base64 data: {filename}")

            # Decode base64 to bytes
            pfx_data = base64.b64decode(pfx_base64)

            # Create temporary file
            with tempfile.NamedTemporaryFile(mode='wb', suffix='.pfx', delete=False) as tmp_file:
                tmp_file.write(pfx_data)
                tmp_pfx_path = tmp_file.name

            try:
                # Install PFX using existing method
                install_cmd = (f'"{self.csp_path / "certmgr.exe"}" -install -store uMy '
                               f'-file "{tmp_pfx_path}" -pfx -silent -keep_exportable -pin {password}')

                output = await self._run_command_async(install_cmd)
                error_code = self._extract_error_code(output)
                container = self._extract_container(output)

                success = error_code == '0x00000000' and container

                if success:
                    self.logger.info(f"Certificate installed successfully: container={container}")
                    return {
                        "success": True,
                        "message": "Certificate installed successfully",
                        "container": container
                    }
                else:
                    self.logger.error(f"Failed to install certificate: {error_code}")
                    return {
                        "success": False,
                        "error": f"Failed to install certificate: {error_code}",
                        "error_code": error_code
                    }

            finally:
                # Clean up temporary file
                try:
                    Path(tmp_pfx_path).unlink()
                except Exception as e:
                    self.logger.warning(f"Failed to delete temporary file {tmp_pfx_path}: {e}")

        except Exception as e:
            self.logger.error(f"Error installing certificate: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    @service_method(description="Get dashboard data for certificates", public=True)
    async def get_dashboard_data(self) -> Dict[str, Any]:
        """
        Возвращает данные о сертификатах для отображения в дашборде

        Returns:
            Dict: Данные о сертификатах в формате для дашборда
        """
        try:
            certificates = await self.list_certificates()

            # Преобразуем данные для дашборда
            certs_list = []
            for cert_id, cert_info in certificates.items():
                # Log all available fields for this certificate
                self.logger.info(f"Certificate {cert_id} available fields: {list(cert_info.keys())}")

                # Try different possible field names for dates (including Russian)
                valid_from = (cert_info.get("ValidFrom") or
                             cert_info.get("Not valid before") or
                             cert_info.get("NotValidBefore") or
                             cert_info.get("Valid from") or
                             cert_info.get("Действителен с") or
                             cert_info.get("Начало действия") or "")

                valid_to = (cert_info.get("ValidTo") or
                           cert_info.get("Not valid after") or
                           cert_info.get("NotValidAfter") or
                           cert_info.get("Valid to") or
                           cert_info.get("Действителен до") or
                           cert_info.get("Конец действия") or "")

                thumbprint = cert_info.get("Thumbprint", "")
                serial = cert_info.get("Serial", "")

                cert_data = {
                    "id": cert_id,
                    "subject": cert_info.get("Subject", "Unknown"),
                    "subject_cn": cert_info.get("Subject_CN", "Unknown"),
                    "issuer": cert_info.get("Issuer", "Unknown"),
                    "issuer_cn": cert_info.get("Issuer_CN", cert_info.get("Issuer", "Unknown")),
                    "thumbprint": thumbprint,
                    "container": cert_info.get("Container", ""),
                    "serial": serial,
                    "valid_from": valid_from,
                    "valid_to": valid_to,
                }

                # Log certificate info for debugging
                self.logger.info(f"Certificate {cert_id}: thumbprint={thumbprint}, serial={serial}, valid_from={valid_from}, valid_to={valid_to}")

                certs_list.append(cert_data)

            return {
                "service_name": "Управление сертификатами",
                "service_type": "certificates",
                "total_certificates": len(certs_list),
                "certificates": certs_list
            }

        except Exception as e:
            self.logger.error(f"Failed to get dashboard data: {e}")
            return {
                "service_name": "Управление сертификатами",
                "service_type": "certificates",
                "total_certificates": 0,
                "certificates": [],
                "error": str(e)
            }


# Точка входа для загрузки сервиса
class Run(LegacyCertsService):
    """Класс для загрузки сервиса управления legacy сертификатами"""
    pass
