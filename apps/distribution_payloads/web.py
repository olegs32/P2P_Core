from random import randint, random
from urllib.parse import unquote
from yattag import Doc
import configparser
import os
import json
import time
from threading import Thread


def web_morda(net):
    doc, tag, text = web_header()
    with tag(
            'a href="jobs?host=test1&act=add&job=eydwYXlsb2FkJzogJ0NtbHRjRzl5ZENCMGFXMWxDbkpsYzNWc2RDQTlJQ2NuQ210bGVTQTlJQ2N6SndwbWIzSWdaQ0JwYmlCa1lYUmhjMlYwT2dvZ0lDQWdhV1lnYTJWNUlEMDlJR1E2Q2lBZ0lDQWdJQ0FnY21WemRXeDBJRDBnWkdGMFlYTmxkQzVwYm1SbGVDaGtLUW89JywgJ2RhdGFzZXQnOiAnV3pFc0lESXNJRE1zSURRc0lEVXNJRFlzSURjc0lEZ3NJRGtzSURBc0lERXdMQ0F5TWl3Z01URXNJRE16TENBME5Dd2dOVFVzSURFc0lESXNJRE1zSURRc0lEVXNJRFlzSURjc0lEZ3NJRGtzSURBc0lERXdMQ0F5TWl3Z01URXNJRE16TENBME5Dd2dOVFZkJ30"'):
        text('Add job')
        text('')
    with tag('br'):
        text()
    text(f'Uptime: {net.get_uptime()}')
    with tag('br'):
        text()
    tmp_worker = 0
    workers_alive = net.get_workers_status()
    for node in workers_alive:
        # print(node)
        if not workers_alive[node]['state'] == 'offline':
            tmp_worker += 1
    # with tag('div align="center"'):
    with tag('table'):
        with tag('tr'):
            with tag('td valign="top"'):
                with tag('div style="display: inline-block;"'):
                    with tag('table border="6"'):
                        with tag('caption'):
                            text(f'Alive workers: {tmp_worker}')
                        with tag('tr'):
                            with tag('th'):
                                text('ID')
                            with tag('th'):
                                text('Label')
                            with tag('th'):
                                text('TTL')
                            with tag('th'):
                                text('State')
                        # print(workers_alive)
                        for node in workers_alive:
                            # print(node)
                            if not workers_alive[node]['state'] == 'offline':
                                with tag('tr'):
                                    with tag('th'):
                                        text(node)
                                    with tag('th'):
                                        text(workers_alive[node]['name'])
                                    with tag('th'):
                                        text(workers_alive[node]['TTL'])
                                    with tag('th'):
                                        text(workers_alive[node]['state'])

                with tag('td valign="top"'):
                    with tag('div style="display: inline-block;"'):
                        with tag('table border="6"'):
                            with tag('caption'):
                                text('Jobs')
                            jobs = net.jobs_list.copy()
                            with tag('tr'):
                                with tag('th'):
                                    text('ID')
                                with tag('th'):
                                    text('workers')
                                with tag('th'):
                                    text(f'jobs, all: {len(jobs)}')
                            for job in jobs:
                                # print(job)
                                with tag('tr'):
                                    with tag('th'):
                                        text(jobs[job]['id'])
                                    with tag('th'):
                                        text(len(jobs[job]['workers']))
                                    with tag('th'):
                                        task = jobs[job]['job']
                                        if len(task) > 10:
                                            task = f'{task[:5]}... len: {len(task)}'
                                        text(task)
                with tag('td valign="top"'):

                    with tag('div style="display: inline-block;"'):
                        with tag('table border="6"'):
                            with tag('caption'):
                                text('Journal')
                            journal = net.finished_dict.copy()
                            with tag('tr'):
                                with tag('th'):
                                    text('Job')
                                with tag('th'):
                                    text('Workers')
                                with tag('th'):
                                    text('Result')
                                with tag('th'):
                                    text(f'Len, all: {len(journal)}')
                                # print(journal)
                            for act in journal:
                                # print(job)
                                with tag('tr'):
                                    with tag('th'):
                                        text(str(act))
                                    with tag('th'):
                                        l = ''
                                        for element in range(0, len(journal[act])):
                                            # for i in journal[act][element]['id']:
                                            #     l = l + '_' + i
                                            try:
                                                text(str(journal[act][element]['id']))
                                            except TypeError:
                                                text(str(journal[act][element]))
                                            with tag('th'):
                                                # txt = ''
                                                # for t in journal[act][element]['result']:
                                                #     txt = txt + t + '\n'
                                                text(str(unquote(journal[act][element]['result'])))
                                    with tag('th'):
                                        text(len(journal[act]))

    return doc.getvalue()


