from threading import Thread
import time


class Client:
    def __init__(self, id, ts, hostname, repo, ping_timeout=60):
        self.id = id
        self.repo = repo
        self.ttl = 60
        self.ts = ts
        self.services = {}
        self.hostname = hostname
        self.control = {'upgrade': [], 'downgrade': [], 'deploy': [], 'remove': [], 'start': [], 'stop': [], 'restart': []}
        self.status = 'Registered'
        self.ping_timeout = ping_timeout
        self.queued = {'upgrade': [], 'downgrade': [], 'deploy': [], 'remove': [], 'start': [], 'stop': [], 'restart': []}
        self.last_update_ts = int
        self.report = {}

    def _status(self):
        while True:
            self.status = 'Online' if time.time() - self.ts < self.ping_timeout else 'Offline'
            # self.online = 'Online' if time.time() - self.ts < self.ping_timeout else False

    def invoke(self):
        status = Thread(target=self._status, daemon=True)
        status.start()

    def update_ts(self, ts):
        self.ts = ts
        self.last_update_ts = time.time()

class Observer:
    def __init__(self, clients):
        self.clients = clients

    def observe(self):
        while True:
            for c in self.clients:
                c.renew_status()
            time.sleep(1)





