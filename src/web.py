import time
from collections import defaultdict
from typing import Dict


class Web:
    def __init__(self, domain: str, node: str, data: dict, services):
        self.domain = domain
        self.data = data
        self.node = node
        self.services = services

    def create_actions(self):
        pass

    def gen_page(self):
        print('gen page')
        uptime = time.time() - self.data.get('start_time')
        services = self.gen_services_menu()
        example_page = {
            'menu_title': self.node,
            'menu': {
                'Dashboard': {'icon': 'house', 'content': [
                    {'type': 'success', 'label': 'Uptime', 'value': 'XX:XX:XX'},
                    {'type': 'button', 'label': 'Press me!', 'action': 'write', 'cmd': 'constructor work'}
                ]
                              },
                'logs': {'icon': 'log', 'content': services
                         }
            }
        }
        print('page generated')
        # print(example_page)
        return example_page

    def gen_services_menu(self):
        services = self.services_state()
        print(services)
        result = []
        for service in services:
            if len(services[service]) > 0:
                result.append({'type': 'subheader', 'label': service, 'value': ''}),
                result.append({'type': 'table', 'label': services[service], 'value': ''}),
            # elif isinstance(services[service], list):
            #     tmp_dict = {'type': 'table', 'value': ''}
            #     if len(services[service]) > 0:
            #         :
            #             l = {index: value for index, value in enumerate(services[service])}
            #
            #         result.append()
        print(result)
        return result

    # @property
    # def services(self):
    #     return self.__services
    #
    # @services.setter
    def services_state(self, ):
        # services_state =
        return {key: self.services[key].state() for key in self.services}

    def do_action(self):
        pass

    def serve(self):
        return self.gen_page()

    def state(self):
        return {'state': 'Running',
                'domain': self.domain,
                'node_id': self.node
                }
