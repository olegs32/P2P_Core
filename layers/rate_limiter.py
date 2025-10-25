"""
Rate Limiting middleware для P2P системы
Поддерживает разные лимиты для разных эндпоинтов
"""

import time
import logging
from typing import Dict, Optional, Tuple
from collections import defaultdict, deque
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse


class TokenBucket:
    """
    Token Bucket алгоритм для rate limiting
    Поддерживает burst запросы
    """

    def __init__(self, rate: int, burst: int):
        """
        Args:
            rate: количество запросов в минуту
            burst: максимальный размер burst (количество токенов)
        """
        self.rate = rate / 60.0  # конвертируем в запросы в секунду
        self.burst = burst
        self.tokens = float(burst)
        self.last_update = time.time()
        self.logger = logging.getLogger("TokenBucket")

    def consume(self, tokens: int = 1) -> bool:
        """
        Попытка израсходовать токены
        Returns: True если токены доступны, False если превышен лимит
        """
        now = time.time()
        elapsed = now - self.last_update

        # Пополнение токенов
        self.tokens = min(
            self.burst,
            self.tokens + elapsed * self.rate
        )
        self.last_update = now

        # Попытка израсходовать токены
        if self.tokens >= tokens:
            self.tokens -= tokens
            return True
        return False

    def get_retry_after(self) -> int:
        """Получить время ожидания до следующей попытки (в секундах)"""
        tokens_needed = 1 - self.tokens
        if tokens_needed <= 0:
            return 0
        return int(tokens_needed / self.rate) + 1


class RateLimiter:
    """
    Глобальный Rate Limiter с поддержкой разных лимитов для разных эндпоинтов
    """

    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self.buckets: Dict[str, TokenBucket] = defaultdict(lambda: None)
        self.endpoint_configs: Dict[str, Tuple[int, int]] = {}
        self.default_rate = 200
        self.default_burst = 30
        self.logger = logging.getLogger("RateLimiter")

    def configure_endpoint(self, path: str, rate: int, burst: int):
        """
        Настроить лимиты для конкретного эндпоинта

        Args:
            path: путь эндпоинта (например, "/rpc")
            rate: количество запросов в минуту
            burst: максимальный размер burst
        """
        self.endpoint_configs[path] = (rate, burst)
        self.logger.info(f"Configured rate limit for {path}: {rate} req/min, burst={burst}")

    def set_default_limits(self, rate: int, burst: int):
        """Установить лимиты по умолчанию"""
        self.default_rate = rate
        self.default_burst = burst

    def _get_bucket(self, client_id: str, endpoint: str) -> TokenBucket:
        """Получить или создать bucket для клиента и эндпоинта"""
        key = f"{client_id}:{endpoint}"

        if key not in self.buckets or self.buckets[key] is None:
            # Получаем конфигурацию для эндпоинта
            rate, burst = self.endpoint_configs.get(
                endpoint,
                (self.default_rate, self.default_burst)
            )
            self.buckets[key] = TokenBucket(rate, burst)

        return self.buckets[key]

    def check_rate_limit(
        self,
        client_id: str,
        endpoint: str
    ) -> Tuple[bool, Optional[int]]:
        """
        Проверить rate limit для клиента

        Args:
            client_id: идентификатор клиента (IP адрес)
            endpoint: путь эндпоинта

        Returns:
            (allowed, retry_after) - разрешен ли запрос и время ожидания
        """
        if not self.enabled:
            return True, None

        bucket = self._get_bucket(client_id, endpoint)
        allowed = bucket.consume()

        if not allowed:
            retry_after = bucket.get_retry_after()
            self.logger.warning(
                f"Rate limit exceeded for {client_id} on {endpoint}. "
                f"Retry after {retry_after}s"
            )
            return False, retry_after

        return True, None

    def cleanup_old_buckets(self, max_age: int = 3600):
        """
        Очистка старых buckets (не использовавшихся больше max_age секунд)
        """
        now = time.time()
        to_remove = []

        for key, bucket in self.buckets.items():
            if bucket and (now - bucket.last_update) > max_age:
                to_remove.append(key)

        for key in to_remove:
            del self.buckets[key]

        if to_remove:
            self.logger.info(f"Cleaned up {len(to_remove)} old rate limit buckets")


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    FastAPI middleware для rate limiting
    """

    def __init__(self, app, rate_limiter: RateLimiter):
        super().__init__(app)
        self.rate_limiter = rate_limiter
        self.logger = logging.getLogger("RateLimitMiddleware")

    def _get_client_id(self, request: Request) -> str:
        """Получить идентификатор клиента (IP адрес)"""
        # Проверяем заголовки для получения реального IP при использовании прокси
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()

        real_ip = request.headers.get("X-Real-IP")
        if real_ip:
            return real_ip

        # Используем client.host если доступен
        if request.client:
            return request.client.host

        return "unknown"

    async def dispatch(self, request: Request, call_next):
        """Обработка запроса с проверкой rate limit"""

        # Получаем идентификатор клиента и путь
        client_id = self._get_client_id(request)
        endpoint = request.url.path

        # Проверяем rate limit
        allowed, retry_after = self.rate_limiter.check_rate_limit(
            client_id, endpoint
        )

        if not allowed:
            # Возвращаем 429 Too Many Requests
            headers = {}
            if retry_after:
                headers["Retry-After"] = str(retry_after)

            return JSONResponse(
                status_code=429,
                content={
                    "error": "Too many requests",
                    "message": f"Rate limit exceeded. Retry after {retry_after} seconds.",
                    "retry_after": retry_after
                },
                headers=headers
            )

        # Пропускаем запрос дальше
        response = await call_next(request)
        return response


# Глобальный экземпляр rate limiter (создается при инициализации приложения)
global_rate_limiter: Optional[RateLimiter] = None


def get_rate_limiter() -> RateLimiter:
    """Получить глобальный rate limiter"""
    global global_rate_limiter
    if global_rate_limiter is None:
        global_rate_limiter = RateLimiter()
    return global_rate_limiter


def configure_rate_limiter_from_config(config) -> RateLimiter:
    """
    Настроить rate limiter из P2PConfig

    Args:
        config: экземпляр P2PConfig

    Returns:
        настроенный RateLimiter
    """
    rate_limiter = RateLimiter(enabled=config.rate_limit_enabled)

    # Устанавливаем лимиты по умолчанию
    rate_limiter.set_default_limits(
        config.rate_limit_default_requests,
        config.rate_limit_default_burst
    )

    # Настраиваем лимиты для RPC эндпоинта (строже)
    rate_limiter.configure_endpoint(
        "/rpc",
        config.rate_limit_rpc_requests,
        config.rate_limit_rpc_burst
    )

    # Настраиваем лимиты для health эндпоинта (послабее)
    rate_limiter.configure_endpoint(
        "/health",
        config.rate_limit_health_requests,
        config.rate_limit_health_burst
    )

    # Настраиваем лимиты для других health эндпоинтов
    rate_limiter.configure_endpoint(
        "/metrics",
        config.rate_limit_health_requests,
        config.rate_limit_health_burst
    )

    global global_rate_limiter
    global_rate_limiter = rate_limiter

    return rate_limiter
