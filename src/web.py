import time
from collections import defaultdict
from typing import Dict, List

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
        print('Generating web page')

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

        agents = self.services.get('lp').get_clients()
        acts = ['deploy', 'update', 'start', 'stop', 'restart', 'remove']
        data = {}
        # data: Dict[str, List[Dict[str: str, str: str, str: str]]]
        for agent in agents:
            data[agent] = []
            for project in self.services.get('project_store').state():

                status = self.services.get('project_manager').status(agent, project)
                data[agent].append({'type': 'write', 'label': project, 'value': ''})
                data[agent].append({'type': 'write', 'label': status, 'value': ''})
                for act in acts:
                    data[agent].append({'type': 'button', 'label': f'{act}_service', 'value': ''})
        print('Panel generated')
        return {'type': 'custom_table', 'cols': 8, 'agents': data}

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
