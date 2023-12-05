import os
import queue
from threading import Thread

from flask import render_template, request, redirect, Request
from flask_login import login_required
from jinja2 import TemplateNotFound

import apps.home.sqlite_db_wrapper as sqlite_db_wrapper
import apps.home.srv_acts as sa
import apps.home.generators as generators
from apps.home import blueprint, utils

BIND_WEB = '0.0.0.0'
PORT_WEB = 8080
LIBRARY = 'repo'
TEMPLATE_ENGINES = 'repo_templates'
"""
Put projects into personal folder for each project in projects library
"""
db_name = r'apps\home\distribution_payloads.db'
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


@blueprint.route('/index')
@login_required
def index():
    uptime = utils.get_uptime()
    services = cicd.get_srv()
    deploy_agents = str(len(services))
    projects_count = len(lib.get_projects())
    return render_template('home/index.html', segment='index', uptime=uptime,
                           deploy_agents=deploy_agents, projects_count=projects_count)


# class JobSchema(BaseModel):
#     service: bool = False
#     params: str = ' '


# @blueprint.get('/jobs/get')
# def jobs_get(id):
#     result = net.jobs_poller(id_worker=id)
#     return JSONResponse(result)
# 
# 
# @blueprint.get('/jobs/finish')
# def jobs_finish(id, id_job, result):
#     result = net.jobs_poller(id_worker=id, finish=True, id_job=id_job, result=result)
#     return JSONResponse(result)
# 
# 
# @blueprint.get('/ping')
# def ping(host, ts, id, status):
#     net.aliver(data={'host': host, 'ts': ts, 'id': id, 'status': status})
#     return 'updated'
# 
# 
# @blueprint.get('/register')
# def register_(host, ):
#     id, secret, = register(host, db)
#     return {'id': id, 'secret': secret}
# 
# 
# @blueprint.post('/jobs/add')
# def jobs_add(host, act, job=Form()):
#     print(host, act, job)
#     print('Processing new job')
#     if act == 'add':
#         return net.jobs_add(job)
# 
# 
# @blueprint.get('/worker_status')
# def worker_status(id, status, ):
#     return JSONResponse(net.worker_status(worker=id, status=status))
# 
# 
# @blueprint.get('/')
# def root():
#     return HTMLResponse(web_morda(net))


# @blueprint.get('/web/{item}')
# def get_item(item):
#     with open(f'web/{item}', 'rb') as f:
#         return HTMLResponse(f.read())


@blueprint.get('/cicd/auth')
def cisd_auth(id, hostname):
    result = cicd.auth(id, hostname)
    return result
#
#
# @blueprint.get('/cicd/agent/{agent}')
# def cisd_agent(agent):
#     return HTMLResponse(web_cicd_agent(cicd, agent))


# @blueprint.get('/cicd/{agent}/{service}')
# def cisd_agent_ctrl(agent, service, action):
#     cicd.service_manager(agent, service, action)
#     return RedirectResponse(f'/cicd/agent/{agent}')


# @blueprint.post('/lib/register', status_code=302)
# def cisd_lib_new2(files: list[UploadFile], name=Form(), version=Form(), loader=File(),
#                         params=Form(), service: JobSchema = Depends(JobSchema), template=Form()):
#     register_new(name, version, loader, files, LIBRARY, TEMPLATE_ENGINES, params, template, service)
#     # return HTMLResponse(web_cisd(cicd, lib))
#     # return RedirectResponse('/cicd')
#     return HTMLResponse("""
#     <!DOCTYPE html>
#         <html>
#           <head>
#             <meta http-equiv="refresh" content="0; url='/cicd/'" />
#           </head>
#         </html>
#     """)


# @blueprint.get('/lib/new')
# def cisd_lib_new():
#     # structure = list(os.walk(LIBRARY))
#     # print(structure)
#     return HTMLResponse(lib_new(TEMPLATE_ENGINES))


# @blueprint.get('/lib/{project}')
# def cisd_agent_ctrl(project):
#     return HTMLResponse(web_lib_project(lib, project, cicd))


# @blueprint.get('/lib/{project}/deploy.tar')
# def cisd_lib_proj_download(project):
#     print(project)
#     return FileResponse(rf"repo\{project}_deploy.tar")
#
#
# @blueprint.get('/lib/{codename}/{action}/{agent}')
# def cisd_agent_ctrl(codename, action, agent):
#     HTMLResponse(web_lib_engine(codename, action, agent, lib, cicd))
#     return RedirectResponse(f'/lib/{codename}')





@blueprint.post('/cicd/ping')
def cisd_ping(id, ts, services: Request, name):
    jsoon = services.json()
    # print(jsoon)
    # print(id, ts, jsoon , name)
    return cicd.ping(id, ts, jsoon, name)


@blueprint.get('/cicd/confirm_change')
def cisd_change(id, ts, services, name):
    return cicd.ping(id, ts, services, name)


@blueprint.get('/cicd/register')
def cisd_register(id, hostname, passphrase):
    result = cicd.register(id, hostname, passphrase)
    return result


@blueprint.route('/cicd/get_config')
def cisd_get_config():
    id = request.args.get('id')
    passphrase = request.args.get('passphrase')
    result = cicd.get_config(id, passphrase)
    print(result)
    return result


@blueprint.get('/cicd/crash')
def cicd_crash(agent, service):
    cicd.service_crashed(agent, service)
    return 'Report accepted'


# @blueprint.get('/cicd/smart')
# def cicd_smart():
#     return HTMLResponse(panel(lib, cicd))
#
#
# @blueprint.get('/ajax/cicd/{mod}')
# def cicd_ajax(mod, agent, ):
#     # print('custom ajax works')
#     return HTMLResponse(get_ajax(mod, cicd, lib, agent))

@blueprint.route('/<template>')
@login_required
def route_template(template):
    try:
        if not template.endswith('.html'):
            template += '.html'
        # Detect the current page
        segment = get_segment(request)

        if 'table' in template:
            workers = generators.generate_workers(cicd.get_srv())
            projects = generators.generate_projects(lib.get_projects())
            return render_template("home/" + template, segment=segment, workers=workers,
                                   projects=projects)

        # Serve the file (if exists) from app/templates/home/FILE.html
        return render_template("home/" + template, segment=segment)

    except TemplateNotFound:
        return render_template('home/page-404.html'), 404

    except:
        return render_template('home/page-500.html'), 500


# Helper - Extract current page name from request
def get_segment(request):
    try:

        segment = request.path.split('/')[-1]

        if segment == '':
            segment = 'index'

        return segment

    except:
        return None
