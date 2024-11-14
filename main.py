# from apps.home import generators
from threading import Thread

import sqlalchemy as db
from sqlalchemy.inspection import inspect

import asyncio
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from collections import defaultdict
from typing import Dict, List
from src.net import *
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from fastapi import Body, FastAPI, Form, HTTPException, Request, File, UploadFile, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.encoders import jsonable_encoder
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, FileResponse
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
# import jinja2
import asyncio
import os
from pydantic import BaseModel
import pathlib
import queue

import time
import uvicorn  # pip install uvicorn fastapi python-multipart yattag pyinstaller
import json

# BIND_WEB = '0.0.0.0'
BIND_WEB = '127.0.0.1'
PORT_WEB = 8081


# lp = LongPoll()


# app.mount("/static", StaticFiles(directory="apps/static"), name="static")

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


class ClientData(BaseModel):
    data: str


class LongPollServer:
    def __init__(self):
        self.clients: Dict[str, Dict[str, int | asyncio.Queue | List[Dict[str, int | str]]]] = {}

    async def add_client(self, client_id: str) -> asyncio.Queue:
        """Инициализирует очередь для нового клиента и список сообщений."""
        queue = asyncio.Queue()
        self.clients[client_id] = {
            "queue": queue,
            "messages": [],
            "last_id": 0  # идентификатор последнего отправленного сообщения
        }
        return queue

    def push(self, to: str, msg: str):
        """Отправляет сообщение с уникальным идентификатором для конкретного клиента."""
        if to in self.clients:
            message_id = self.clients[to]["last_id"] + 1
            self.clients[to]["messages"].append({"id": message_id, "msg": msg})
            self.clients[to]["last_id"] = message_id
            # Отправляем сообщение в очередь, чтобы оно стало доступным для клиента
            self.clients[to]["queue"].put_nowait(msg)
            print(f"Message '{msg}' sent to client {to} with id {message_id}")
            return {'success': True}
        else:
            print(f"Client {to} not found")
            return {'success': False, 'error': f"Client {to} not found"}

    async def get_message(self, client_id: str, last_id: int):
        """Возвращает следующее новое сообщение для клиента, основываясь на его последнем полученном идентификаторе."""
        if client_id not in self.clients:
            await self.add_client(client_id)

        queue = self.clients[client_id]["queue"]
        messages = [m for m in self.clients[client_id]["messages"] if m["id"] > last_id]
        # print(messages)

        if messages:
            print('return', messages)
            return messages  # Если есть сообщения, возвращаем их сразу
        try:
            # Иначе ждем новых сообщений в очереди
            await asyncio.wait_for(queue.get(), timeout=60)
            messages = [m for m in self.clients[client_id]["messages"] if m["id"] > last_id]
            print('alredy awaited and sending', messages)
            return messages
        except asyncio.TimeoutError:
            return []  # Таймаут — возвращаем пустой список


long_poll_server = LongPollServer()


@app.get("/agent/lp")
async def get_long_poll(client_id: str, last_id: int = 0):
    """Получает все новые сообщения для клиента, начиная с идентификатора last_id."""
    messages = await long_poll_server.get_message(client_id, last_id)
    print('processed LP')
    return {"client_id": client_id, "messages": messages}


@app.get("/agent/push")
async def push_long_poll(agent_id: str, msg: str):
    """Получает все новые сообщения для клиента, начиная с идентификатора last_id."""
    return long_poll_server.push(to=agent_id, msg=msg)
    # return {"client_id": agent_id, "messages": msg}


# Agent updates operations
@app.get("/agent/update")
async def update_operation(agent_id: str, operation_id: str, state: str):
    """Update client operation state"""
    await client_manager.update_operation(agent_id, operation_id, state)
    return {"status": 2}


@app.post("/agent/update")
async def update_operation(agent_id: str, data: Request):
    """Mass updates client states"""
    for state in data:
        await client_manager.update_operation(agent_id, data[state]['operation_id'], data[state]['state'])
    return {"status": 2}


