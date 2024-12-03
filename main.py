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
services = {'lp': lp, 'connection_manager': connection_manager,
            'state_manager': state_manager, 'project_manager': project_manager,
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
