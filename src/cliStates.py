import asyncio
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from collections import defaultdict
from typing import Dict, List


# Хранилище состояний клиентов
class ClientStateManager:
    def __init__(self, queue: asyncio.Queue):
        # Храним состояние клиентов в виде словаря
        self.client_states: Dict[str, Dict[str, str]] = defaultdict(dict)
        self.queue = queue

    async def update_operation(self, client_id: str, operation_id: str, state: str):
        """Обновляем состояние операции клиента"""
        self.client_states[client_id][operation_id] = state
        print(f"Обновлено состояние операции: {operation_id} для клиента: {client_id} -> {state}")
        await self.queue.put((client_id, operation_id, state))

    def get_operations(self, client_id: str) -> Dict[str, str]:
        """Возвращаем состояния всех операций клиента"""
        return self.client_states.get(client_id, {})


# Управление WebSocket-соединениями
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def send_message(self, message: str):
        """Отправка сообщения всем подключенным клиентам"""
        for connection in self.active_connections:
            await connection.send_text(message)


# Инициализация FastAPI приложения и объектов
app = FastAPI()
queue = asyncio.Queue()
client_manager = ClientStateManager(queue)
connection_manager = ConnectionManager()


# Фоновая задача для обработки изменений состояний
async def watch_state_changes(queue: asyncio.Queue):
    """Слушаем изменения состояний и отправляем обновления через WebSocket"""
    while True:
        client_id, operation_id, state = await queue.get()
        message = f"Client {client_id}: Operation {operation_id} changed to {state}"
        print(message)
        await connection_manager.send_message(message)
        queue.task_done()


@app.get("/update-operation/")
async def update_operation(client_id: str, operation_id: str, state: str):
    """Обновление состояния операции клиента"""
    await client_manager.update_operation(client_id, operation_id, state)
    return {"status": "updated"}


# WebSocket роут для взаимодействия с клиентом
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """Управление подключением WebSocket клиента"""
    await connection_manager.connect(websocket)
    try:
        while True:
            # Ожидание сообщений от клиента (например, для теста)
            data = await websocket.receive_text()
            print(f"Received from WebSocket: {data}")
            await websocket.send_text(f"Message from server: {data}")
    except WebSocketDisconnect:
        connection_manager.disconnect(websocket)
        print("WebSocket disconnected")


# Запуск watchdog при старте приложения
@app.on_event("startup")
async def startup_event():
    print("Запуск фоновой задачи для отслеживания изменений состояний")
    asyncio.create_task(watch_state_changes(queue))


# Запуск сервера через uvicorn
if __name__ == "__main__":
    uvicorn.run("cliStates:app", host="127.0.0.1", port=8000, reload=True)

# Stable !


# import asyncio
# import uvicorn
# from fastapi import FastAPI
# from collections import defaultdict
# from typing import Dict
#
# # Хранилище состояний клиентов
# class ClientStateManager:
#     def __init__(self, queue: asyncio.Queue):
#         # Храним состояние клиентов в виде словаря
#         self.client_states: Dict[str, Dict[str, str]] = defaultdict(dict)
#         self.queue = queue
#
#     async def update_operation(self, client_id: str, operation_id: str, state: str):
#         """Обновляем состояние операции клиента"""
#         self.client_states[client_id][operation_id] = state
#         print(f"Обновлено состояние операции: {operation_id} для клиента: {client_id} -> {state}")
#         await self.queue.put((client_id, operation_id, state))
#
#     def get_operations(self, client_id: str) -> Dict[str, str]:
#         """Возвращаем состояния всех операций клиента"""
#         return self.client_states.get(client_id, {})
#
# # Функция для отслеживания изменений
# async def watch_state_changes(queue: asyncio.Queue):
#     """Слушаем изменения состояний"""
#     while True:
#         client_id, operation_id, state = await queue.get()
#         print(f"Watchdog: обнаружено изменение состояния операции {operation_id} для клиента {client_id}: {state}")
#         queue.task_done()
#
# # FastAPI приложение
# app = FastAPI()
#
# # Очередь для сообщений от ClientStateManager к Watchdog
# queue = asyncio.Queue()
# client_manager = ClientStateManager(queue)
#
# @app.get("/update-operation")
# async def update_operation(client_id: str, operation_id: str, state: str):
#     """Обновление состояния операции клиента"""
#     await client_manager.update_operation(client_id, operation_id, state)
#     return {"status": "updated"}
#
# # Запуск watchdog без потоков, асинхронно
# @app.on_event("startup")
# async def startup_event():
#     print(1)
#     asyncio.create_task(watch_state_changes(queue))
#     print(2)
#
# # Запуск приложения через uvicorn.run()
# if __name__ == "__main__":
#     uvicorn.run("cliStates:app", host="127.0.0.1", port=8000, reload=True)
#







