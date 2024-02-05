import time

import src.utils as utils


class Client:
    def __init__(self, id, ts, hostname, repo, ping_timeout=60):
        self.id = id
        self.repo = repo
        self.ttl = 60
        self.ts = ts
        self.services = {}  # update from client
        self.hostname = hostname
        self.control = {'upgrade': [], 'downgrade': [], 'deploy': [], 'remove': [], 'start': [], 'stop': [],
                        'restart': []}
        self.status = 'Registered'
        self.ping_timeout = ping_timeout
        self.queued = {'upgrade': [], 'downgrade': [], 'deploy': [], 'remove': [], 'start': [], 'stop': [],
                       'restart': []}
        self.ping_resp = {'status': 200, 'upgrade': [], 'downgrade': [], 'deploy': [], 'remove': [], 'start': [],
                          'stop': [],
                          'restart': []}
        self.last_update_ts = int
        self.last_connect = -1
        self.report = {}
        self.progress = {}

    def _status(self):
        while True:
            self.status = 'Online' if time.time() - self.ts < self.ping_timeout else 'Offline'
            self.last_connect = int(time.time() - self.ts)
            time.sleep(1)
            # self.online = 'Online' if time.time() - self.ts < self.ping_timeout else False

    def invoke(self):
        utils.threader([{'target': self._status}])

    def update_ts(self, ts):
        self.ts = ts
        self.last_update_ts = time.time()

# class Observer:
#     def __init__(self, clients):
#         self.clients = clients
#
#     def observe(self):
#         while True:
#             for c in self.clients:
#                 c.renew_status()
#             time.sleep(1)
