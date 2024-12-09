import queue
import time

import uvicorn  # pip install uvicorn fastapi python-multipart yattag pyinstaller
from fastapi import FastAPI, Request
from fastapi import WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel

from src.managers import *
from src.projects import ProjectManager
from src.servers import *
from src.web import Web

BIND_WEB = '0.0.0.0'
# BIND_WEB = '127.0.0.1'
PORT_WEB = 8080
DOMAIN = 'direct'
REPO = 'repo'
NODE = f'NODE_{DOMAIN}'
START_TIME = time.time()


async def watch_state_changes(queue: asyncio.Queue):
    """Слушаем изменения состояний и отправляем обновления через WebSocket"""
    while True:
        client_id, operation_id, state = await queue.get()
        message = {'Client': client_id, 'Operation': operation_id, 'state': state}
        print(message)
        await connection_manager.send_message(message)
        queue.task_done()


class ClientData(BaseModel):
    data: str


# Инициализация FastAPI приложения и объектов
app = FastAPI()
queue = asyncio.Queue()
state_manager = AgentStateManager(queue)
connection_manager = ConnectionManager()
project_manager = AgentProjectManager()
project_store = ProjectManager(REPO)
lp = LongPollServer()
web_data = {'start_time': START_TIME}
services = {'lp': lp,
            'state_manager': state_manager,
            'project_manager': project_manager,
            'project_store': project_store}
web = Web(DOMAIN, NODE, web_data, services)
services['web'] = web
# Указание сервисов, доступных извне по протоколу BCP
router = Router(DOMAIN, NODE, services)


@app.get("/agent/auth")
async def agent_auth(hostname: str, passphrase: str):
    client_id = f"{DOMAIN}_{hostname}"
    if client_id not in lp.clients:
        secret = hostname + hostname
        if passphrase == secret:

            if client_id not in lp.clients:
                await lp.add_client(client_id)
            return {"client_id": client_id, 'success': True}
            # else:
            #     return {"client_id": client_id, 'success': True}
            #     return {"client_id": 'DUPLICATE', 'success': True}

    else:
        return {"client_id": client_id, 'success': True}  # Debug
        # return {"client_id": None, 'success': False}


@app.get("/agent/lp")
async def get_long_poll(client_id: str, last_id: int = 0):
    """Получает все новые сообщения для клиента, начиная с идентификатора last_id."""
    messages = await lp.get_message(client_id, last_id)
    # print('processed LP')
    return {"client_id": client_id, "messages": messages}


# only for http debug
@app.get("/agent/push")
async def push_long_poll(agent_id: str, action: str, service: str):
    """Координирует запрос к адресу назначения."""
    return lp.push(src='debug', dst=agent_id, msg={'action': action, 'service': service})
    # return {"client_id": agent_id, "messages": msg}


# Agent updates operations
@app.get("/agent/update")
async def get_update_operation(agent_id: str, operation_id: str, state: str):
    """Update client operation state"""
    await state_manager.update_operation(agent_id, operation_id, state)
    return {"status": 2}


@app.post("/agent/projects/{agent_id}")
async def post_update_operation(agent_id: str, data: Request):
    """Mass updates client states"""
    print(await data.json())
    services = await data.json()
    print(services)

    for project_name in services.get('hosted_projects', {}):
        project = services.get('hosted_projects', {}).get(project_name, {})
        project_manager.force_update(agent_id, project_name, project)

    # print(project_name, )

    return {"status": 2}


@app.post("/route")
async def route(src: str, dst: str, service: str, data: Request):
    """Координирует запрос к адресу назначения."""
    data = await data.json()
    return router.route(src, dst, service, data)
    # return {"client_id": agent_id, "messages": msg}


@app.get('/store/deploy', status_code=200)
async def lib_project_deploy(project):
    print(project)
    project_store.tar_project(project)
    tarball = project_store.get_tar_location(project)
    print(tarball)
    return FileResponse(tarball)


# WebSocket роут для взаимодействия с клиентом (frontend streamlit?)
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """Управление подключением WebSocket клиента"""
    await connection_manager.connect(websocket)
    try:
        while True:
            # Ожидание сообщений от клиента (например, для теста)
            data = json.loads(await websocket.receive_text())
            if data.get('cmd', None) == 'get':
                if data.get('param', None) == 'agents':
                    print('send agents data', lp.get_clients())
                    await websocket.send_json(lp.get_clients())
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


#
#
#
# @app.get("/docs", include_in_schema=False)
# async def custom_swagger_ui_html():
#     return get_swagger_ui_html(
#         openapi_url=app.openapi_url,
#         title="Custom Swagger UI"
#     )


if __name__ == "__main__":
    uvicorn.run("main:app", host=BIND_WEB, port=PORT_WEB)
    # uvicorn.run("main:app", host=BIND_WEB, port=PORT_WEB, reload=True)

