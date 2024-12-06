import time
from collections import defaultdict
from typing import Dict

from src.tools import uptime


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

        services = self.gen_services_menu()
        up = uptime(self.data.get('start_time', 0))
        example_page = {
            'menu_title': self.node,
            'menu': {
                'Dashboard': {'icon': 'house', 'content': [
                    {'type': 'success', 'label': f'Uptime: {up}', 'value': ''},
                    self.control_panel()
                ]
                              },
                'States': {'icon': 'log', 'content': services
                           },
                'Logs': {'icon': 'log', 'content': services
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
            if len(services[service]) > -1:
                result.append({'type': 'subheader', 'label': service, 'value': ''}),
                result.append({'type': 'dataframe', 'label': services[service], 'value': ''}),
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

    def control_panel(self):
        # for project in self.services.get('project_store'):
        #     pass
        data = [{'name': 'anydesk',
                 'status': 'some status',
                 'start': 'start_service',
                 'restart': 'start_service',
                 'stop': 'start_service',
                 'remove': 'start_service',
                 'update': 'start_service',
                 'deploy': 'start_service',
                 }]
        return {'type': 'custom_table', 'cols': len(data[0]), 'rows': data}

    def logs(self):
        logs = []
        logs.append({'type': 'write', 'label': 'logs station', 'value': ''})
        return logs

    def serve(self):
        return self.gen_page()

    def state(self):
        return {'state': 'Running',
                'domain': self.domain,
                'node_id': self.node
                }
