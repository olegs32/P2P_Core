import asyncio
import json
import os
import time
from typing import List, Dict
import aiohttp
import random
from pathlib import Path
import sys

from aiohttp import web

os.makedirs('cache', exist_ok=True)

DEFAULT_PORT = 9000
COORDINATOR_CACHE_FILE = Path("cache/coordinator_cache.json")

if len(sys.argv) > 1 and sys.argv[1] == "coordinator":
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 8080
else:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT

NODE_ID = port
PEER_CACHE_FILE = Path(f"cache/peer_cache_{NODE_ID}.json")
COORDINATOR_URL = "http://localhost:8080"  # Заменить на реальный адрес
LOCAL_HOST = "127.0.0.1"

class Peer:
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port

    def to_dict(self):
        return {"host": self.host, "port": self.port}

    def __repr__(self):
        return f"{self.host}:{self.port}"

class P2PNode:
    def __init__(self, host: str, port: int, id: int):
        self.host = host
        self.port = port
        self.node_id = id
        self.peers: List[Peer] = []

    async def register_with_coordinator(self):
        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(f"{COORDINATOR_URL}/register", json={"host": self.host, "port": self.port}) as resp:
                    print(f"[REG] Coordinator response: {resp.status}")
            except Exception as e:
                print(f"[REG] Coordinator unreachable: {e}")

    async def get_peers_from_coordinator(self):
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(f"{COORDINATOR_URL}/peers") as resp:
                    data = await resp.json()
                    self.peers = [Peer(p['host'], p['port']) for p in data.get("peers", []) if p['host'] != self.host or p['port'] != self.port]
                    print(f"[SYNC] Peers from coordinator: {self.peers}")
            except Exception as e:
                print(f"[SYNC] Failed to fetch peers: {e}")

    def load_peers_from_cache(self):
        if PEER_CACHE_FILE.exists():
            try:
                with open(PEER_CACHE_FILE, "r") as f:
                    data = json.load(f)
                    self.peers = [Peer(p['host'], p['port']) for p in data]
                    print(f"[CACHE] Loaded peers from cache: {self.peers}")
            except Exception as e:
                print(f"[CACHE] Error reading peer cache: {e}")

    def save_peers_to_cache(self):
        try:
            with open(PEER_CACHE_FILE, "w") as f:
                json.dump([p.to_dict() for p in self.peers], f)
            print(f"[CACHE] Saved peers to cache.")
        except Exception as e:
            print(f"[CACHE] Failed to save peer cache: {e}")

    async def peer_exchange(self):
        if not self.peers:
            return

        peer = random.choice(self.peers)
        url = f"http://{peer.host}:{peer.port}/peers"
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        new_peers = [Peer(p['host'], p['port']) for p in data.get("peers", [])]
                        all_peers = {f"{p.host}:{p.port}": p for p in self.peers + new_peers}
                        self.peers = list(all_peers.values())
                        print(f"[GOSSIP]:{self.node_id} Updated peer list: {self.peers}")
            except Exception as e:
                print(f"[GOSSIP] Peer exchange failed: {e}")

    async def serve(self):
        async def get_peers(request):
            return web.json_response({"peers": [p.to_dict() for p in self.peers]})

        app = web.Application()
        app.add_routes([web.get("/peers", get_peers)])

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, self.host, self.port)
        await site.start()
        print(f"[HTTP] Peer server started at {self.host}:{self.port}")

    async def run(self):
        self.load_peers_from_cache()
        await self.register_with_coordinator()
        await self.get_peers_from_coordinator()
        self.save_peers_to_cache()
        asyncio.create_task(self.serve())

        while True:
            await self.peer_exchange()
            self.save_peers_to_cache()
            await asyncio.sleep(10)


async def run_coordinator(port=8080):
    peers: Dict[str, Peer] = {}

    def save_coordinator_cache():
        try:
            with open(COORDINATOR_CACHE_FILE, "w") as f:
                json.dump([p.to_dict() for p in peers.values()], f)
        except Exception as e:
            print(f"[COORD] Cache save error: {e}")

    def load_coordinator_cache():
        if COORDINATOR_CACHE_FILE.exists():
            try:
                with open(COORDINATOR_CACHE_FILE, "r") as f:
                    data = json.load(f)
                    for item in data:
                        key = f"{item['host']}:{item['port']}"
                        peers[key] = Peer(item['host'], item['port'])
            except Exception as e:
                print(f"[COORD] Cache load error: {e}")

    async def cleanup_dead_peers():
        while True:
            dead = []
            for key, peer in list(peers.items()):
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.get(f"http://{peer.host}:{peer.port}/peers", timeout=2) as resp:
                            if resp.status != 200:
                                dead.append(key)
                except:
                    dead.append(key)
            for key in dead:
                print(f"[COORD] Removing dead peer: {key}")
                peers.pop(key, None)
            save_coordinator_cache()
            await asyncio.sleep(15)

    async def register(request):
        data = await request.json()
        host = data.get("host")
        port_ = data.get("port")
        if host and port_:
            key = f"{host}:{port_}"
            peers[key] = Peer(host, port_)
            print(f"[COORD] Registered peer: {key}")
            save_coordinator_cache()
        return web.Response(status=200)

    async def get_peers(request):
        return web.json_response({"peers": [p.to_dict() for p in peers.values()]})

    load_coordinator_cache()

    app = web.Application()
    app.add_routes([
        web.post("/register", register),
        web.get("/peers", get_peers),
    ])
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"[COORD] Coordinator running on 0.0.0.0:{port}")

    asyncio.create_task(cleanup_dead_peers())
    await asyncio.Event().wait()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "coordinator":
        port = int(sys.argv[2]) if len(sys.argv) > 2 else 8080
        asyncio.run(run_coordinator(port))
    else:
        port = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT
        node = P2PNode(LOCAL_HOST, port, NODE_ID)
        asyncio.run(node.run())
