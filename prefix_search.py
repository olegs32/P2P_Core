import asyncio
from kademlia.network import Server
from kademlia.protocol import KademliaProtocol
from kademlia.node import Node
from kademlia.routing import RoutingTable

# Extend the KademliaProtocol to add FIND_PREFIX RPC
class PrefixProtocol(KademliaProtocol):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # register new RPC method
        self.rpc_find_prefix = self.handle_find_prefix

    def handle_find_prefix(self, prefix, peer):
        """
        RPC handler: returns local (key, value) pairs whose keys start with prefix,
        plus list of closest nodes for further search.
        """
        # search local storage
        matches = []
        for key in self.storage.iterkeys():
            if key.startswith(prefix):
                matches.append((key, self.storage.get(key)))

        # get k closest contacts to prefix hash
        dummy_node = Node(prefix, None)
        neighbors = self.router.findNeighbors(dummy_node)
        contacts = [contact for contact in neighbors]
        return matches, contacts

    async def find_prefix(self, prefix):
        """
        Client call: searches across network for keys starting with prefix.
        """
        # start from bootstrap contacts
        shortlist = list(self.router.findNeighbors(Node(prefix, None)))
        seen = set()
        results = []

        while shortlist:
            contact = shortlist.pop(0)
            if contact in seen:
                continue
            seen.add(contact)
            try:
                # send RPC
                matches, neighbors = await self.call_find_prefix(contact, prefix)
                results.extend(matches)
                # add new neighbors
                for n in neighbors:
                    if n not in seen:
                        shortlist.append(n)
            except Exception:
                continue

        return results

# Extend Server to use PrefixProtocol
class PrefixServer(Server):
    def __init__(self):
        super().__init__()
        # patch protocol factory
        self.router = None
        self.protocol = None

    async def listen(self, port, interface="0.0.0.0"):
        loop = asyncio.get_event_loop()
        # use our custom protocol
        self.protocol = PrefixProtocol(self.node, self.router, self.storage)
        await loop.create_datagram_endpoint(
            lambda: self.protocol,
            local_addr=(interface, port)
        )
        print(f"Listening on {interface}:{port}")

# Example usage
def run_example():
    async def run():
        # start two nodes for demo
        server1 = PrefixServer()
        await server1.listen(8468)
        server2 = PrefixServer()
        await server2.listen(8469)

        # bootstrap second to first
        await server2.bootstrap([("127.0.0.1", 8468)])

        # store some keys
        await server1.set("apple:1", "Data for apple 1")
        await server1.set("apple:2", "Data for apple 2")
        await server1.set("banana:1", "Data for banana 1")

        # client on server2 searches prefix "apple"
        results = await server2.protocol.find_prefix("apple")
        print("Prefix search results:", results)

        # shutdown
        server1.stop()
        server2.stop()

    asyncio.run(run())

if __name__ == "__main__":
    run_example()
