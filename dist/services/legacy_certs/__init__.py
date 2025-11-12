"""
Legacy Certs Service - управление legacy сертификатами CSP

Этот сервис предоставляет функциональность для работы с сертификатами CSP:
- Развертывание сертификатов из PFX/CER файлов
- Экспорт сертификатов в различные форматы
- Поиск и управление установленными сертификатами
"""

from .main import LegacyCertsService, Run

__all__ = ['LegacyCertsService', 'Run']
__version__ = '1.0.0'