# import asyncio
# from collections import defaultdict
# from typing import Dict
# from fastapi import FastAPI
#
#
# # Хранилище состояний клиентов
# class ClientStateManager:
#     def __init__(self, queue: asyncio.Queue):
#         # Храним состояние клиентов в виде словаря
#         self.client_states: Dict[str, Dict[str, str]] = defaultdict(dict)
#         self.queue = queue
#
#     async def update_operation(self, client_id: str, operation_id: str, state: str):
#         """Обновляем состояние операции клиента"""
#         self.client_states[client_id][operation_id] = state
#         print(f"Обновлено состояние операции: {operation_id} для клиента: {client_id} -> {state}")
#         await self.queue.put((client_id, operation_id, state))
#
#     def get_operations(self, client_id: str) -> Dict[str, str]:
#         """Возвращаем состояния всех операций клиента"""
#         return self.client_states.get(client_id, {})
#
#
# # Функция для отслеживания изменений
# async def watch_state_changes(queue: asyncio.Queue):
#     """Слушаем изменения состояний"""
#     while True:
#         client_id, operation_id, state = await queue.get()
#         print(f"Watchdog: обнаружено изменение состояния операции {operation_id} для клиента {client_id}: {state}")
#         # Здесь можно добавить код для обновления Web UI через Streamlit
#         queue.task_done()
#
#
# # FastAPI приложение для longpoll
# app = FastAPI()
#
# # Очередь для сообщений от ClientStateManager к Watchdog
# queue = asyncio.Queue()
# client_manager = ClientStateManager(queue)
#
#
# @app.post("/update-operation/")
# async def update_operation(client_id: str, operation_id: str, state: str):
#     """Обновление состояния операции клиента"""
#     await client_manager.update_operation(client_id, operation_id, state)
#     return {"status": "updated"}
#
#
# # Запуск watchdog без потоков, асинхронно
# @app.on_event("startup")
# async def startup_event():
#     await asyncio.create_task(watch_state_changes(queue))