def web_header():
    doc, tag, text = Doc().tagtext()
    with tag('html'):
        with tag('head'):
            # with tag('meta http-equiv="Refresh" content="3"'):
            #     text('')
            with tag('title'):
                text('DNP')
            with tag('style'):
                # text('MAP')
                text('body {background: #fffff0;}')
            with tag('link rel="stylesheet" href="../../../../web/shrdp.css"'):
                text()
        with tag('body'):
            return doc, tag, text


def gen_nodes(nodes, ajax):
    """

    :param doc: inherit from yattag
    :param tag: inherit from yattag
    :param text: inherit from yattag
    :param nodes: {node: {status: online/offline, hostname: str, services: int, last_update: int},}
    :type: nodes: dict
    :return: doc, tag, text
    """
    doc, tag, text = Doc().tagtext()

    def gen_nodes_data(doc, tag, text):
        with tag('div', id="data"):
            with tag('div class="title"'):
                text('Online Agents')
                with tag('div class="grid"'):
                    for node in nodes:
                        with tag('div class="cell"'):
                            with tag('div class="cell_sub"'):
                                with tag('a', href=f'/cicd/agent/{node}', klass='rdp_a', target='_blank'):
                                    text(f"{nodes[node]['hostname']}")
                                with tag('div class="info"'):
                                    with tag('div', klass='header_gray'):
                                        text(nodes[node]['status'])
                                    with tag('div', klass='header_gray'):
                                        text(f"last connect: {nodes[node]['last_update']} sec")
                                    with tag('div', klass='header_gray'):
                                        text(f"Services: {nodes[node]['services']}")
        return doc, tag, text

    if ajax is False:
        with tag('html'):
            with tag('head'):
                # with tag('meta http-equiv="Refresh" content="3"'):
                #     text('')
                with tag('title'):
                    text('General DNP')
                with tag('style'):
                    # text('MAP')
                    text('body {background: #fffff0;}')
                with tag('link rel="stylesheet" href="../../../../web/shrdp.css"'):
                    text()
                with tag('script', type="text/javascript",
                         src="../../../../web/jquery.min.js"):
                    pass
                with tag('script', type="text/javascript", ):
                    text("""function timer(){
                        $.ajax({url: """ + f"'/ajax/cicd/main_page?agent=common'" + """, cache: false, success: function(html){
                        $("#data").html(html);}});}
                        $(document).ready(function(){timer();setInterval('timer()',1000);});""")
            with tag('body'):
                doc, tag, text = gen_nodes_data(doc, tag, text)
    else:
        doc, tag, text = gen_nodes_data(doc, tag, text)

    return doc, tag, text


