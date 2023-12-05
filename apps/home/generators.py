import time
import datetime


def generate_workers(nodes):
    html = ''
    for node in nodes:
        status = 'offline'
        status_code = 'secondary">Offline'
        try:
            last_connect = int(nodes[node]['ttl'])
        except KeyError:
            last_connect = -1
        # last_connect = int(time.time() - float(nodes[node]['ts']))
        if 0 <= last_connect < 120:
            status = 'online'
            status_code = 'success">Online'
        html = html + f"""
                    <tr>
                    <td>
                      <div class="d-flex px-2 py-1">
                        <div class="d-flex flex-column justify-content-center">
                          <h6 class="mb-0 text-sm">{nodes[node]['hostname']}</h6>
                          <p class="text-xs text-secondary mb-0">{node}</p>
                        </div>
                      </div>
                    </td>
                    <td>
                      <p class="text-xs font-weight-bold mb-0">Deployer</p>
                      <p class="text-xs text-secondary mb-0">VERSION</p>
                    </td>
                    <td class="align-middle text-center text-sm">
                      <span class="badge badge-sm bg-gradient-{status_code}
                    </td>
                    <td class="align-middle text-center">
                      <span class="text-secondary text-xs font-weight-bold">{last_connect} Sec's</span>
                    </td>
                      <td class="align-middle text-center">
                          <span class="text-secondary text-xs font-weight-bold">{len(nodes[node]['services'])}</span>
                      </td>

                  </tr>
    
    """
    return html


def generate_projects(projects):
    html = ''
    for proj in projects:
        html = html + f"""
        <tr>
            <td>
              <div class="d-flex px-2">

                <div class="my-auto">
                  <h6 class="mb-0 text-sm">{projects[proj]['name']}</h6>
                </div>
              </div>
            </td>
            <td>
              <p class="text-sm font-weight-bold mb-0">{proj}</p>
            </td>
            <td>
              <span class="text-xs font-weight-bold">{', '.join(projects[proj]['files'])}</span>
            </td>
            <td>
              <span class="text-xs font-weight-bold">{projects[proj]['version']}</span>
            </td>
            <td class="align-middle text-center">
                <div class="d-flex align-items-center justify-content-center">
                  <span class="me-2 text-xs font-weight-bold">{projects[proj]['loader']}</span>
                </div>
            </td>            
            <td class="align-middle text-center">
            <div class="d-flex align-items-center justify-content-center">
                  <span class="me-2 text-xs font-weight-bold">{projects[proj]['service']}</span>
            </div>
        </td>
        </tr>

        """
    return html


def gen_ajax_js(block, agent='common', timer=1):
    # todo cache off on error ajax
    html = """<script type="text/javascript">
    function timer_""" + block + """(){
        $.ajax({url: """ + f"'/ajax/cicd/{block}?agent={agent}'" + """, cache: true, success: function(html){ 
        $("#""" + block + """").html(html);}});}
        $(document).ready(function(){timer_""" + block + """();
        setInterval('timer_""" + f"{block}()', {timer * 1000}" + """);});
        </script>
        """

    return html


def gen_workers_ctrl(nodes):
    """
    <div class="col-xl-6">
            <div class="row">


                    {#                    <span class="text-xs">Belong Interactive</span>#}
{#                    <hr class="horizontal dark my-3">#}
{#                    <h5 class="mb-0">+$2000</h5>#}
{#                  </div>#}

{#                  <div class="card-body pt-0 p-3 text-center">#}


              <div class="col-md-5 col-7">


    """
    html = ''
    for node in nodes:
        html += f"""
        <div class="col-md-4 col-4" style="padding-bottom: 2%;
">
                <div class="card">
                  <div class="card-header mx-4 p-3 text-center">

                  </div>

                    <h6 class="text-center mb-0">{nodes[node]['hostname']}</h6>
                    <br>
                    <div style="align-self: center;
                                padding-bottom: 5%;">
                    <div class="icon icon-shape icon-lg bg-gradient-success shadow text-center border-radius-lg">
                      <a href="#" onClick="Go('/project/{node}/deploy', '{nodes[node]['hostname']}')"><i class="material-icons opacity-10">add_circle</i></a>

                    </div>
                    <div class="icon icon-shape icon-lg bg-gradient-info shadow text-center border-radius-lg">
                     <a href="#" onClick="Go('/project/{node}/control', '{nodes[node]['hostname']}')"><i class="material-icons opacity-10">apps</i></a>
                    </div>
                    <div class="icon icon-shape icon-lg bg-gradient-primary shadow text-center border-radius-lg">
                       <a href="#" onClick="Go('/project/{node}/remove', '{nodes[node]['hostname']}')"><i class="material-icons opacity-10">remove</i></a>
                    </div>
                        </div>

        </div>
                </div>
        """
    return html


