# Решение проблемы с aioredis в Python 3.12+
import sys
from typing import Optional, Any, Dict
import json
from dataclasses import dataclass
from cachetools import TTLCache
from datetime import datetime, timedelta
import hashlib
import asyncio
import warnings

# Подавление предупреждений об устаревших функциях
warnings.filterwarnings("ignore", category=DeprecationWarning)

# Попытка импорта aioredis с обработкой ошибки
try:
    import aioredis

    REDIS_AVAILABLE = True
except (ImportError, TypeError) as e:
    print(f"Redis недоступен: {e}")
    print("Используется только локальное кеширование")
    REDIS_AVAILABLE = False
    aioredis = None


@dataclass
class CacheConfig:
    """Конфигурация системы кеширования"""
    # Redis конфигурация (если доступен)
    redis_url: str = "redis://localhost:6379"
    redis_enabled: bool = True

    # Локальное кеширование
    l1_cache_size: int = 1000
    l1_cache_ttl: int = 300
    l2_cache_ttl: int = 3600

    # Альтернативное распределенное кеширование через HTTP
    distributed_cache_nodes: list = None
    cluster_mode: bool = False


class InMemoryDistributedCache:
    """Простая реализация распределенного кеша через HTTP без Redis"""

    def __init__(self, node_id: str, nodes: list = None):
        self.node_id = node_id
        self.nodes = nodes or []
        self.local_store: Dict[str, Dict] = {}
        self.invalidation_listeners = []

    async def get(self, key: str) -> Optional[str]:
        """Получение значения из кеша"""
        if key in self.local_store:
            entry = self.local_store[key]
            if datetime.now() < entry['expires']:
                return entry['value']
            else:
                del self.local_store[key]
        return None

    async def setex(self, key: str, ttl: int, value: str):
        """Установка значения с TTL"""
        expires = datetime.now() + timedelta(seconds=ttl)
        self.local_store[key] = {
            'value': value,
            'expires': expires
        }

    async def delete(self, key: str):
        """Удаление ключа"""
        self.local_store.pop(key, None)

    async def publish(self, channel: str, message: str):
        """Публикация сообщения (эмуляция pub/sub)"""
        # Уведомление локальных слушателей
        for listener in self.invalidation_listeners:
            asyncio.create_task(listener(channel, message))

    def add_listener(self, listener):
        """Добавление слушателя событий"""
        self.invalidation_listeners.append(listener)

    async def close(self):
        """Закрытие соединения"""
        self.local_store.clear()


