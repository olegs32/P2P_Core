import asyncio


class ProxyDomain:
    def __init__(self, domain, node, route):
        self.domain = domain
        self.node = node  # reverse address (src)
        self.route = route

    # def __getattr__(self, item: str):
    #     return RemoteHostProxy(self.domain, [item])

    # def __setattr__(self, service, value):
    #     # call service
    #     pass
    #     # return RemoteHostProxy(self.from_node, [item])

    def __getitem__(self, dst: str):
        # parts = item.split(".")
        dst = ".".join(dst.split('.')[1:])
        return RemoteHostProxy(self.domain, self.node, dst, self.route)


class RemoteHostProxy:
    def __init__(self, domain: str, src: str, dst: str, route):
        self.domain = domain
        self.src = src
        self.dst = dst.split('.')[0]
        self.service = dst.split('.')[1:]
        self.route = route

    def __getattr__(self):
        result = asyncio.run(self.route(self.src, self.dst, {'action': 'getattr', 'service': self.service, 'data': ''}))
        if result['stat']['service']:

            return result['data']
        else:
            raise f"Service Error"

    def __getitem__(self, item):
        return asyncio.run(self.route(self.src, self.dst, {'action': 'getitem', 'service': self.service, 'data': item}))

    async def __call__(self, *args, **kwargs):
        return await self.route(self.src, self.dst,
                                {'action': 'call', 'service': self.service, 'data': {"args": args, "kwargs": kwargs}})

    # async def _call_remote(self, *args, **kwargs):
    #     # to = ".".join(self.service_parts)
    #     return await self.route(self.src, self.dst, {'action': 'call', 'data': {"args": args, "kwargs": kwargs}})

#
# class RemoteServiceProxy:
#     def __init__(self, from_node: str):
#         self.from_node = from_node
#
#     def __getattr__(self, item: str):
#         return RemoteHostProxy(self.from_node, [item])
#
#     def __getitem__(self, item: str):
#         parts = item.split(".")
#         return RemoteHostProxy(self.from_node, parts)
