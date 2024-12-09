import time

import requests

# data = {'action': 'gen_page'}
# resp = requests.post(, json=data)
#
# print(resp.status_code)
# print(resp.content)
data = {'action': 'start_service', 'service': 'anydesk'}
resp = requests.post(f'http://127.0.0.1:8080/route?src=debug&dst=direct_sysadmin-pc&service=anydesk', json=data)
print(resp.status_code)
print(resp.content)
time.sleep(5)
data = {'action': 'stop_service', 'service': 'anydesk'}
resp = requests.post(f'http://127.0.0.1:8080/route?src=debug&dst=direct_sysadmin-pc&service=anydesk', json=data)

print(resp.status_code)
print(resp.content)

# l = [1, 2, 3, 4, 5, 6, 7, 8, 9]
#
#
# def up(q):
#     return q ** 2
#
#
# print(list(map(up, l)))
#
# td = {'up': up}
#
# print(list(map(td.get('up'), l)))