class P2PMultiLevelCache:
    """Многоуровневое кеширование для P2P системы с fallback подходами"""

    def __init__(self, config: CacheConfig, node_id: str):
        self.config = config
        self.node_id = node_id

        # L1 Cache - локальный кеш в памяти
        self.l1_cache = TTLCache(
            maxsize=config.l1_cache_size,
            ttl=config.l1_cache_ttl
        )

        # L2 Cache - распределенный кеш
        self.l2_cache = None
        self.access_frequency = {}
        self.redis_available = REDIS_AVAILABLE and config.redis_enabled

    async def setup_distributed_cache(self):
        """Инициализация распределенного кеша с fallback логикой"""
        if self.redis_available:
            try:
                # Попытка использования Redis
                if hasattr(aioredis, 'from_url'):
                    self.l2_cache = aioredis.from_url(self.config.redis_url)
                else:
                    # Для старых версий aioredis
                    self.l2_cache = await aioredis.create_redis_pool(self.config.redis_url)

                # Тестирование соединения
                await self.l2_cache.ping()
                print(f"Подключение к Redis успешно: {self.config.redis_url}")

            except Exception as e:
                print(f"Ошибка подключения к Redis: {e}")
                print("Переключение на локальный распределенный кеш")
                self.redis_available = False

        if not self.redis_available:
            # Использование простого in-memory кеша как fallback
            self.l2_cache = InMemoryDistributedCache(
                self.node_id,
                self.config.distributed_cache_nodes
            )

    def _get_cache_key(self, key: str, scope: str = "global") -> str:
        """Формирование ключа кеша"""
        return f"p2p:cache:{scope}:{key}"

    async def get(self, key: str, scope: str = "global") -> Optional[Any]:
        """Получение данных из многоуровневого кеша"""
        # L1 Cache проверка
        if key in self.l1_cache:
            self.access_frequency[key] = self.access_frequency.get(key, 0) + 1
            return self.l1_cache[key]

        # L2 Cache проверка
        if self.l2_cache:
            cache_key = self._get_cache_key(key, scope)

            try:
                cached_data = await self.l2_cache.get(cache_key)

                if cached_data:
                    data = json.loads(cached_data)
                    # Кеширование в L1 для быстрого доступа
                    self.l1_cache[key] = data
                    self.access_frequency[key] = self.access_frequency.get(key, 0) + 1
                    return data
            except Exception as e:
                print(f"Ошибка чтения из L2 кеша: {e}")

        return None

    async def set(self, key: str, value: Any, scope: str = "global", ttl: Optional[int] = None):
        """Сохранение данных в многоуровневый кеш"""
        # L1 Cache
        self.l1_cache[key] = value

        # L2 Cache
        if self.l2_cache:
            cache_key = self._get_cache_key(key, scope)
            cache_ttl = ttl or self.config.l2_cache_ttl

            try:
                await self.l2_cache.setex(cache_key, cache_ttl, json.dumps(value, default=str))
            except Exception as e:
                print(f"Ошибка записи в L2 кеш: {e}")

    async def invalidate(self, key: str, scope: str = "global"):
        """Инвалидация кеша"""
        # L1 Cache
        self.l1_cache.pop(key, None)

        # L2 Cache
        if self.l2_cache:
            try:
                cache_key = self._get_cache_key(key, scope)
                await self.l2_cache.delete(cache_key)

                # Уведомление других узлов
                await self._notify_cache_invalidation(key, scope)
            except Exception as e:
                print(f"Ошибка инвалидации L2 кеша: {e}")

    async def _notify_cache_invalidation(self, key: str, scope: str):
        """Уведомление других узлов об инвалидации"""
        if self.l2_cache:
            try:
                await self.l2_cache.publish(
                    'cache_invalidation',
                    json.dumps({
                        'key': key,
                        'scope': scope,
                        'node_id': self.node_id,
                        'timestamp': datetime.now().isoformat()
                    })
                )
            except Exception as e:
                print(f"Ошибка публикации инвалидации: {e}")

    async def setup_invalidation_listener(self):
        """Настройка слушателя инвалидации кеша"""
        if not self.l2_cache:
            return

        try:
            if self.redis_available:
                # Redis pub/sub
                pubsub = self.l2_cache.pubsub()
                await pubsub.subscribe('cache_invalidation')

                async def handle_invalidation():
                    async for message in pubsub.listen():
                        if message['type'] == 'message':
                            try:
                                data = json.loads(message['data'])
                                # Не инвалидируем собственные изменения
                                if data['node_id'] != self.node_id:
                                    self.l1_cache.pop(data['key'], None)
                            except Exception:
                                pass

                # Запуск в фоновой задаче
                asyncio.create_task(handle_invalidation())

            else:
                # Локальный слушатель для in-memory кеша
                async def local_invalidation_handler(channel: str, message: str):
                    if channel == 'cache_invalidation':
                        try:
                            data = json.loads(message)
                            if data['node_id'] != self.node_id:
                                self.l1_cache.pop(data['key'], None)
                        except Exception:
                            pass

                self.l2_cache.add_listener(local_invalidation_handler)

        except Exception as e:
            print(f"Ошибка настройки слушателя инвалидации: {e}")

    async def close(self):
        """Закрытие соединений кеша"""
        if self.l2_cache:
            try:
                await self.l2_cache.close()
            except Exception as e:
                print(f"Ошибка закрытия кеша: {e}")


# Кеширующий декоратор для RPC методов
def cached_rpc(ttl: int = 3600, scope: str = "global"):
    """Декоратор для кеширования RPC методов"""

    def decorator(func):
        async def wrapper(*args, **kwargs):
            # Создание ключа кеша на основе имени функции и параметров
            cache_key = f"{func.__name__}:{hashlib.md5(str(kwargs).encode()).hexdigest()}"

            # Попытка получить из кеша
            if hasattr(wrapper, '_cache'):
                try:
                    cached_result = await wrapper._cache.get(cache_key, scope)
                    if cached_result is not None:
                        return cached_result
                except Exception:
                    pass

            # Выполнение функции
            result = await func(*args, **kwargs)

            # Сохранение в кеш
            if hasattr(wrapper, '_cache'):
                try:
                    await wrapper._cache.set(cache_key, result, scope, ttl)
                except Exception:
                    pass

            return result

        return wrapper

    return decorator


# Альтернативная реализация простого кеша только в памяти (без Redis)
class SimpleMemoryCache:
    """Простой кеш в памяти как альтернатива Redis"""

    def __init__(self, node_id: str, max_size: int = 1000):
        self.node_id = node_id
        self.cache = TTLCache(maxsize=max_size, ttl=3600)

    async def get(self, key: str) -> Optional[str]:
        return self.cache.get(key)

    async def setex(self, key: str, ttl: int, value: str):
        # TTLCache автоматически управляет TTL
        self.cache[key] = value

    async def delete(self, key: str):
        self.cache.pop(key, None)

    async def publish(self, channel: str, message: str):
        # Заглушка для pub/sub
        pass

    async def close(self):
        self.cache.clear()