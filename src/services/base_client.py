from threading import Thread
import time


class Client:
    def __init__(self, id, ttl, ts, hostname, ping_timeout=60):
        self.id = id
        self.ttl = ttl
        self.ts = ts
        self.services = []
        self.hostname = hostname
        self.changing = {}
        self.online = False
        self.ping_timeout = ping_timeout

    def _status(self):
        self.online = True if time.time() - self.ts < 120 else False
        time.sleep(self.ping_timeout)