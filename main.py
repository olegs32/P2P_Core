import logging
import queue
import threading
import time
import hmac
import hashlib

import requests
import uvicorn  # pip install uvicorn fastapi python-multipart yattag pyinstaller
from fastapi import FastAPI, Request, Header, HTTPException
from fastapi import WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel

from src.managers import *
from src.projects import ProjectManager
from src.servers import *
from src.web import Web

# Constants
CURRENT_VERSION = "1.2.0"
BIND_WEB = '0.0.0.0'  # Bind to all interfaces
PORT_WEB = 8080
DOMAIN = 'direct'
REPO = 'repo'
NODE = f'NODE_{DOMAIN}'
START_TIME = time.time()
MAX_REQUEST_ID = 3000
SECRET_KEY = "your-secret-key"
AUTHORIZED_TOKENS = {"client-token-1", "client-token-2"}
DOWNLOAD_URL = f"http://127.0.0.1:{PORT_WEB}/downloads/client.exe"

# Global variables
REQUEST_TRACKER: Dict[str, Dict[str, str]] = defaultdict(dict)
LOCK = threading.Lock()

# Initialize FastAPI app and services
app = FastAPI()
async_queue = asyncio.Queue()
state_manager = AgentStateManager(async_queue)
connection_manager = ConnectionManager()
project_manager = AgentProjectManager()
project_store = ProjectManager(REPO)
lp = LongPollServer()
web_data = {'start_time': START_TIME}
services = {
    'lp': lp,
    'state_manager': state_manager,
    'project_manager': project_manager,
    'project_store': project_store
}
web = Web(DOMAIN, NODE, web_data, services)
services['web'] = web
# Указание сервисов, доступных извне по протоколу BCP
router = Router(DOMAIN, NODE, services)


def generate_request_id():
    """Generate a unique request ID."""
    with LOCK:
        for i in range(MAX_REQUEST_ID):
            if i not in REQUEST_TRACKER:
                return i
        raise Exception("Request ID limit exceeded")


async def watch_state_changes(queue: asyncio.Queue):
    """Listen for state changes and send updates via WebSocket."""
    while True:
        client_id, operation_id, state = await queue.get()
        message = {'Client': client_id, 'Operation': operation_id, 'state': state}
        await connection_manager.send_message(message)
        queue.task_done()


class ClientData(BaseModel):
    data: str


@app.get("/api/check_version")
def check_version(x_client_token: str = Header(...)):
    """Check the current version of the application."""
    if x_client_token not in AUTHORIZED_TOKENS:
        raise HTTPException(status_code=403, detail="Invalid client token")
    return {"version": CURRENT_VERSION, "download_url": DOWNLOAD_URL, "release_notes": "Исправлены ошибки."}


@app.post("/api/validate_client")
async def validate_client(file_hash: str):
    """Validate the client binary hash."""
    valid_hashes = {"valid-hash-1", "valid-hash-2"}
    if file_hash not in valid_hashes:
        raise HTTPException(status_code=403, detail="Invalid client binary")
    return {"status": "verified"}


@app.get("/downloads/client.exe")
async def download_client():
    """Serve the client executable for download."""
    return FileResponse("dist/client2.exe")


@app.get("/agent/auth")
async def agent_auth(hostname: str, passphrase: str):
    """Authenticate an agent."""
    client_id = f"{DOMAIN}_{hostname}"
    if client_id not in lp.clients:
        secret = hostname + hostname
        if passphrase == secret:
            await lp.add_client(client_id)
            return {"client_id": client_id, 'success': True}
    return {"client_id": client_id, 'success': False}


@app.get("/agent/lp")
async def get_long_poll(client_id: str, last_id: int = 0):
    """Get new messages for a client from the long poll server."""
    if client_id in REQUEST_TRACKER:
        waiting: dict = REQUEST_TRACKER.get(client_id)
        for ack in waiting:
            if last_id >= waiting[ack].get('id'):
                lp.push(client_id, waiting[ack], {'service': 'ack', 'action': ack})
                REQUEST_TRACKER[client_id].pop(ack)
                logging.info(f'Confirm delivery {ack} ')

    messages = await lp.get_message(client_id, last_id)
    return {"client_id": client_id, "messages": messages}


# only for http debug
@app.get("/agent/push")
async def push_long_poll(agent_id: str, action: str, service: str):
    """Push a message to an agent for debugging."""
    return lp.push(src='debug', dst=agent_id, msg={'action': action, 'service': service})


# Agent updates operations
@app.get("/agent/update")
async def get_update_operation(agent_id: str, operation_id: str, state: str):
    """Update the state of an agent operation."""
    await state_manager.update_operation(agent_id, operation_id, state)
    return {"status": 2}


@app.post("/agent/projects/{agent_id}")
async def post_update_operation(agent_id: str, data: Request):
    """Mass update client states."""
    services = await data.json()
    for project_name in services.get('hosted_projects', {}):
        project = services.get('hosted_projects', {}).get(project_name, {})
        project_manager.force_update(agent_id, project_name, project)
    return {"status": 2}


@app.post("/route")
async def route(src: str, dst: str, data: Request):
    """Route a message to a destination."""
    data = await data.json()
    response = router.route(src, dst, data)
    if response.get('success'):
        REQUEST_TRACKER[src][response['id']] = dst
        return {'success': True, "status": "Message delivered"}
    else:
        return {'success': False, "status": "Failed to deliver", "error": response.get("msg")}


@app.get('/store/deploy', status_code=200)
async def lib_project_deploy(project):
    """Deploy a project from the store."""
    project_store.tar_project(project)
    tarball = project_store.get_tar_location(project)
    return FileResponse(tarball)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """Manage WebSocket connections."""
    await connection_manager.connect(websocket)
    try:
        while True:
            data = json.loads(await websocket.receive_text())
            if data.get('cmd', None) == 'get':
                if data.get('param', None) == 'agents':
                    await websocket.send_json(lp.get_clients())
            await websocket.send_text(f"Message from server: {data}")
    except WebSocketDisconnect:
        connection_manager.disconnect(websocket)
        print("WebSocket disconnected")


@app.on_event("startup")
async def startup_event():
    """Start background tasks on application startup."""
    asyncio.create_task(watch_state_changes(async_queue))

#
# @app.lifespan
# async def lifespan(app: FastAPI):
#     """Handle application lifespan events."""
#     # Start background tasks
#     background_task = asyncio.create_task(watch_state_changes(async_queue))
#     yield
#     # Cleanup
#     background_task.cancel()
#     try:
#         await background_task
#     except asyncio.CancelledError:
#         pass


if __name__ == "__main__":
    uvicorn.run("main:app", host=BIND_WEB, port=PORT_WEB)
