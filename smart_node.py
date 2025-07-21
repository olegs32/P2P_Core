import time
import asyncio
import httpx
import random
from typing import Dict

NODE_PORT = 9000  # будет переопределено из командной строки
COORDINATOR = "http://localhost:8080"

last_seen: Dict[str, float] = {}
hash_cache = None

async def register():
    # async with httpx.AsyncClient(http2=True, timeout=5) as client:
    async with httpx.AsyncClient(timeout=5) as client:
        await client.post(f"{COORDINATOR}/register", json={"host": "localhost", "port": NODE_PORT})

async def get_peers():
    global hash_cache
    headers = {}
    if hash_cache:
        headers["If-None-Match"] = hash_cache

    async with httpx.AsyncClient(timeout=5) as client:
    # async with httpx.AsyncClient(http2=True, timeout=5) as client:
        resp = await client.get(f"{COORDINATOR}/peers", headers=headers)
        if resp.status_code == 304:
            return  # Не изменилось
        hash_cache = resp.headers.get("etag")
        peers = resp.json().get("peers", [])
        now = time.time()
        for peer in peers:
            key = f"{peer['host']}:{peer['port']}"
            last_seen[key] = now

async def ping_peers():
    now = time.time()
    peers = list(last_seen.items())
    random.shuffle(peers)  # для распределённой нагрузки

    for key, ts in peers:
        delay = now - ts
        # умный пинг — только тех, кто давно не отвечал
        if delay < 30:
            continue
        elif delay < 300 and random.random() < 0.3:
            continue
        elif delay < 900 and random.random() < 0.8:
            continue
        host, port = key.split(":")
        try:
            async with httpx.AsyncClient(timeout=5) as client:
            # async with httpx.AsyncClient(http2=True, timeout=5) as client:
                r = await client.head(f"http://{host}:{port}/ping")
                if r.status_code == 200:
                    last_seen[key] = time.time()
        except Exception:
            pass

async def run_node():
    await register()
    while True:
        await get_peers()
        await ping_peers()
        await asyncio.sleep(10)

if __name__ == "__main__":
    import sys
    NODE_PORT = int(sys.argv[1])
    asyncio.run(run_node())
