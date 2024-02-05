# from apps.home import generators
from threading import Thread

import sqlalchemy as db
from sqlalchemy.inspection import inspect

import src.utils
from src.calls import generators
from src.services.base_client import *
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from fastapi import Body, FastAPI, Form, HTTPException, Request, File, UploadFile, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.encoders import jsonable_encoder
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, FileResponse
# import jinja2
import asyncio
import os
from pydantic import BaseModel
import pathlib
import queue

import time
import uvicorn  # pip install uvicorn fastapi python-multipart yattag pyinstaller
import json

from src.services.observer import ClientObserver, ProjectsObserver

# import src.sqlite_db_wrapper as sqlite_db_wrapper

# BIND_WEB = '0.0.0.0'
BIND_WEB = '127.0.0.1'
PORT_WEB = 8081
TEMPLATE_ENGINES = 'repo_templates'
REPOS = ['repo', ]
clients = {}
projects = {}
app = FastAPI()
app.mount("/static", StaticFiles(directory="apps/static"), name="static")
engine = db.create_engine("sqlite:///src/db/main.db")
conn = engine.connect()
metadata = db.MetaData()

clients_db = db.Table('clients', metadata,
                      db.Column('client_id', db.Integer, primary_key=True),
                      db.Column('hostname', db.Text)
                      )
metadata.create_all(engine)
# last_id = inspect('clients').primary_key[0]
# print(last_id)
templates = Jinja2Templates(directory="apps/templates/")
"""
Put projects into personal folder for each project in projects library
"""

client_observer = ClientObserver(clients)
project_observer = ProjectsObserver(projects, REPOS)
utils.threader([{'target': client_observer.run}])


# observer_th = Thread(target=client_observer.run, args=())
# observer_th.start()


@app.get('/register', status_code=200)
async def register(ts: float, hostname):
    id = len(clients) + 1
    print(f'register {id} client: {hostname}')
    clients[id] = Client(id, int(ts), hostname, REPOS)
    clients[id].invoke()
    print(clients)

    return JSONResponse({'id': id})


@app.post('/ping', status_code=200)
async def ping(id: int, ts: float, services: Request):
    srvcs = await services.json()
    if id in clients.keys():
        clients[id].update_ts(ts)
        clients[id].services = srvcs
        print(clients[id].services)

        return JSONResponse({'status': 200, 'actions': clients[id].ping_resp})
    else:
        return {'status': 409, 'description': 'Client not registered'}


@app.get('/ajax', status_code=200)
async def ajax(path):
    resp = {}
    if path == '/':
        resp['summary_agents'] = len(clients)
        resp['deploy_projects'] = len(projects)
        resp['summary_agents'] = len(clients)
        resp['uptime'] = utils.get_uptime()
        # return JSONResponse(resp)

    elif path == '/tables.html':
        resp['table_workers'] = generators.gen_block_clients(clients)
        resp['projects'] = generators.gen_block_projects(projects)
        # return JSONResponse(resp)

    elif path == '/deployment.html':
        resp['projects'] = generators.gen_adv_projects_acts(projects)
        # return JSONResponse(resp)

    return JSONResponse(resp)


@app.get('/lib/{project}/deploy.tar', status_code=200)
async def lib_proj_download(codename):
    print(codename)
    locate = projects[codename].path
    return FileResponse(rf"{locate}\{codename}_deploy.tar")


@app.get('/project/{id}/{action}', status_code=200)
async def cli_acts(request: Request, id: str, action: str):
    html = ''
    if action == 'summary':
        print(action)
        html = generators.gen_client_project(clients, projects[id])
    elif action == 'control':
        html = generators.gen_client_control(clients, projects, id)

    # print('custom ajax works')
    return HTMLResponse(html)


@app.get('/', status_code=200)
async def root(request: Request):
    uptime = utils.get_uptime()
    agents = str(len(clients))
    projects_count = len(projects)
    return templates.TemplateResponse('home/index.html', context={'request': request,
                                                                  'uptime': uptime,
                                                                  'deploy_agents': agents,
                                                                  'projects_count': projects_count})


@app.get('/tables.html', status_code=200)  # todo not ready request
async def root(request: Request):
    return templates.TemplateResponse('home/tables.html',
                                      context={'request': request,
                                               'workers': generators.gen_block_clients(clients),
                                               'projects': generators.gen_block_projects(projects), })


@app.get('/deployment.html', status_code=200)
async def root_tables(request: Request):
    return templates.TemplateResponse('home/deployment.html',
                                      context={'request': request,
                                               'projects': generators.gen_adv_projects_acts(projects),
                                               })


if __name__ == "__main__":
    uvicorn.run("main:app", host=BIND_WEB, port=PORT_WEB)
