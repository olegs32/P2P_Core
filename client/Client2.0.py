import httpx
import asyncio


class LongPollClient:
    def __init__(self, client_id: str, server_url: str):
        self.client_id = client_id
        self.server_url = server_url
        self.last_id = 0  # идентификатор последнего полученного сообщения

    async def get_updates(self):
        """Получает обновления от сервера через Long Polling."""
        async with httpx.AsyncClient() as client:
            while True:
                try:
                    response = await client.get(
                        f"{self.server_url}/agent/lp",
                        params={"client_id": self.client_id, "last_id": self.last_id},
                        timeout=None
                    )
                    if response.status_code == 200:
                        data = response.json()
                        messages = data.get("messages", [])
                        if messages:
                            for message in messages:
                                print(f"Received message: {message['msg']} with id: {message['id']}")
                                self.last_id = message['id']  # обновляем last_id
                        else:
                            print("No new messages")
                    await asyncio.sleep(5)  # Ждем перед повторным запросом
                except Exception as e:
                    print(f"Error in get_updates: {e}")
                    await asyncio.sleep(5)  # Ждем перед повторным подключением в случае ошибки


# Пример использования клиента
async def main():
    client = LongPollClient(client_id="client_2", server_url="http://127.0.0.1:8081")
    await client.get_updates()


# Запуск клиента
asyncio.run(main())
