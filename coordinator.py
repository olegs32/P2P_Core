import time
import hashlib
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Dict, List
import uvicorn


app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# === Хранилище пиров и времени активности ===
peers: Dict[str, float] = {}
peer_data: Dict[str, dict] = {}
peer_list_hash = ""

class PeerInfo(BaseModel):
    host: str
    port: int

def calculate_peer_hash() -> str:
    raw = "".join(sorted(peers.keys()))
    return hashlib.sha256(raw.encode()).hexdigest()

def update_peer_list_hash():
    global peer_list_hash
    peer_list_hash = calculate_peer_hash()

@app.post("/register")
async def register(peer: PeerInfo):
    key = f"{peer.host}:{peer.port}"
    peers[key] = time.time()
    peer_data[key] = peer.dict()
    update_peer_list_hash()
    return {"status": "registered"}

@app.get("/peers")
async def get_peers(request: Request):
    client_hash = request.headers.get("If-None-Match")
    if client_hash == peer_list_hash:
        return Response(status_code=304)  # Not Modified

    now = time.time()
    active_peers = []
    for key, ts in peers.items():
        if now - ts < 60:  # считаем активными
            active_peers.append(peer_data[key])

    headers = {"ETag": peer_list_hash}
    return JSONResponse(content={"peers": active_peers}, headers=headers)

@app.head("/ping")
async def ping():
    return Response(status_code=200)

def remove_stale_peers():
    now = time.time()
    stale = [key for key, ts in peers.items() if now - ts > 900]
    for key in stale:
        peers.pop(key, None)
        peer_data.pop(key, None)
    update_peer_list_hash()

@app.on_event("startup")
async def on_startup():
    import asyncio
    async def cleanup_loop():
        while True:
            remove_stale_peers()
            await asyncio.sleep(60)
    import asyncio
    asyncio.create_task(cleanup_loop())

if __name__ == "__main__":
    uvicorn.run("coordinator:app", host="0.0.0.0", port=8080,)
    # uvicorn.run("server:app", host="0.0.0.0", port=8080, http="h2", loop="uvloop")