def get_agent(doc, tag, text, data: dict, agent, ajax=False):
    """

    :param agent: int
    :param doc: inherit from yattag
    :param tag: inherit from yattag
    :param text: inherit from yattag
    :param data: {'ts': float, 'status': {'test_service': {'status': 'running'}}, 'hostname': str, 'ttl': float}
    :type data: dict
    :return: doc, tag, text
    """

    def get_agent_core(doc, tag, text):
        works = data['services']
        with tag('div', id="data"):
            with tag('div class="grid"'):
                with tag('div class="cell"'):
                    with tag('div class="cell_sub"'):
                        text(f"{data['hostname']} ")
                        with tag('div class="info"'):
                            with tag('div', klass='header_gray'):
                                try:
                                    text(f"Last update: {int(data['ttl'])} sec")
                                except KeyError:
                                    text(f"Last update: N/A sec")

                        for work in works:
                            status = works[work]['status']
                            with tag('div class="session_head"'):
                                if work in data['changing']:
                                    if data['changing'][work] == 'start':
                                        work_ = 'initiating'
                                    elif data['changing'][work] == 'stop':
                                        work_ = 'shutting down'
                                    elif data['changing'][work] == 'restart':
                                        work_ = 'restarting'
                                    text(work_)
                                else:
                                    text(work)
                                if work not in data['changing']:
                                    with tag('div class="info"'):
                                        with tag('div', klass='header_gray'):
                                            text(f"{status}")
                                        if 'stop' in status:
                                            with tag('div', klass='header_start'):
                                                with tag('a', href=f"/cicd/{agent}/{work}?action=start",
                                                         klass='rdp_a', ):
                                                    text('start')
                                        if 'run' in status:
                                            with tag('div', klass='header_stop'):
                                                with tag('a', href=f"/cicd/{agent}/{work}?action=stop",
                                                         klass='rdp_a', ):
                                                    text('stop')
                                        with tag('div', klass='header_restart'):
                                            with tag('a', href=f"/cicd/{agent}/{work}?action=restart", klass='rdp_a', ):
                                                text('restart')
        return doc, tag, text

    if ajax is False:
        doc, tag, text = Doc().tagtext()
        with tag('html'):
            with tag('head'):
                # with tag('meta http-equiv="Refresh" content="3"'):
                #     text('')
                with tag('title'):
                    text('Agent info')
                with tag('style'):
                    # text('MAP')
                    text('body {background: #fffff0;}')
                with tag('link rel="stylesheet" href="../../../../web/shrdp.css"'):
                    text()
                doc, tag, text = web_ajax_add(doc, tag, text, 'web_cicd_agent', agent)
            with tag('body'):
                doc, tag, text = get_agent_core(doc, tag, text)
    else:
        doc, tag, text = get_agent_core(doc, tag, text)

    return doc, tag, text


def gen_lib(doc, tag, text, lib):
    projects = lib.get_projects()
    print(projects)
    with tag('div class="title"'):
        text('Global Projects Library       ')
        with tag('a', href='/lib/new'):
            text('Upload new')
        with tag('div class="grid"'):
            for proj in projects:
                print(proj)
                with tag('div class="cell"'):
                    with tag('div class="cell_sub"'):
                        with tag('a', href=f'/lib/{proj}', klass='rdp_a', target='_blank'):
                            print(projects[proj])
                            text(projects[proj]['name'])
                        with tag('div class="info"'):
                            with tag('div', klass='header_gray'):
                                text('Code name: ', proj)
                            with tag('div', klass='header_gray'):
                                text(f"Version: {projects[proj]['version']}")
                            with tag('div', klass='header_gray'):
                                text(f"Loader: {projects[proj]['loader']}")

    return doc, tag, text