# import asyncio
# from collections import defaultdict
# from typing import Dict
# from fastapi import FastAPI
# import threading
#
# # Хранилище состояний клиентов
# class ClientStateManager:
#     def __init__(self, queue: asyncio.Queue):
#         # Храним состояние клиентов в виде словаря
#         self.client_states: Dict[str, Dict[str, str]] = defaultdict(dict)
#         self.queue = queue
#
#     def update_operation(self, client_id: str, operation_id: str, state: str):
#         """Обновляем состояние операции клиента"""
#         self.client_states[client_id][operation_id] = state
#         print(f"Обновлено состояние операции: {operation_id} для клиента: {client_id} -> {state}")
#         # Используем run_coroutine_threadsafe для взаимодействия с асинхронным кодом
#         asyncio.run_coroutine_threadsafe(self.queue.put((client_id, operation_id, state)), asyncio.get_event_loop())
#
#     def get_operations(self, client_id: str) -> Dict[str, str]:
#         """Возвращаем состояния всех операций клиента"""
#         return self.client_states.get(client_id, {})
#
# # Поток для отслеживания изменений
# class Watchdog(threading.Thread):
#     def __init__(self, queue: asyncio.Queue, loop: asyncio.AbstractEventLoop):
#         super().__init__()
#         self.queue = queue
#         self.loop = loop
#
#     def run(self):
#         """Главный цикл отслеживания состояний"""
#         # В этом потоке используем главный loop, переданный в конструктор
#         asyncio.set_event_loop(self.loop)
#         self.loop.run_until_complete(self.watch_state_changes())
#
#     async def watch_state_changes(self):
#         """Слушаем изменения состояний"""
#         while True:
#             client_id, operation_id, state = await self.queue.get()
#             print(f"Watchdog: обнаружено изменение состояния операции {operation_id} для клиента {client_id}: {state}")
#             self.queue.task_done()
#
# # FastAPI приложение для longpoll
# app = FastAPI()
#
# # Очередь для сообщений от ClientStateManager к Watchdog
# queue = asyncio.Queue()  # Без аргумента loop
# client_manager = ClientStateManager(queue)
#
# @app.post("/update-operation/")
# async def update_operation(client_id: str, operation_id: str, state: str):
#     """Обновление состояния операции клиента"""
#     client_manager.update_operation(client_id, operation_id, state)
#     return {"status": "updated"}
#
# # Запуск watchdog в отдельном потоке
# loop = asyncio.get_event_loop()
# watchdog = Watchdog(queue, loop)
# print(1)
# watchdog.start()
# print(2)
#

# import asyncio
# from collections import defaultdict
# from typing import Dict
# from fastapi import FastAPI
# import threading
#
# # Хранилище состояний клиентов
# class ClientStateManager:
#     def __init__(self, queue: asyncio.Queue, loop: asyncio.AbstractEventLoop):
#         # Храним состояние клиентов в виде словаря
#         self.client_states: Dict[str, Dict[str, str]] = defaultdict(dict)
#         self.queue = queue
#         self.loop = loop  # Главный event loop
#
#     def update_operation(self, client_id: str, operation_id: str, state: str):
#         """Обновляем состояние операции клиента"""
#         self.client_states[client_id][operation_id] = state
#         print(f"Обновлено состояние операции: {operation_id} для клиента: {client_id} -> {state}")
#         # Используем run_coroutine_threadsafe для взаимодействия с асинхронным кодом
#         asyncio.run_coroutine_threadsafe(self.queue.put((client_id, operation_id, state)), self.loop)
#
#     def get_operations(self, client_id: str) -> Dict[str, str]:
#         """Возвращаем состояния всех операций клиента"""
#         return self.client_states.get(client_id, {})
#
# # Поток для отслеживания изменений
# class Watchdog(threading.Thread):
#     def __init__(self, queue: asyncio.Queue, loop: asyncio.AbstractEventLoop):
#         super().__init__()
#         self.queue = queue
#         self.loop = loop
#
#     def run(self):
#         """Главный цикл отслеживания состояний"""
#         # В этом потоке используем главный loop, переданный в конструктор
#         asyncio.set_event_loop(self.loop)
#         self.loop.run_until_complete(self.watch_state_changes())
#
#     async def watch_state_changes(self):
#         """Слушаем изменения состояний"""
#         while True:
#             client_id, operation_id, state = await self.queue.get()
#             print(f"Watchdog: обнаружено изменение состояния операции {operation_id} для клиента {client_id}: {state}")
#             # Здесь можно добавить код для обновления Web UI через Streamlit
#             self.queue.task_done()
#
# # FastAPI приложение для longpoll
# app = FastAPI()
#
# # Очередь для сообщений от ClientStateManager к Watchdog
# loop = asyncio.get_event_loop()
# queue = asyncio.Queue(loop=loop)
# client_manager = ClientStateManager(queue, loop)
#
# @app.post("/update-operation/")
# async def update_operation(client_id: str, operation_id: str, state: str):
#     """Обновление состояния операции клиента"""
#     client_manager.update_operation(client_id, operation_id, state)
#     return {"status": "updated"}
#
# # Запуск watchdog в отдельном потоке
# watchdog = Watchdog(queue, loop)
# watchdog.start()
