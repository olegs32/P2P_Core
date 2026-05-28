import asyncio
import websockets
import json
import sys

from GRID.network import MsgPack

# Адрес WebSocket сервера
URI = "ws://localhost:9000/ws/1"  # Замените на ваш адрес


async def send_messages(websocket):
    """
    Корутина для отправки нескольких сообщений.
    В реальном приложении здесь может быть цикл input() или чтение из очереди.
    """
    try:
        # Пример отправки нескольких сообщений подряд
        messages_to_send = [MsgPack(source=f'Node{i}', dst='Node0', service='test', method='echo',
                           data='some data').model_dump_json() for i in range(0, 5) ]

        for msg in messages_to_send:
            # Преобразуем словарь в JSON строку
            # json_message = json.dumps(msg)
            print(f"[SEND] {msg}")
            await websocket.send(msg)

            # Небольшая пауза, чтобы сообщения не улетали мгновенно пачкой
            # (опционально, зависит от логики вашего приложения)
            await asyncio.sleep(1)

        # Если нужно отправлять сообщения бесконечно или по запросу пользователя,
        # можно раскомментировать следующий блок:
        """
        while True:
            message = await asyncio.get_event_loop().run_in_executor(None, input, "Введите сообщение: ")
            if not message:
                break
            await websocket.send(message)
            print(f"[SEND] {message}")
        """

    except Exception as e:
        print(f"Ошибка при отправке: {e}")


async def receive_messages(websocket):
    """
    Корутина для постоянного получения сообщений и вывода их в консоль.
    """
    try:
        async for message in websocket:
            # message - это строка (или bytes, если сервер шлет бинарные данные)
            print(f"[RECEIVE] {message}")
    except websockets.exceptions.ConnectionClosedOK:
        print("Соединение закрыто сервером корректно.")
    except websockets.exceptions.ConnectionClosedError as e:
        print(f"Соединение закрыто с ошибкой: {e}")
    except Exception as e:
        print(f"Ошибка при получении: {e}")


async def main():
    """
    Основная функция, устанавливающая соединение и запускающая две задачи параллельно.
    """
    try:
        # Подключение к серверу
        async with websockets.connect(URI) as websocket:
            print("Соединение установлено.")

            # Создаем две параллельные задачи:
            # 1. Отправка сообщений
            # 2. Получение сообщений
            send_task = asyncio.create_task(send_messages(websocket))
            receive_task = asyncio.create_task(receive_messages(websocket))

            # Ждем завершения обеих задач.
            # receive_task будет работать бесконечно, пока соединение не разорвется.
            # send_task завершится после отправки списка сообщений.

            # Если send_task завершится раньше, мы все равно продолжаем слушать receive_task.
            # Если receive_task упадет (например, соединение разорвано), отмена произойдет автоматически.

            done, pending = await asyncio.wait(
                [send_task, receive_task],
                return_when=asyncio.FIRST_EXCEPTION
            )

            # Отменяем оставшиеся задачи, если одна из них завершилась с ошибкой
            for task in pending:
                task.cancel()

    except ConnectionRefusedError:
        print("Не удалось подключиться: сервер недоступен.")
    except Exception as e:
        print(f"Произошла ошибка: {e}")


if __name__ == "__main__":
    # Запуск асинхронного цикла
    asyncio.run(main())


# import time
#
# import websockets, asyncio
#
# from GRID.network import Router, MsgPack
#
#
# async def forward(message):
#     url = 'ws://localhost:9000/ws/1'
#     async with websockets.connect(url) as websocket:
#         # time.sleep(2)
#         await websocket.send(message)
#         print(await websocket.recv())
#
#
# def xmit_Loop(message):
#     loop = asyncio.new_event_loop()
#     loop.run_until_complete(forward(message))
#     asyncio.set_event_loop(loop)
#

test = MsgPack(source='Node1', dst='Node0', service='test', method='echo', data='some data').model_dump_json()
# test = MsgPack(source='Node1', dst='Node0', service='Test', method=method, data=data)
print(test)

# xmit_Loop(test)