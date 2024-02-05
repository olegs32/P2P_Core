def gen_block_projects(projects):
    html = ''
    for proj in projects:
        project = projects[proj]
        html = html + f"""
            <tr>
                <td>
                  <div class="d-flex px-2">
                    <div class="my-auto">
                      <h6 class="mb-0 text-sm">{project.name}</h6>
                    </div>
                  </div>
                </td>
                <td>
                  <p class="text-sm font-weight-bold mb-0">{project.codename}</p>
                </td>
                <td>
                  <span class="text-xs font-weight-bold">{', '.join(project.files)}</span>
                </td>
                <td>
                  <span class="text-xs font-weight-bold">{project.version}</span>
                </td>
                <td class="align-middle text-center">
                    <div class="d-flex align-items-center justify-content-center">
                      <span class="me-2 text-xs font-weight-bold">{project.loader}</span>
                    </div>
                </td>            
                <td class="align-middle text-center">
                <div class="d-flex align-items-center justify-content-center">
                      <span class="me-2 text-xs font-weight-bold">{project.service}</span>
                </div>
            </td>
            </tr>

            """
    return html


def gen_block_clients(clients):
    html = ''

    for cli in clients:
        client = clients[cli]
        status_code = f'secondary">{client.status}'
        if 0 <= client.last_connect < client.ping_timeout:
            status_code = f'success">' + client.status
        html = html + f"""
                        <tr>
                        <td>
                          <div class="d-flex px-2 py-1">
                            <div class="d-flex flex-column justify-content-center">
                              <h6 class="mb-0 text-sm">{client.hostname}</h6>
                              <p class="text-xs text-secondary mb-0">{client.id}</p>
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
                          <span class="text-secondary text-xs font-weight-bold">{client.last_connect} Sec's</span>
                        </td>
                          <td class="align-middle text-center">
                              <span class="text-secondary text-xs font-weight-bold">{len(client.services)}</span>
                          </td>

                      </tr>
        """
    return html


# def gen_adv_cli_acts(clients):
#     html = ''
#     for cli in clients:
#         client = clients[cli]
#
#         html += f"""
#                 <div class="col-md-4 col-4" style="padding-bottom: 2%;
#         ">
#                         <div class="card">
#                           <div class="card-header mx-4 p-3 text-center">
#
#                           </div>
#
#                             <h6 class="text-center mb-0">{client.hostname}</h6>
#                             <br>
#                             <div style="align-self: center;
#                                         padding-bottom: 5%;">
#                             <div class="icon icon-shape icon-lg bg-gradient-success shadow text-center border-radius-lg">
#                               <a href="#" onClick="Go('/project/{client.id}/summary', '{client.hostname}')">
#                               <i class="material-icons opacity-10">summarize</i></a>
#
#                             </div>
#                             <div class="icon icon-shape icon-lg bg-gradient-info shadow text-center border-radius-lg">
#                              <a href="#" onClick="Go('/project/{client.id}/control', '{client.hostname}')"><i class="material-icons opacity-10">apps</i></a>
#                             </div>
#
#
#                 </div>
#                         </div>
#                 """
#
#     return html


def gen_client_project(clients, project):
    action = ''
    html = ''
    for cli in clients:
        client = clients[cli]
        if client.hostname in project.hosted:
            action = 'delete'
        else:
            action = 'deploy'

        html += f"""
        <ul class="list-group">
              <li class="list-group-item border-0 d-flex justify-content-between ps-0 mb-2 border-radius-lg">
                <div class="d-flex flex-column">
                  <h6 class="mb-1 text-dark font-weight-bold text-sm">{project.name}</h6>
                  <span class="text-xs">{project.id}</span>
                </div>
                <div class="d-flex align-items-center text-sm">
                  {project.codename}
                  <button class="btn btn-link text-dark text-sm mb-0 px-0 ms-4">
                  <i class="material-icons text-lg position-relative me-1">picture_as_pdf</i>{action.capitalize()}</button>
                </div>
              </li>
            </ul>"""

    return html


def gen_client_control(clients, projects, proj):
    html = ''
    # todo Add a href to action button, material_image to icon
    # todo add overlay to display 'parameters': 'kmv.pfx kmv.cer', 'name': 'KMV cert', 'service': 'False', 'status': 'stopped'
    for cli in clients:
        client = clients[cli]
        print(client)
        for ser in client.services:
            service = client.services[ser]
            if ser != "hosted_projects":
                button = ''
                status = service['status']

                if 'stop' in status:
                    button += f"""<a href="#" onClick=httpGet("/client/{cli}/{proj}/start")>
                                   <button class="btn btn-link text-dark text-sm mb-0 px-0 ms-4">
                                   <i class="material-icons text-lg position-relative me-1">play_arrow</i>Start</button></a>"""
                if 'run' in status:
                    button += f"""<a href="#" onClick=httpGet("/client/{cli}/{proj}/stop")>
                                   <button class="btn btn-link text-dark text-sm mb-0 px-0 ms-4">
                                   <i class="material-icons text-lg position-relative me-1">stop</i>Stop</button></a>"""
                    button += f"""<a href="#" onClick=httpGet("/client/{cli}/{proj}/restart")>
                                   <button class="btn btn-link text-dark text-sm mb-0 px-0 ms-4">
                                   <i class="material-icons text-lg position-relative me-1">sync</i>Restart</button></a>"""

                html += f"""
               <ul class="list-group">
                     <li class="list-group-item border-0 d-flex justify-content-between ps-0 mb-2 border-radius-lg">
                       <div class="d-flex flex-column">
                         <h6 class="mb-1 text-dark font-weight-bold text-sm">{service['name']}</h6>
                         <span class="text-xs">{cli}: {client.hostname}</span>
                       </div>
                       <div class="d-flex align-items-center text-sm">
                           <div class="d-flex flex-column">
                               <h5 class="mb-1 text-dark font-weight-bold text-sm">{service['status']}</h5>
                               <span class="text-xs">{service['loader']}</span>
                           </div>
                         {button}
                       </div>
                     </li>
                   </ul>

               """
    if html == '':
        html = '<div class="d-flex align-items-center text-sm"> No hosted projects</div>'

    return html


def gen_adv_projects_acts(projects):
    html = ''
    for proj in projects:
        project = projects[proj]

        html += f"""
                    <div class="col-md-4 col-4" style="padding-bottom: 2%;">
                            <div class="card">
                              <div class="card-header mx-4 p-3 text-center">

                              </div>

                                <h6 class="text-center mb-0">{project.name}</h6>
                                <br>
                                <div style="align-self: center;
                                            padding-bottom: 5%;">
                                <div class="icon icon-shape icon-lg bg-gradient-success shadow text-center border-radius-lg">
                                  <a href="#" onClick="Go('/project/{project.codename}/summary', '{project.name}')">
                                  <i class="material-icons opacity-10">summarize</i></a>

                                </div>
                                <div class="icon icon-shape icon-lg bg-gradient-info shadow text-center border-radius-lg">
                                 <a href="#" onClick="Go('/project/{project.codename}/control', '{project.name}')">
                                 <i class="material-icons opacity-10">apps</i></a>
                                </div>

                                    </div>

                    </div>
                            </div>
                    """

    return html
