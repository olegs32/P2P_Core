import asyncio
import websockets

async def listen():
    uri = "ws://127.0.0.1:8000/ws"  # Адрес WebSocket-сервера
    async with websockets.connect(uri) as websocket:
        print("WebSocket connection opened")

        # Отправляем приветственное сообщение на сервер
        await websocket.send("Hello from Python WebSocket client!")

        try:
            while True:
                # Ожидание сообщений от сервера
                message = await websocket.recv()
                print(f"Message from server: {message}")
        except websockets.ConnectionClosed:
            print("WebSocket connection closed")

# Запуск клиента
if __name__ == "__main__":
    asyncio.run(listen())