# WebSocket роут для взаимодействия с клиентом (frontend streamlit?)
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



if __name__ == "__main__":
    uvicorn.run("main:app", host=BIND_WEB, port=PORT_WEB)
    # uvicorn.run("main:app", host=BIND_WEB, port=PORT_WEB, reload=True)

#
#
# from src.services.observer import ClientObserver, ProjectsObserver
#
# # import src.sqlite_db_wrapper as sqlite_db_wrapper
#
# TEMPLATE_ENGINES = 'repo_templates'
# REPOS = ['repo', ]
# clients = {}
# projects = {}
#
# engine = db.create_engine("sqlite:///src/db/main.db")
# conn = engine.connect()
# metadata = db.MetaData()
# event_store = []
#
# clients_db = db.Table('clients', metadata,
#                       db.Column('client_id', db.Integer, primary_key=True),
#                       db.Column('hostname', db.Text)
#                       )
# metadata.create_all(engine)
# # last_id = inspect('clients').primary_key[0]
# # print(last_id)
# templates = Jinja2Templates(directory="apps/templates/")
# """
# Put projects into personal folder for each project in projects library
# """
#
# client_observer = ClientObserver(clients, projects)
# project_observer = ProjectsObserver(projects, REPOS)
# utils.threader([{'target': client_observer.run}])
#
#
# # for r in REPOS:
# #     if not os.path.exists(f"{r}\\temp_dir"):
# #         os.mkdir(f"{r}\\temp_dir")
#
#
# class Project(BaseModel):
#     rescan: bool = False
#
#
# @app.get("/events")
# async def get_events(last_event_id: int = 0):
#     try:
#         while True:
#             if len(event_store) and event_store[-1]['id'] > last_event_id:
#                 # new_events = [event for event in event_store if event['id'] > last_event_id]
#                 new_events = []
#                 for event in event_store:
#                     if event['id'] > last_event_id:
#                         new_events.append(event)
#                         print(event)
#                 return JSONResponse(content={"events": new_events})
#             await asyncio.sleep(1)
#     except asyncio.CancelledError:
#         raise HTTPException(status_code=408, detail="Request timeout")
#
#
# @app.post("/events")
# async def post_event(event: dict):
#     event['id'] = event_store[-1]['id'] + 1 if event_store else 1
#     event_store.append(event)
#     return JSONResponse(content={"status": "success"})
#
#
# @app.get('/confirm/{id}/{action}/{payload}', status_code=200)
# async def confirm(id: int, action: str, payload: str):
#     cli = clients[id].ping_resp[action]
#     cli.pop(cli.index(payload))
#     # clients[id].progress[] #todo make progress
#
#     return 'ok'
#
#
# @app.get('/register', status_code=200)
# async def register(ts: float, hostname):
#     id = len(clients) + 1
#     print(f'register {id} client: {hostname}')
#     clients[id] = Client(id, int(ts), hostname, REPOS)
#     clients[id].invoke()
#     # print(clients)
#     await post_event({"data": f'Client {id} registered', "role": "assistant"})
#
#     return JSONResponse({'id': id})
#
#
# @app.post('/ping', status_code=200)
# async def ping(id: int, ts: float, services: Request):
#     srvcs = await services.json()
#     if id in clients.keys():
#         hostname = clients[id].hostname
#         clients[id].update_ts(ts)
#         clients[id].services = srvcs
#         for s in srvcs:
#             if s != 'hosted_projects':
#                 if s in projects:
#                     # print(projects[s].hosted)
#                     if hostname not in projects[s].hosted:
#                         projects[s].hosted.append(hostname)
#         await post_event({"data": f'Client {id} still alive at {int(ts)}', "role": "assistant"})
#         return JSONResponse({'status': 200, 'actions': clients[id].ping_resp})
#     else:
#         return {'status': 409, 'description': 'Client not registered'}
#
#
# @app.get('/ajax', status_code=200)
# async def ajax(path):
#     resp = {}
#     if path == '/':
#         resp['summary_agents'] = len(clients)
#         resp['deploy_projects'] = len(projects)
#         resp['summary_agents'] = len(clients)
#         resp['uptime'] = utils.get_uptime()
#
#     elif path == '/tables.html':
#         resp['table_workers'] = generators.gen_block_clients(clients)
#         resp['projects'] = generators.gen_block_projects(projects)
#
#     elif path == '/deployment.html':
#         resp['projects'] = generators.gen_adv_projects_acts(projects)
#     return JSONResponse(resp)
#
#
# @app.get('/lib/{project}/deploy.tar', status_code=200)
# async def lib_proj_download(project):
#     print(project)
#     locate = projects[project].path
#     return FileResponse(rf"{locate}\{project}_deploy.tar")
#
#
# @app.get('/project/{proj}/{action}', status_code=200)
# async def cli_acts(request: Request, proj: str, action: str):
#     html = ''
#     # global hosted_projects # todo just do it!
#     if action == 'summary':
#         # print(action)
#         html = generators.gen_client_project(clients, projects, proj)
#         # hosted_projects = [generators.gen_client_project, (clients, projects, proj)]
#     elif action == 'control':
#         html = generators.gen_client_control(clients, projects, proj)
#         # hosted_projects = [generators.gen_client_control, (clients, projects, proj)]
#     return HTMLResponse(html)
#
#
# @app.get('/client/{id}/{proj}/{action}', status_code=200)
# async def cli_control(request: Request, id: int, proj: str, action: str):
#     # print(clients)
#     # print(clients[id].queued[action])
#     clients[id].queued[action].append(proj)
#     # print(clients[id].queued[action])
#     return 'ok'
#
#
# @app.get('/get/dashboard', status_code=200)
# async def dashboard(request: Request):
#     uptime = utils.get_uptime()
#     online_clients = clients
#     agents = str(len(clients))
#     repos_projects = projects
#     resp = {'uptime': uptime,
#             'agents': agents,
#             'repos_projects': list(projects)
#             }
#     return JSONResponse(resp)
#
#
# @app.get('/get/workers', status_code=200)
# async def dashboard_workers(request: Request):
#     workers = {}
#     for i in clients:
#         print(clients[i])
#         print(type(clients[i].describe()))
#         workers[i] = clients[i].describe()
#     return JSONResponse({'workers': workers})
#
#
# @app.get('/get/projects', status_code=200)
# async def dashboard_workers(request: Request, rescan: bool = False, ):
#     if rescan is True:
#         print(rescan)
#         project_observer.rescan_projects()
#         # exit(32123)
#     projs = {}
#     for i in projects:
#         print(projects[i])
#         print(type(projects[i].describe()))
#         print(projects[i].describe())
#         projs[i] = projects[i].describe()
#
#     return JSONResponse({'projects': projs, 'REPOS': REPOS})
#
#
# @app.get('/', status_code=200)
# async def root(request: Request):
#     uptime = utils.get_uptime()
#     agents = str(len(clients))
#     projects_count = len(projects)
#     return templates.TemplateResponse('home/index.html',
#                                       context={'request': request,
#                                                'uptime': uptime,
#                                                'deploy_agents': agents,
#                                                'projects_count': projects_count})
#
#
# @app.get('/tables.html', status_code=200)  # todo not ready request
# async def root(request: Request):
#     return templates.TemplateResponse('home/tables.html',
#                                       context={'request': request,
#                                                'workers': generators.gen_block_clients(clients),
#                                                'projects': generators.gen_block_projects(projects), })
#
#
# @app.get('/deployment.html', status_code=200)
# async def root_tables(request: Request):
#     return templates.TemplateResponse('home/deployment.html',
#                                       context={'request': request,
#                                                'projects': generators.gen_adv_projects_acts(projects),
#                                                })
#
#
# @app.get("/docs", include_in_schema=False)
# async def custom_swagger_ui_html():
#     return get_swagger_ui_html(
#         openapi_url=app.openapi_url,
#         title="Custom Swagger UI"
#     )