def gen_proj_details(doc, tag, text, project, codename, cicd):
    online = cicd.get_srv()
    # {'files': ['project.ini', 'run.py'], 'loader': 'run.py', 'version': '1.0', 'parameters': "''",
    # 'name': 'first libs proj'}
    # print(project)
    # print(online)
    # agent = [f"{x}" for x in online]
    with tag('div class="title"'):
        text(project['name'])
        with tag('div class="grid"'):
            with tag('div class="cell"'):
                with tag('div class="cell_sub"'):
                    text('Details')
                    with tag('div class="info"'):
                        with tag('div', klass='header_gray'):
                            text('Loader: ', project['loader'])
                        with tag('div', klass='header_gray'):
                            text(f"Version: {project['version']}")
                        with tag('div', klass='header_gray'):
                            text(f"Params: {project['parameters']}")
                        with tag('div', klass='title'):
                            text(', '.join(project['files']))

            with tag('div class="deploy_sub"'):
                # with tag('a', href=f'/cicd/lib/{proj}', klass='rdp_a', target='_blank'):
                text('Deploy')
                with tag('div class="deploy_sub_grid"'):
                    with tag('div class="info"'):
                        for agent in online:
                            if codename not in str(online[agent]['services']):
                                # with tag('div', klass='title'):
                                with tag('div class="header_start"'):
                                    if online[agent]['deploy']['progress'] > 0:
                                        doc, tag, text = web_bar(doc, tag, text, online[agent]['deploy']['progress'])
                                    else:
                                        with tag('a', href=f'/lib/{codename}/deploy/{agent}', klass='rdp_a'):
                                            text(str(online[agent]['hostname']))
            with tag('div class="deploy_sub"'):
                text('Update/Remove')
                # with tag('div class="deploy_sub_grid"'):
                for agent in online:
                    with tag('div class="info"'):
                        if codename in str(online[agent]['services']):
                            # with tag('div', klass='title'):
                            with tag('div class="header_gray"'):
                                text(f"{online[agent]['hostname']}")
                            with tag('div class="header_gray"'):
                                # print(online[agent])
                                text(f"Version {online[agent]['services'][codename]['version']}")

                            if project['version'] > online[agent]['services'][codename]['version']:
                                with tag('div class="header_start"'):
                                    if online[agent]['upgrade']['progress'] > 0:
                                        doc, tag, text = web_bar(doc, tag, text, online[agent]['upgrade']['progress'])
                                    else:
                                        with tag('a', href=f'/lib/{codename}/upgrade/{agent}', klass='rdp_a', ):
                                            text('Upgrade')

                            elif project['version'] < online[agent]['services'][codename]['version']:
                                if online[agent]['downgrade']['progress'] > 0:
                                    doc, tag, text = web_bar(doc, tag, text, online[agent]['downgrade']['progress'])
                                else:
                                    with tag('div class="header_start"'):
                                        with tag('a', href=f'/lib/{codename}/downgrade/{agent}', klass='rdp_a', ):
                                            text('Downgrade')

                            with tag('div class="header_stop"'):
                                if online[agent]['remove']['progress'] > 0:
                                    doc, tag, text = web_bar(doc, tag, text, online[agent]['remove']['progress'])
                                else:
                                    with tag('a', href=f'/lib/{codename}/remove/{agent}', klass='rdp_a', ):
                                        text('Stop and remove')
    doc, tag, text = web_bar(doc, tag, text, cicd.test_progress_bar)

    return doc, tag, text


def web_ajax_add(doc, tag, text, page, agent, update_tag='data'):
    with tag('script', type="text/javascript",
             # src="https://ajax.googleapis.com/ajax/libs/jquery/3.1.1/jquery.min.js"):
             src="../../../../web/jquery.min.js"):
        pass
    with tag('script', type="text/javascript", ):
        text("""function timer(){
            $.ajax({url: """ + f"'/ajax/cicd/{page}?agent={agent}'" + """, cache: false, success: function(html){
            $("#""" + update_tag + """ ").html(html);}});}
            $(document).ready(function(){timer();setInterval('timer()',1000);});""")
    return doc, tag, text


def web_bar(doc, tag, text, progress):
    with tag('div', klass="animated-progress progress-purple"):
        with tag('span data-progress=100', style=f"width: {progress}%;"):
            pass
    return doc, tag, text


def web_lib_new(doc, tag, text, repo):
    with tag('form method="post" enctype="multipart/form-data" action="/lib/register"'):
        # with tag('form method="post" action="/lib/register"'):
        with tag('div class="title"'):
            text('New project')
            with tag('div class="grid"'):
                with tag('div class="cell"'):
                    with tag('div class="cell_sub"'):
                        text('Info')

                        # doc.
                        with tag('div class="info"'):
                            with tag('div', klass='header_gray'):
                                text('Name ')
                                with tag('input name="name" type=text'):
                                    pass
                        with tag('div class="info"'):
                            with tag('div', klass='header_gray'):
                                text('Version ')
                                with tag('input name="version" type=text'):
                                    pass
                        with tag('div class="info"'):
                            with tag('div', klass='header_gray'):
                                text('Parameters: ')
                                with tag('input name="params" value=" "'):
                                    pass
                        with tag('div class="info"'):
                            with tag('div', klass='header_gray'):
                                text('Service: ')
                                with tag('input name="service" type=checkbox checked'):
                                    pass
                with tag('div class="cell"'):
                    with tag('div class="cell_sub"'):
                        text('Files')

                        with tag('div class="info"'):
                            with tag('div', klass='header_gray'):
                                text('Loader: ')
                                with tag('input name="loader" type=file'):
                                    pass
                                with tag('input name="template" list=templates'):
                                    with tag('datalist id="templates"'):
                                        structure = list(os.walk(repo))
                                        print(structure)
                                        if len(structure) > 0:
                                            for template in structure[0][2]:
                                                with tag('option'):
                                                    text(template)
                        with tag('div class="info"'):
                            with tag('div', klass='header_gray'):
                                text('Addition files: ')
                                with tag('input name="files" type=file multiple'):
                                    pass
                        with tag('div class="info"'):
                            with tag('div', klass='header_start'):
                                with tag('input type=submit'):
                                    pass
                # with tag('div class="cell"'):
                #     with tag('div class="cell_sub"'):
                #         text('Templates')
                #         with tag('div class="info"'):
                #             with tag('div', klass='header_start'):
                #                 with tag('input list=templates'):
                #                     with tag('datalist id="templates"'):
                #                         with tag('option'):
                #                             text('CertUtil')

        # with tag('div', klass='header_gray'):
        #     text(f"Version: {project['version']}")
        # with tag('div', klass='header_gray'):
        #     text(f"Params: {project['parameters']}")
        # with tag('div', klass='title'):
        #     text(', '.join(project['files']))

    return doc, tag, text


