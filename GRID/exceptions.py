# Исключения
class MethodNotFound(Exception):
    def __init__(self, service, method):
        super().__init__(f'Method not found: {service}.{method}')

class NodeNotFound(Exception):
    def __init__(self, node):
        super().__init__(f'Node not found: {node}')

class RPCTimeout(Exception):
    def __init__(self, label, timeout):
        super().__init__(f'RPC timeout ({timeout}s): {label}')