def gen_certs_ctrl(certs: dict, full):
    """
    <div class="col-xl-6">
        <div class="row">
        <div class="col-md-5 col-7"> """
    html = ''
    for agent in certs:
        html += f"""
        <div class="col-md-4 col-4" style="padding-bottom: 2%;">
            <div class="card">
                <div class="card-header mx-4 p-3 text-center"></div>
                    <h6 class="text-center mb-0">{full[agent]['hostname']}</h6>
                    <br>
                <div>"""
        print(certs[agent])

        for cert_id in certs[agent]:
            cert = certs[agent][cert_id]
            print(certs)
            print(cert_id)
            print(cert)
            html += f"""<a class="dropdown-item border-radius-md" href="javascript:;" onClick="Go('/cert/{agent}/{cert_id}', '{cert['CN']}')">
                <div class=" py-1">
                    <div class=" justify-content-center">
                        <h6 class="text-sm font-weight-normal mb-1">{cert['CN']} </h6>
                            <p class="text-xs text-secondary mb-0">
                            <i class="fa fa-clock me-1"></i>{cert["Not valid after"]}</p>
                        </div>
                    </div>
                </a>

                
        
    """
    html += f"""
    </div>
    </div>
    """
    return html


def gen_cert_details(cert):
    # fr = datetime.date(str(cert["Not valid before"]).split()[0].replace('\\', ','))
    d = str(cert["Not valid after"]).split()[0].split('/')
    to = datetime.date(int(d[2]), int(d[1]), int(d[1]))
    remain = to - datetime.date.today()
    # if int(remain) < 0:
    #     remain = f'EXPIRED on {remain} days'
    try:
        container = str(cert["Container"]).replace('\\\\\\\\', '\\')
    except KeyError:
        container = 'No information presented'
    try:
        G = cert["G"]
    except KeyError:
        G = ''
    try:
        snils = cert["СНИЛС"]
    except KeyError:
        snils = ''
    try:
        inn = cert["ИНН"]
    except KeyError:
        inn = ''
    try:
        t = cert["T"]
    except KeyError:
        t = ''

    html = f"""
        <div class=" py-1">
            <div class=" justify-content-center">
                <h6 class="text-sm font-weight-normal mb-1">{cert['CN']} </h6>
                <p class="text-xs text-secondary mb-0">{G}</p>
            </div>
            <br>
            <div class=" justify-content-center">
                <h6 class="text-sm font-weight-normal mb-1">{t} </h6>
                <p class="text-xs text-secondary mb-0">{cert["O"]}</p>
            </div>
            <br>
            <div class=" justify-content-center">
                <h6 class="text-sm font-weight-normal mb-1">СНИЛС: {snils} </h6>
                <h6 class="text-sm font-weight-normal mb-1">ИНН: {inn} </h6>
            </div>
            <br>
            <div class=" justify-content-center">
                <h6 class="text-sm font-weight-normal mb-1"><i class="fa fa-clock me-2"></i>From  {str(cert["Not valid before"]).split()[0]} </h6>
                <h6 class="text-sm font-weight-normal mb-1"><i class="fa fa-clock me-2"></i>To {str(cert["Not valid after"]).split()[0]} </h6>
                <h8 class="text-sm text-secondary mb-1"><i class="material-icons text-lg position-relative me-2">
                warning</i>Remain {str(remain).split(',')[0]} </h8>
            </div>
            <br>
            <div class=" justify-content-center">
                <h6 class="text-sm font-weight-normal mb-1">{cert["E"]} </h6>
            </div>
            <br>
            <div class=" justify-content-center">
                <h6 class="text-sm font-weight-normal mb-1">Linked: {cert["PrivateKey Link"]} </h6>
                <p class="text-xs text-secondary mb-0">{container}</p>
            </div>
            <br>
        </div>
    """
    return html


