# from apps.home import generators
from src.srv_acts import *
import src.utils as utils
from threading import Thread
from fastapi import Body, FastAPI, Form, HTTPException, Request, File, UploadFile, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.encoders import jsonable_encoder
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, FileResponse
import asyncio
import os
from pydantic import BaseModel
import pathlib
import queue
import src.sqlite_db_wrapper as sqlite_db_wrapper
import time
import uvicorn  # pip install uvicorn fastapi python-multipart yattag pyinstaller
import json

BIND_WEB = '0.0.0.0'
PORT_WEB = 8080
LIBRARY = 'repo'
TEMPLATE_ENGINES = 'repo_templates'
app = FastAPI()
app.mount("/static", StaticFiles(directory="apps/static"), name="static")

templates = Jinja2Templates(directory="apps/templates/")
"""
Put projects into personal folder for each project in projects library
"""
db_name = r'src\distribution_payloads.db'
# try:
#     x = db
# except Exception as ex:
#     print(ex)
db = sqlite_db_wrapper.SqlDb(db_name)
if not db.check4exist('settings', 'key', 'ttl'):
    db.set('settings', ('key', 'value'), ('ttl', 20))
if not db.check4exist('settings', 'key', 'last_id'):
    db.set('settings', ('key', 'value'), ('last_id', 0))
if not db.check4exist('cicd_settings', 'key', 'renew_client_time'):
    db.set('cicd_settings', ('key', 'value'), ('renew_client_time', 30))
if not db.check4exist('cicd_settings', 'key', 'restart_service_on_crash'):
    db.set('cicd_settings', ('key', 'value'), ('restart_service_on_crash', 'True'))

net = net_ctrl(db=db)
q = queue.LifoQueue()
req_queue = []
cicd = cicd(db)
lib = Library(LIBRARY)
ths = {'check_nodes_th': Thread(target=net.node_checker_up)}
ths['check_nodes_th'].start()
ths['jobs_controller_th'] = Thread(target=net.jobs_processor)
ths['jobs_controller_th'].start()
ths['cisd_watchdog_th'] = Thread(target=cicd.watchdog)
ths['cisd_watchdog_th'].start()