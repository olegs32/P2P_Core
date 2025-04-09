import hashlib
import socket
import random
import threading
import time
from collections import defaultdict


# ----------------------------
# Utils
# ----------------------------
def get_local_ip():
    return socket.gethostbyname(socket.gethostname())


def hash_id(value):
    return int(hashlib.sha1(value.encode()).hexdigest(), 16) % (2 ** 16)



import asyncio
import hashlib
import random

class DHTNode:
    def __init__(self, node_id, bootstrap=None):
        self.node_id = node_id
        self.storage = {}
        self.routing_table = {}  # {node_id: domain}
        self.bootstrap = bootstrap

    async def join_network(self):
        if self.bootstrap and self.bootstrap != self.node_id:
            # Просим bootstrap добавить нас
            await set.dht[self.bootstrap].domain.join(self.node_id)

    async def join(self, other_node_id):
        self.routing_table[other_node_id] = other_node_id  # упрощённо
        print(f"{self.node_id} joined with {other_node_id}")

    async def get(self, key):
        if key in self.storage:
            return self.storage[key]
        closest = self._closest_node(hashlib.sha1(key.encode()).hexdigest())
        if closest == self.node_id:
            return None
        return await get.dht[closest].domain[key]()

    async def set(self, key, value):
        key_hash = hashlib.sha1(key.encode()).hexdigest()
        closest = self._closest_node(key_hash)
        if closest == self.node_id:
            self.storage[key] = value
        else:
            await set.dht[closest].domain[key](value)

    async def ping(self):
        return f"Pong from {self.node_id}"

    def _closest_node(self, key_hash):
        all_nodes = list(self.routing_table.keys()) + [self.node_id]
        return min(all_nodes, key=lambda nid: abs(int(nid, 16) - int(key_hash, 16)))


















#
# # ----------------------------
# # Node Class
# # ----------------------------
# class DHTNode:
#     def __init__(self, ip=None, bootstrap_ip=None):
#         self.ip = ip or get_local_ip()
#         self.id = hash_id(self.ip)
#         self.bootstrap_ip = bootstrap_ip
#
#         self.buckets = defaultdict(list)  # bucket[distance] = [nodes]
#         self.dht_storage = {}  # Local key-value storage
#         self.routing_table = {}  # id -> (ip, last_seen)
#
#         if self.bootstrap_ip:
#             self.bootstrap()
#
#         # Start background thread for discovery
#         threading.Thread(target=self.dpinger, daemon=True).start()
#
#     # ----------------------------
#     # Bootstrap & Discovery
#     # ----------------------------
#     def bootstrap(self):
#         self.add_node(self.bootstrap_ip)
#         self.discover_via_neighbors()
#
#     def add_node(self, ip):
#         node_id = hash_id(ip)
#         if node_id == self.id:
#             return
#         distance = self.distance(node_id)
#         if ip not in self.buckets[distance]:
#             self.buckets[distance].append(ip)
#         self.routing_table[node_id] = (ip, time.time())
#
#     def discover_via_mdns(self):
#         # Placeholder: implement mdns or UDP broadcast here
#         pass
#
#     def discover_via_neighbors(self):
#         for distance in self.buckets:
#             for neighbor_ip in self.buckets[distance]:
#                 self.ping_node(neighbor_ip)
#
#     def ping_node(self, ip):
#         # Simulated response
#         self.add_node(ip)
#
#     def dpinger(self):
#         while True:
#             time.sleep(10)
#             for node_id, (ip, _) in list(self.routing_table.items()):
#                 self.ping_node(ip)
#
#     # ----------------------------
#     # Lookup
#     # ----------------------------
#     def distance(self, other_id):
#         return self.id ^ other_id
#
#     def find_closest_nodes(self, key_id, k=3):
#         sorted_nodes = sorted(self.routing_table.items(), key=lambda item: key_id ^ item[0])
#         return [ip for _, (ip, _) in sorted_nodes[:k]]
#
#     def find_value(self, key):
#         key_id = hash_id(key)
#         if key in self.dht_storage:
#             return self.dht_storage[key]
#
#         closest_nodes = self.find_closest_nodes(key_id)
#         for ip in closest_nodes:
#             # Simulate remote fetch (in real case: network call)
#             node = dht_network.get(ip)  # From simulated global net
#             if node and key in node.dht_storage:
#                 return node.dht_storage[key]
#         return None
#
#     def __getitem__(self, key):
#         return self.find_value(key)
#
#     def __setitem__(self, key, value):
#         self.dht_storage[key] = value
#         # Optionally replicate to k closest nodes
#         key_id = hash_id(key)
#         for ip in self.find_closest_nodes(key_id):
#             node = dht_network.get(ip)
#             if node:
#                 node.dht_storage[key] = value
#
#     # ----------------------------
#     # Domain Call (Stub for RPC)
#     # ----------------------------
#     def domain(self, action, **kwargs):
#         return f"Handling {action} with {kwargs}"
#
#
# # ----------------------------
# # Simulated Global DHT Network
# # ----------------------------
# dht_network = {}
#
# # Example of initializing nodes
# node1 = DHTNode(ip="192.168.0.1")
# node2 = DHTNode(ip="192.168.0.2", bootstrap_ip="192.168.0.1")
# node3 = DHTNode(ip="192.168.0.3", bootstrap_ip="192.168.0.1")
#
# # Register nodes into simulated network
# for node in [node1, node2, node3]:
#     dht_network[node.ip] = node
#
# # Store and fetch a value
# node2["hello"] = "world"
# print(node3["hello"])  # → "world"
