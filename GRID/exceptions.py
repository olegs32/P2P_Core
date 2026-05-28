# class MethodNotFound(Exception):
#     def __init__(self,service: str, method: str = 'self'):
#         super().__init__('Error')
#         self.method = method
#         self.service = service
#
#     def __str__(self):
#         if self.method == 'self':
#             return f"Service {self.service} not found"
#         else:
#             return f"Method {self.method} of service {self.service} not found"

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