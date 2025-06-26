from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Dict, Any, List
import json
import asyncio
import httpx

app = FastAPI()


# Модель для RPC запроса
class RPCRequest(BaseModel):
    service: str
    method: str
    args: List[Any] = []
    kwargs: Dict[str, Any] = {}


class RPCResponse(BaseModel):
    result: Any = None
    error: str = None


# Реестр сервисов - здесь можно подключать реальные сервисы
class ServiceRegistry:
    def __init__(self):
        self.services = {}

    def register_service(self, name: str, service_instance):
        self.services[name] = service_instance

    def get_service(self, name: str):
        return self.services.get(name)


# Глобальный реестр сервисов
registry = ServiceRegistry()


# Пример сервиса
class ExampleService:
    def calculate(self, a: int, b: int, operation: str = "add"):
        if operation == "add":
            return a + b
        elif operation == "multiply":
            return a * b
        else:
            raise ValueError(f"Unknown operation: {operation}")

    def get_info(self):
        return {"service": "example", "version": "1.0"}


# Регистрация примера сервиса
registry.register_service("example", ExampleService())


@app.post("/rpc", response_model=RPCResponse)
async def rpc_endpoint(request: RPCRequest):
    try:
        # Получение сервиса
        service = registry.get_service(request.service)
        if not service:
            raise HTTPException(status_code=404, detail=f"Service '{request.service}' not found")

        # Получение метода
        if not hasattr(service, request.method):
            raise HTTPException(status_code=404,
                                detail=f"Method '{request.method}' not found in service '{request.service}'")

        method = getattr(service, request.method)

        # Вызов метода
        if asyncio.iscoroutinefunction(method):
            result = await method(*request.args, **request.kwargs)
        else:
            result = method(*request.args, **request.kwargs)

        return RPCResponse(result=result)

    except Exception as e:
        return RPCResponse(error=str(e))


# Дополнительный эндпоинт для получения списка сервисов
@app.get("/services")
async def list_services():
    return {"services": list(registry.services.keys())}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
