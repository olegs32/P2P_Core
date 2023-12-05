# from apps.distribution_payloads.srv_acts import *
import apps.home.srv_acts as sa
from threading import Thread
from fastapi import FastAPI, Form, Request, File, UploadFile, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, FileResponse
import os
from pydantic import BaseModel
import queue
import apps.home.sqlite_db_wrapper as sqlite_db_wrapper
import uvicorn  # pip install uvicorn fastapi python-multipart yattag pyinstaller

BIND_WEB = '0.0.0.0'
PORT_WEB = 8080
LIBRARY = 'repo'
TEMPLATE_ENGINES = 'repo_templates'
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

net = sa.net_ctrl(db=db)
q = queue.LifoQueue()
req_queue = []
cicd = sa.cicd(db)
lib = sa.Library(LIBRARY)
ths = {'check_nodes_th': Thread(target=net.node_checker_up)}
ths['check_nodes_th'].start()
ths['jobs_controller_th'] = Thread(target=net.jobs_processor)
ths['jobs_controller_th'].start()
ths['cisd_watchdog_th'] = Thread(target=cicd.watchdog)
ths['cisd_watchdog_th'].start()

if not os.path.exists(LIBRARY):
    os.mkdir(LIBRARY)
if not os.path.exists(TEMPLATE_ENGINES):
    os.mkdir(TEMPLATE_ENGINES)

app = FastAPI()


class JobSchema(BaseModel):
    service: bool = False
    params: str = ' '


@app.get('/jobs/get', status_code=200)
async def jobs_get(id):
    result = net.jobs_poller(id_worker=id)
    return JSONResponse(result)


@app.get('/jobs/finish', status_code=200)
async def jobs_finish(id, id_job, result):
    result = net.jobs_poller(id_worker=id, finish=True, id_job=id_job, result=result)
    return JSONResponse(result)


@app.get('/ping', status_code=200)
async def ping(host, ts, id, status):
    net.aliver(data={'host': host, 'ts': ts, 'id': id, 'status': status})
    return 'updated'


@app.get('/register', status_code=200)
async def register_(host, ):
    id, secret, = sa.register(host, db)
    return {'id': id, 'secret': secret}


@app.post('/jobs/add')
async def jobs_add(host, act, job=Form()):
    print(host, act, job)
    print('Processing new job')
    if act == 'add':
        return net.jobs_add(job)


@app.get('/worker_status', status_code=200)
async def worker_status(id, status, ):
    return JSONResponse(net.worker_status(worker=id, status=status))


@app.get('/', status_code=200)
async def root():
    return HTMLResponse(sa.web_morda(net))


@app.get('/web/{item}', status_code=200)
async def get_item(item):
    with open(f'web/{item}', 'rb') as f:
        return HTMLResponse(f.read())


@app.get('/cicd/auth', status_code=200)
async def cisd_auth(id, hostname):
    result = cicd.auth(id, hostname)
    return result


@app.get('/cicd/agent/{agent}', status_code=200)
async def cisd_agent(agent):
    return HTMLResponse(sa.web_cicd_agent(cicd, agent))


@app.get('/cicd/{agent}/{service}', status_code=200)
async def cisd_agent_ctrl(agent, service, action):
    cicd.service_manager(agent, service, action)
    return RedirectResponse(f'/cicd/agent/{agent}')


@app.post('/lib/register', status_code=302)
async def cisd_lib_new2(files: list[UploadFile], name=Form(), version=Form(), loader=File(),
                        params=Form(), service: JobSchema = Depends(JobSchema), template=Form()):
    sa.register_new(name, version, loader, files, LIBRARY, TEMPLATE_ENGINES, params, template, service)
    # return HTMLResponse(web_cisd(cicd, lib))
    # return RedirectResponse('/cicd')
    return HTMLResponse("""
    <!DOCTYPE html>
        <html>
          <head>
            <meta http-equiv="refresh" content="0; url='/cicd/'" />
          </head>
        </html>
    """)


@app.get('/lib/new', status_code=200)
async def cisd_lib_new():
    # structure = list(os.walk(LIBRARY))
    # print(structure)
    return HTMLResponse(sa.lib_new(TEMPLATE_ENGINES))


@app.get('/lib/{project}', status_code=200)
async def cisd_agent_ctrl(project):
    return HTMLResponse(sa.web_lib_project(lib, project, cicd))


@app.get('/lib/{project}/deploy.tar', status_code=200)
async def cisd_lib_proj_download(project):
    print(project)
    return FileResponse(rf"repo\{project}_deploy.tar")


@app.get('/lib/{codename}/{action}/{agent}', status_code=200)
async def cisd_agent_ctrl(codename, action, agent):
    HTMLResponse(sa.web_lib_engine(codename, action, agent, lib, cicd))
    return RedirectResponse(f'/lib/{codename}')


@app.get('/cicd', status_code=200)
async def cisd():
    return HTMLResponse(sa.web_cisd(cicd, lib))


@app.post('/cicd/ping', status_code=200)
async def cisd_ping(id, ts, services: Request, name):
    jsoon = await services.json()
    # print(jsoon)
    # print(id, ts, jsoon , name)
    return JSONResponse(cicd.ping(id, ts, jsoon, name))


@app.get('/cicd/confirm_change', status_code=200)
async def cisd_ping(id, ts, services, name):
    return JSONResponse(cicd.ping(id, ts, services, name))


@app.get('/cicd/register', status_code=200)
async def cisd_register(id, hostname, passphrase):
    result = cicd.register(id, hostname, passphrase)
    return result


@app.get('/cicd/get_config', status_code=200)
async def cisd_get_config(id, passphrase):
    result = cicd.get_config(id, passphrase)
    return result


@app.get('/cicd/crash', status_code=200)
async def cicd_crash(agent, service):
    cicd.service_crashed(agent, service)
    return 'Report accepted'


@app.get('/cicd/smart', status_code=200)
async def cicd_smart():
    return HTMLResponse(sa.panel(lib, cicd))


@app.get('/ajax/cicd/{mod}', status_code=200)
async def cicd_ajax(mod, agent, ):
    # print('custom ajax works')
    return HTMLResponse(sa.get_ajax(mod, cicd, lib, agent))


# @app.get('/', status_code=200)
# async def jobs_get(id):
#     result =
#     return result
#


if __name__ == "__main__":
    # uvicorn.run("main:app", host=BIND_WEB, port=PORT_WEB)
    uvicorn.run("main:app", host=BIND_WEB, port=PORT_WEB, reload=True)