def gen_proj_deploy(agent):
    # html = ''
    # for node in nodes:
    html = f"""
        <ul class="list-group">
              <li class="list-group-item border-0 d-flex justify-content-between ps-0 mb-2 border-radius-lg">
                <div class="d-flex flex-column">
                  <h6 class="mb-1 text-dark font-weight-bold text-sm">{agent}</h6>
                  <span class="text-xs">#MS-415646</span>
                </div>
                <div class="d-flex align-items-center text-sm">
                  $180
                  <button class="btn btn-link text-dark text-sm mb-0 px-0 ms-4"><i class="material-icons text-lg position-relative me-1">picture_as_pdf</i> Deploy/start/stop/remove</button>
                </div>
              </li>
            </ul>
        
        """

    return html


def gen_proj_control(agent, id):
    html = ''
    # todo Add a href to action button, material_image to icon
    # todo add overlay to display 'parameters': 'kmv.pfx kmv.cer', 'name': 'KMV cert', 'service': 'False', 'status': 'stopped'
    print(agent)
    for service in agent['services']:
        print(service)
        if service != "hosted_projects":
            button = ''
            status = agent['services'][service]['status']
            if 'stop' in status:
                button += f"""<a href="/cicd/{id}/{service}?action=start">
                            <button class="btn btn-link text-dark text-sm mb-0 px-0 ms-4">
                            <i class="material-icons text-lg position-relative me-1">play_arrow</i>Start</button></a>"""
                # button += f"""<a href="/cicd/{agent}/{service}?action=start">
                #             <button class="btn btn-link text-dark text-sm mb-0 px-0 ms-4">
                #             <i class="material-icons text-lg position-relative me-1">delete</i>!Remove!</button></a>"""
            if 'start' in status:
                button += f"""<a href="/cicd/{id}/{service}?action=stop">
                            <button class="btn btn-link text-dark text-sm mb-0 px-0 ms-4">
                            <i class="material-icons text-lg position-relative me-1">stop</i>Stop</button></a>"""
                button += f"""<a href="/cicd/{id}/{service}?action=restart">
                            <button class="btn btn-link text-dark text-sm mb-0 px-0 ms-4">
                            <i class="material-icons text-lg position-relative me-1">sync</i>Restart</button></a>"""

                pass

            html += f"""
            <ul class="list-group">
                  <li class="list-group-item border-0 d-flex justify-content-between ps-0 mb-2 border-radius-lg">
                    <div class="d-flex flex-column">
                      <h6 class="mb-1 text-dark font-weight-bold text-sm">{agent['services'][service]['name']}</h6>
                      <span class="text-xs">{agent['services'][service]['version']}</span>
                    </div>
                    <div class="d-flex align-items-center text-sm">
                        <div class="d-flex flex-column">
                            <h5 class="mb-1 text-dark font-weight-bold text-sm">{agent['services'][service]['status']}</h5>
                            <span class="text-xs">{agent['services'][service]['loader']}</span>
                        </div>
                      {button}
                    </div>
                  </li>
                </ul>
            
            """
            if html == '':
                html = '<div class="d-flex align-items-center text-sm"> No hosted projects</div>'

    return html


def gen_tasks_prog_bar(cicd, id):
    # todo Do it!
    html = """
            <li class="list-group-item border-0  justify-content-between ps-0 mb-2 border-radius-lg">
                <div class="d-flex flex-column">
                    <h6 class="mb-1 text-dark font-weight-bold text-sm">Stable</h6>
                    <div class="progress">
                        <div class="progress-bar bg-gradient-success" role="progressbar" aria-valuenow="60"
                             aria-valuemin="0" aria-valuemax="100" style="width: 60%;"></div>
                    </div>
                </div>
                <div class="align-items-center justify-content-center"><span
                        class="me-2 text-xs font-weight-bold">60%</span>
                </div>
            </li>
    """
