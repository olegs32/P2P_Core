import time
import src.utils as utils


class ClientObserver:
    def __init__(self, clients):
        self.clients = clients

    def queue2cli(self):
        while True:
            if len(self.clients) > 0:
                for client in self.clients:
                    # print(self.clients[client])
                    for action in self.clients[client].queued:
                        if len(self.clients[client].queued[action]) != 0:
                            for e in self.clients[client].queued[action]:
                                self.clients[client].queued[action].pop(e)  # todo fix error
                                self.clients[client].ping_resp[action].append(e)
            time.sleep(1)

    def run(self):
        utils.threader([{'target': self.queue2cli}])