def web_panel(lib, cicd, nodes, ajax=False, ):
    doc, tag, text = Doc().tagtext()

    def web_panel_core(doc, tag, text):
        with tag('div class="title"'):
            text('Online Agents')
            with tag('div class="grid"'):
                for node in nodes:
                    with tag('div class="cell"'):
                        with tag('div class="cell_sub"'):
                            with tag('a', href=f'/cicd/agent/{node}', klass='rdp_a', target='_blank'):
                                text(f"{nodes[node]['hostname']}")
                            with tag('div class="info"'):
                                with tag('div', klass='header_gray'):
                                    text(nodes[node]['status'])
                                with tag('div', klass='header_gray'):
                                    text(f"last connect: {nodes[node]['last_update']} sec")
                                with tag('div', klass='header_gray'):
                                    text(f"Services: {nodes[node]['services']}")
                            # parse services from agent
                            works = cicd.online[node]['services']
                            # print(works)
                            for work in works:
                                status = works[work]['status']
                                # print(work)
                                # print(status)
                                data = cicd.online[node]
                                with tag('div class="session_head"'):
                                    if work in data['changing']:
                                        if data['changing'][work] == 'start':
                                            work_ = 'initiating'
                                        elif data['changing'][work] == 'stop':
                                            work_ = 'shutting down'
                                        elif data['changing'][work] == 'restart':
                                            work_ = 'restarting'
                                        text(work_)
                                    else:
                                        text(work)
                                    if work not in data['changing']:
                                        with tag('div class="info"'):
                                            with tag('div', klass='header_gray'):
                                                text(f"{status}")
                                            if 'stop' in status:
                                                with tag('div', klass='header_start'):
                                                    with tag('a', href=f"/cicd/{node}/{work}?action=start",
                                                             klass='rdp_a', ):
                                                        text('start')
                                            if 'run' in status:
                                                with tag('div', klass='header_stop'):
                                                    with tag('a', href=f"/cicd/{node}/{work}?action=stop",
                                                             klass='rdp_a', ):
                                                        text('stop')
                                            with tag('div', klass='header_restart'):
                                                with tag('a', href=f"/cicd/{node}/{work}?action=restart",
                                                         klass='rdp_a', ):
                                                    text('restart')
        return doc, tag, text

    if ajax is False:
        with tag('html'):
            with tag('head'):
                # with tag('meta http-equiv="Refresh" content="3"'):
                #     text('')
                with tag('title'):
                    text(f'Smart Panel')
                with tag('style'):
                    text('body {background: #fffff0;}')
                with tag('link rel="stylesheet" href="../../../../web/progress_bar.css"'):
                    text()
                with tag('link rel="stylesheet" href="../../../../web/shrdp.css"'):
                    pass
                doc, tag, text = web_ajax_add(doc, tag, text, 'smart_panel', 'smart_panel')

            with tag('body'):
                with tag('div', id='data'):
                    doc, tag, text = web_panel_core(doc, tag, text)

    else:
        doc, tag, text = web_panel_core(doc, tag, text)
    return doc, tag, text
