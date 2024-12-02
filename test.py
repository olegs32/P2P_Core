import asyncio
import websockets
import json
import streamlit as st
from collections import defaultdict
from typing import Dict, List


class Data:
    def __init__(self, req, resp):
        self.server = {}
        self.req = req
        self.resp = resp




class WebSocketClient:
    def __init__(self, uri):
        self.uri = uri
        self.connection = None
        self.listen_task = None
        self.message_queue = asyncio.Queue()  # Очередь для полученных сообщений

    async def connect(self):
        """Устанавливает соединение с сервером и запускает фоновую задачу прослушивания."""
        self.connection = await websockets.connect(self.uri)
        # self.listen_task = asyncio.create_task(self._listen())  # Запуск фонового прослушивания
        st.info("WebSocket соединение установлено.")

    async def _listen(self):
        """Фоновая задача для прослушивания сообщений от сервера."""
        try:
            while True:
                message = json.loads(await self.connection.recv())
                await self.message_queue.put(message)  # Сохраняем сообщение в очередь
        except websockets.ConnectionClosed:
            st.warning("WebSocket соединение закрыто.")
        except Exception as e:
            st.error(f"Ошибка в WebSocket: {e}")
        finally:
            self.connection = None

    async def send(self, data):
        """Отправляет данные на сервер."""
        if self.connection:
            await self.connection.send(json.dumps(data))
        else:
            st.warning("Соединение не установлено. Попробуйте снова.")

    async def get_message(self):
        """Получает следующее сообщение из очереди."""
        if not self.message_queue.empty():
            return await self.message_queue.get()
        return None

    async def close(self):
        """Закрывает соединение и останавливает фоновую задачу."""
        if self.listen_task:
            self.listen_task.cancel()
        if self.connection:
            await self.connection.close()
            st.info("WebSocket соединение закрыто.")


# Streamlit-пример с WebSocketClient
async def start_client():
    if "ws_client" not in st.session_state:
        st.session_state.ws_client = WebSocketClient(uri="ws://127.0.0.1:8080/ws")
        await st.session_state.ws_client.connect()
        st.session_state.connected = True
        # try:
        #     asyncio.run(st.session_state.ws_client.connect())
        #     st.session_state.connected = True
        st.success("Соединение установлено.")
        # except Exception as e:
        #     st.error(f"Ошибка подключения: {e}")
    ws_client = st.session_state.ws_client
    st.title("WebSocket Client with Background Connection")

    client = WebSocketClient(uri="ws://127.0.0.1:8080/ws")

    if st.button("Подключиться"):
        await ws_client.connect()

    message = st.text_input("Введите сообщение для отправки")
    if st.button("Отправить сообщение"):
        st.write(st.session_state.ws_client)
        await ws_client.send(message)

    if st.button("Получить сообщение"):
        message = await ws_client.get_message()
        if message:
            st.write("Получено сообщение:", message)
        else:
            st.info("Сообщений пока нет.")

    # if st.button("Отключиться"):
    #     await ws_client.close()


# Запуск Streamlit
if __name__ == "__main__":
    asyncio.run(start_client())
