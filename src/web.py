class Web:
    def __init__(self):
        pass

    def create_actions(self):
        pass

    def gen_page(self):
        print('gen page')
        return example_page

    def gen_menu(self):
        pass

    def do_action(self):
        pass

    def serve_client(self):
        pass


example_page = {
    'menu': {
        'Dashboard': [
            {'type': 'success', 'label': 'Uptime', 'value': 'XX:XX:XX'},
            {'type': 'button', 'label': 'Uptime', 'action': 'start_test'}
        ]

    }

}
