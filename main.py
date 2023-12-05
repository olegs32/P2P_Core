# from apps.home import generators
import sqlalchemy as db
from sqlalchemy.inspection import inspect
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
# import src.sqlite_db_wrapper as sqlite_db_wrapper

BIND_WEB = '0.0.0.0'
PORT_WEB = 8081
TEMPLATE_ENGINES = 'repo_templates'
REPOS = 'repo'
clients = []
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
# observer = Observer(clients)
# observer_th = Thread(target=observer.observe, args='')
# observer_th.start()

@app.get('/register', status_code=200)
async def register(ts, hostname):
    id = len(clients) + 1
    print(f'register {id} client: {hostname}')
    clients.append(Client(id, int(ts), hostname, REPOS).invoke())

    return id


if __name__ == "__main__":
    uvicorn.run("main:app", host=BIND_WEB, port=PORT_WEB)