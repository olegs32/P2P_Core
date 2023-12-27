import datetime
import time
from threading import Thread

START_TIME = time.time()


def thread_controller(threads: list):
    ths = []
    for th in threads:
        args = ''
        if 'args' in th:
            args = th['args']
        ths.append(Thread(target=th['target'], args=args))
        ths[-1].start()
    while True:
        for index, th in enumerate(threads):
            if not ths[index].is_alive:
                args = ''
                if 'args' in th:
                    args = th['args']
                ths.append(Thread(target=th['target'], args=args))
                ths[-1].start()
            time.sleep(1)


def threader(threads: list):
    """

    :param threads: [{'target': target, 'args': list}]
    """
    th = Thread(target=thread_controller, args=(threads,), daemon=True)
    th.start()


def get_uptime():
    secs = mins = hours = 0
    secs = round(time.time() - START_TIME)
    # if secs > 60:
    #     mins = secs // 60
    # if mins > 60:
    #     hours = mins // 60
    # if hours > 24:
    #     days = hours // 24
    # return secs
    sec = datetime.timedelta(seconds=secs)
    d = datetime.datetime(1, 1, 1) + sec

    # print("DAYS:HOURS:MIN:SEC")
    # print("%d:%d:%d:%d" % (d.day - 1, d.hour, d.minute, d.second))
    return "%dD:%dH:%dM:%dS" % (d.day - 1, d.hour, d.minute, d.second)
    # return datetime.datetime.fromtimestamp(int(round(time.time() - START_TIME))).strftime('%Y-%m-%d %H:%M:%S')


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
