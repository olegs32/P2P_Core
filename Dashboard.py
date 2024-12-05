import json

import streamlit as st
import asyncio
import websockets
import asyncio
import configparser
import threading
from datetime import datetime
import os
import json
import shutil
import time

import requests
import uptime
import streamlit as st
from streamlit_option_menu import option_menu
from streamlit.runtime.scriptrunner import add_script_run_ctx
from src.st_styles import *
from src.st_tabs import *

START_TIME = time.time()
API_SERVER = f'http://127.0.0.1:8080'
# INTERACTIVE_TRESHOLD = 120
post_data = {'action': 'serve'}

if 'resp' not in st.session_state:
    st.session_state.resp = requests.post(url=f'{API_SERVER}/route?src=debug&dst=NODE_direct&service=web',
                                          json=post_data).json()
resp = st.session_state.resp
st.set_page_config(page_title=resp.get('data').get('menu_title'), layout="wide", menu_items={})


async def listen_to_server():
    uri = f"ws://{API_SERVER}/ws"  # WebSocket-сервер
    async with websockets.connect(uri) as websocket:
        while True:
            message = json.loads(await websocket.recv())

            st.write(message)
            # st.rerun()


class Action:
    def __init__(self):
        pass

    def write(self, arg):
        st.write(arg)

    def empty(self, arg):
        st.warning(f'No Action defined to {arg}')

    def send_action(self, msg):
        print(msg)


actions = Action()

# st.write(resp.get('data'))
if resp.get('successfully') is True:
    data = resp.get('data')
    options = list(data.get('menu').keys())
    icons = [data.get('menu').get(x).get('icon') for x in options]

    selected = option_menu(
        menu_title=data.get('menu_title'),  # Заголовок меню
        options=options,  # Варианты меню
        icons=icons,  # Иконки для меню
        menu_icon="server",  # Иконка для меню
        default_index=0,  # Индекс по умолчанию
        orientation="horizontal",  # Горизонтальное расположение
        styles=menu_style
    )
    content = data.get('menu').get(selected).get('content')
    for item in content:
        if item.get('type') == 'custom_table':
            rows = item.get('rows')
            columns = list(st.columns(item.get('cols')))
            for index, row in enumerate(rows):
                for cell in row:
                    with columns[index]:
                        st.button(cell, on_click=actions.send_action(row[cell]))

        else:
            obj = getattr(st, item.get('type'))
            element = obj(item.get('label'))
            if 'action' in item:
                action = item.get('action')
                if element:
                    act = getattr(actions, action, None)
                    if act:
                        act(item.get('cmd'))
                    else:
                        actions.empty(action)
                    st.write('acted')
                # st.write(element)
            # st.write()

#
#
# # from streamlit.runtime.scriptrunner.script_run_context import get_script_run_ctx
#
# # st.write('begin')
# #
# #
# async def listen_to_server():
#     uri = "ws://127.0.0.1:8080/ws"  # WebSocket-сервер
#     async with websockets.connect(uri) as websocket:
#         while True:
#             message = json.loads(await websocket.recv())
#
#             st.write(message)
#             # st.rerun()
#
#
# # Запуск корутины с WebSocket в Streamlit
# # asyncio.run(listen_to_server())
# #
# #
#
# class WebSocketClient:
#     def __init__(self, uri):
#         self.uri = uri
#         self.connection = None
#
#     async def connect(self):
#         """Устанавливает соединение с сервером."""
#         self.connection = await websockets.connect(self.uri)
#
#     async def send_with_callback(self, data, callback):
#         """
#         Отправляет данные на сервер и ожидает ответ, вызывая callback.
#         :param data: Данные для отправки (dict).
#         :param callback: Функция для обработки ответа.
#         """
#         if not self.connection:
#             st.warning("Соединение не установлено.")
#             return
#
#         try:
#             # Отправляем данные
#             await self.connection.send(json.dumps(data))
#
#             # Получаем ответ от сервера
#             response = await self.connection.recv()
#             response_data = json.loads(response)
#
#             # Вызываем callback с полученными данными
#             callback(response_data)
#         except Exception as e:
#             st.error(f"Ошибка при отправке или получении данных: {e}")
#
#     async def close(self):
#         """Закрывает соединение."""
#         if self.connection:
#             await self.connection.close()
#             self.connection = None
#
#

#

#
# if 'LAST_EVENT_ID' not in st.session_state:
#     st.session_state.LAST_EVENT_ID = 0
# if 'messages' not in st.session_state:
#     st.session_state.messages = []
# if 'fetch_thread' not in st.session_state:
#     st.session_state.fetch_thread = None
# if 'stop_thread' not in st.session_state:
#     st.session_state.stop_thread = False
# if 'started_thread' not in st.session_state:
#     st.session_state.started_thread = False
# if "ws_client" not in st.session_state:
#     st.session_state.ws_client = WebSocketClient(uri="ws://127.0.0.1:8080/ws")
#     st.session_state.connected = False
#     try:
#         asyncio.run(st.session_state.ws_client.connect())
#         st.session_state.connected = True
#         st.success("Соединение установлено.")
#     except Exception as e:
#         st.error(f"Ошибка подключения: {e}")
#
# ws_client = st.session_state.ws_client
# global messages_container
#
# # st.session_state.last_update = int(time.time())
# st.markdown(page_style, unsafe_allow_html=True)
# st.markdown(card_style, unsafe_allow_html=True)
# st.markdown(card_style, unsafe_allow_html=True)
# st.markdown(button_container_style, unsafe_allow_html=True)
#
#
# def display_messages():
#     global messages_container
#     for message in st.session_state.messages:
#         messages_container.chat_message(message["role"]).st.write(message["data"])
#
#
# # st.markdown(css_style, unsafe_allow_html=True)
#
# def post_event(event):
#     response = requests.post(API_URL, json={"data": event, "role": "user"})
#     if response.status_code == 200:
#         st.success("Event posted successfully")
#     else:
#         st.error("Failed to post event")
#
#
# def fetch_events():
#     global messages_container
#     # with st.sidebar:
#
#     # st.session_state.messages_container.chat_message("assistant").write('b4stop')
#
#     # st.success(int(time.time()) - st.session_state.last_update)
#     # st.success(int(time.time()) - START_TIME)
#     while int(int(time.time()) - START_TIME) < INTERACTIVE_TRESHOLD:
#         print(int(time.time()) - START_TIME)
#         # while True:
#         response = requests.get(API_URL, params={"last_event_id": st.session_state.LAST_EVENT_ID})
#         # st.session_state.messages_container.chat_message('ai').write(response.json())
#         # st.write(response.text)
#
#         if response.status_code == 200:
#             events = response.json().get("events", [])
#             if events:
#                 if events[-1]['id'] > st.session_state.LAST_EVENT_ID:
#                     for event in events:
#                         # st.write(event, st.session_state.LAST_EVENT_ID, events[-1]['id'])
#                         if event['id'] > st.session_state.LAST_EVENT_ID:
#                             # st.session_state.messages_container.chat_message("assistant").write(event)
#                             st.session_state.messages.append(event)
#                     st.session_state.LAST_EVENT_ID = events[-1]['id']
#         # st.session_state.messages_container.chat_message("assistant").write('looped')
#
#         time.sleep(2)
#     st.session_state.started_thread = False
#     st.warning("Longpoll finished")
#     print("Longpoll finished:", int(time.time()) - START_TIME)
#
#     # st.session_state.messages_container.chat_message("assistant").write('looped2')
#
#
# def start_fetch_thread():
#     st.session_state.fetch_thread = threading.Thread(target=fetch_events)
#     add_script_run_ctx(st.session_state.fetch_thread)
#     st.session_state.fetch_thread.start()
#     st.write('LongPolling...', time.time())
#     st.session_state.started_thread = True
#
#
# def stop_fetch_thread():
#     st.session_state.stop_thread = True
#     if st.session_state.fetch_thread is not None:
#         st.session_state.fetch_thread.join()
#
#
# def show_card(title, content):
#     st.markdown(
#         f'<div class="card"><div class="card-title">{title}</div>'
#         f'<div class="card-content">{content}</div></div>',
#         unsafe_allow_html=True)
#
#
# def show_card_warn(title, content):
#     st.markdown(
#         f'<div class="card-warn"><div class="card-title">{title}</div>'
#         f'<div class="card-content-warn">{content}</div></div>',
#         unsafe_allow_html=True)
#
#
# def pub_project(executor, addition_files):
#     if executor is not None:
#         with open(f"{REPOS[0]}\\{codename}\\{executor.name}", 'wb') as f:
#             f.write(executor.getvalue())
#     if addition_files is not None:
#         for file in addition_files:
#             with open(f"{REPOS[0]}\\{codename}\\{file.name}", 'wb') as f:
#                 f.write(file.getvalue())
#     st.write('Successfully published!')
#
#
# def remove_project(path):
#     st.write("Project removed")
#     shutil.rmtree(path)
#     requests.get('http://127.0.0.1:8081/get/projects?rescan=True')
#
#
# def deploy_actions(clients, projects, action):
#     bars = []
#     log = []
#     tasks = (len(clients) * len(projects))
#     step = 1 / tasks
#     cur_progress = 0
#     for client in clients:
#         for project in projects:
#             result = (requests.get(f"http://127.0.0.1:8081/client/{client}/{project}/{action}"))
#             st.write(result.text)
#             cur_progress += step
#             if cur_progress != tasks:
#                 st.progress(cur_progress, text='Submitting')
#     # time.sleep(1)
#
#     return log
#
#
# def frontend():
#
#     with st.sidebar:
#         pass
#         # Создаем горизонтальное меню
#
#         # if st.session_state.started_thread is False:
#         #     start_fetch_thread()
#         #     # st.write('th started')
#         # # st.write(st.session_state.messages)
#         # if len(st.session_state.messages) == 0:
#         #     time.sleep(0.1)
#         # for message in st.session_state.messages:
#         #     messages_container.chat_message("assistant").write(message)
#         # if prompt := st.chat_input("Enter command", ):
#         #     post_event(prompt)
#         #     time.sleep(0.5)
#         #     messages_container.chat_message("assistant").write(st.session_state.messages[1])
#         #     st.write(st.session_state.messages)
#         #     messages_container.chat_message("user").write(prompt)
#
#         # if st.session_state.fetch_thread is None:
#
#         # Отображение чата
#
#     if selected == "Dashboard":
#         pass
#         # req = requests.get('http://127.0.0.1:8081/get/dashboard').json()
#         # ffirst, fsecond, fthird, fetc = st.columns([1, 1, 1, 1])
#         # with ffirst:
#         #     with st.container(border=True):
#         #         show_card('System', f"""
#         #                     Response time: {round(time.time() - START_TIME, 5)}
#         #                     """, )
#         #
#         # with fsecond:
#         #     with st.container(border=True):
#         #         show_card('Uptime:', f'{req["uptime"]}')
#         #
#         #         # Отображение uptime
#         #         # st.write(f'Response time: {round(time.time() - start_time, 5)}')
#         # with fthird:
#         #     with st.container(border=True):
#         #         show_card(f'Summary Agents: {req["agents"]}', 'online: {online}')
#         #
#         #         # Отображение uptime
#         #         # st.write(f'Response time: {round(time.time() - start_time, 5)}')
#         # with fetc:
#         #     with st.container(border=True):
#         #         show_card(f'Repository projects: {len(req["repos_projects"])}', req["repos_projects"])
#
#         # Отображение uptime
#
#     elif selected == "Workers":
#         data = {"cmd": 'get', 'param': 'agents'}
#         asyncio.run(ws_client.send_with_callback(data, handle_workers))
#     # req = requests.get('http://127.0.0.1:8081/get/workers').json()
#     # data = {}
#     # left_column, right_column = st.columns([2, 2])
#     # with left_column:
#     #     worker = {}
#     #     # for i in req['workers']:
#     #     #     worker[f"{i}_{req['workers'][i]['hostname']}"] = i
#     #     # worker = st.selectbox('select workers', options=worker)
#     #     with st.container(border=True, height=400):
#     #         st.markdown('<div class="button-container">', unsafe_allow_html=True)
#     #
#     #         for i in req['workers']:
#     #             btype = 'primary'
#     #             if req['workers'][i]['status'] == 'Online':
#     #                 btype = 'secondary'
#     #             btn = st.button(f"{i}_{req['workers'][i]['hostname']}", type=btype)
#     #             if btn:
#     #                 data = req['workers'][i]
#     #         st.markdown('</div>', unsafe_allow_html=True)
#     #
#     #     # bworkers
#     #
#     # with right_column:
#     #     if data:
#     #         with st.container(border=True, height=400):
#     #             for topic in data:
#     #                 if topic != 'services':
#     #                     try:
#     #                         st.markdown(f":blue[{str(locale[topic])}]: {data[topic]}")
#     #                     except KeyError:
#     #                         st.markdown(f":orange[{str(topic)}]: {data[topic]}")
#     #                 if topic == 'services':
#     #                     for key in data[topic]:
#     #                         if key == 'hosted_projects':
#     #                             try:
#     #                                 # st.markdown(f":blue[{str(locale[key])}]: {data[topic][key]}")
#     #                                 st.markdown(f":blue[{str(locale[key])}]: {data[topic][key]}")
#     #                             except KeyError:
#     #                                 st.markdown(f":blue[{str(key)}]: {data[topic][key]}")
#     #                             st.divider()
#     #                         else:
#     #                             # st.write(data)
#     #                             # st.write(topic)
#     #                             st.subheader(data[topic][key]['name'])
#     #                             # try:
#     #                             #     st.markdown(f":blue[{str(locale[key])}]: {data[topic][key]}")
#     #                             # except KeyError:
#     #                             #     st.markdown(f":orange[{str(key)}]: {data[topic][key]}")
#     #                             for param in data[topic][key]:
#     #                                 try:
#     #                                     st.markdown(f":blue[{str(locale[param])}]: {data[topic][key][param]}")
#     #                                 except KeyError:
#     #                                     st.markdown(f":orange[{str(param)}]: {data[topic][key][param]}")
#     #                             actions = ['deploy', 'remove', 'start', 'stop', 'restart']
#     #                             with st.popover('Control'):
#     #                                 for i in actions:
#     #                                     st.button(i.capitalize(), key=i.capitalize() + key,
#     #                                               on_click=deploy_actions, args=([data['id']], [key], i))
#     #
#     #                             st.divider()
#
#     # st.write(f'Summary processing time: {round(time.time() - start_time, 5)} secs')
#
#     elif selected == "Projects":
#         req = requests.get('http://127.0.0.1:8081/get/projects?rescan=False').json()
#         data = {}
#         REPOS = req['REPOS']
#         project = {}
#         conf = configparser.ConfigParser()
#
#         # new_proj_persist = False
#
#         new_proj = st.checkbox('Create new project')
#         rescan = st.button('Rescan_projects')
#         if rescan:
#             req = requests.get('http://127.0.0.1:8081/get/projects?rescan=True').json()
#         left_column, right_column = st.columns([2, 2])
#
#         with left_column:
#
#             with right_column:
#                 if new_proj:
#                     with st.container(border=True, height=400) as c:
#                         name = st.text_input('Name')
#
#                         if name:
#                             cn = name.replace(' ', '_')
#                             codename = st.text_input('Code_Name', value=cn)
#                             if os.path.exists(f"{REPOS[0]}\\{codename}"):
#                                 show_card_warn("Warning!", "Project with same name already existing!")
#
#                             executor = st.file_uploader('Execution file', accept_multiple_files=False, type='exe')
#                             #     st.form_submit_button('Register new project')
#                             addition_files = st.file_uploader('Addition files', accept_multiple_files=True, )
#                             service = st.checkbox('Service (enable watchdog)')
#                             version = st.number_input('Version', step=1, )
#                             args = st.text_input('Run arguments')
#                             publish = st.button('Publicate!')
#
#                             if publish:
#                                 if executor is not None:
#                                     try:
#                                         os.mkdir(f"{REPOS[0]}\\{codename}")
#                                         pub_project(executor, addition_files)
#                                     except FileExistsError:
#
#                                         # pub_pr = st.checkbox('Continue')
#                                         # if pub_pr:
#                                         pub_project(executor, addition_files)
#                                     project[codename] = {}
#                                     project[codename]['loader'] = executor.name
#                                     project[codename]['version'] = version
#                                     project[codename]['parameters'] = args
#                                     project[codename]['name'] = name
#                                     project[codename]['service'] = service
#                                     conf.read_dict(project)
#                                     with open(f"{REPOS[0]}\\{codename}\\project.ini", 'w') as f:
#                                         conf.write(f)
#                                     st.write(executor, publish)
#                                     req = requests.get('http://127.0.0.1:8081/get/projects?rescan=True').json()
#
#             # with right_column:
#
#             with st.container(border=True, height=400):
#                 # st.markdown('<div class="button-container">', unsafe_allow_html=True)
#                 for i in req['projects']:
#                     btn = st.button(i)
#                     if btn:
#                         data = req['projects'][i]
#                 # st.markdown('</div>', unsafe_allow_html=True)
#
#                 # bworkers
#
#                 with right_column:
#                     # st.write(data)
#                     if data:
#                         with st.container(border=True, height=400):
#                             # with st.container(border=True):
#                             # st.markdown(f'<div class="card">', unsafe_allow_html=True,)
#                             st.button('Remove project', on_click=remove_project,
#                                       args=(f"{REPOS[0]}\\{data['codename']}",))
#
#                             for key in data:
#                                 try:
#                                     st.markdown(f":blue[{str(locale[key])}]: {data[key]}")
#                                 except KeyError:
#                                     st.markdown(f":orange[{str(key)}]: {data[key]}")
#
#                     # st.markdown('</div>', unsafe_allow_html=True)
#
#         # st.write(f'Summary processing time: {round(time.time() - start_time, 5)} secs')
#
#
#
#     elif selected == "Deployment":
#         projects = requests.get('http://127.0.0.1:8081/get/projects?rescan=False').json()
#         clients = requests.get('http://127.0.0.1:8081/get/workers').json()
#         data = {}
#         REPOS = projects['REPOS']
#         project = {}
#
#         left_column, middle, right_column = st.columns([1, 1, 1])
#
#         with left_column:
#             with st.container(border=True):
#                 selected_projects = st.multiselect('Projects', projects['projects'])
#                 # for i in projects['projects']:
#                 #     btn = st.checkbox(i)
#                 #     if btn:
#                 #         data = projects['projects'][i]
#         with middle:
#             with st.container(border=True):
#                 selected_clients = st.multiselect('Clients', clients['workers'])
#
#                 for i in clients['workers']:
#                     btn = st.checkbox(i)
#                     if btn:
#                         data = projects['projects'][i]
#
#         with right_column:
#             with st.container(border=True):
#                 if selected_clients and selected_projects:
#
#                     # st.write(data)
#                     #     if data:
#                     # with st.container(border=True):
#                     # st.markdown(f'<div class="card">', unsafe_allow_html=True,)
#                     st.title('Actions:')
#                     actions = ['deploy', 'remove', 'start', 'stop', 'restart']
#                     for i in actions:
#                         st.button(i.capitalize(), on_click=deploy_actions,
#                                   args=(selected_clients, selected_projects, i))
#                     # st.button('Remove', on_click=deploy_actions, args=(selected_clients, selected_projects, 'remove'))
#                     # st.button('Start', on_click=deploy_actions, args=(selected_clients, selected_projects, 'start'))
#                     # st.button('Stop', on_click=deploy_actions, args=(selected_clients, selected_projects, 'stop'))
#                     # st.button('Restart', on_click=deploy_actions, args=(selected_clients, selected_projects, 'restart'))
#                     st.write()
#
#         # st.write(f'Summary processing time: {round(time.time() - start_time, 5)} secs')
#     elif selected == "Console":
#         if st.session_state.started_thread is False:
#             start_fetch_thread()
#             # st.write('th started')
#         old_len = 0
#         st.subheader('Console')
#         messages_container = st.container(height=600)
#         if prompt := st.chat_input("Enter command", ):
#             post_event(prompt)
#             # time.sleep(0.2)
#             # messages_container.chat_message("user").write(prompt)
#
#         while int(int(time.time()) - START_TIME) < INTERACTIVE_TRESHOLD:
#             if len(st.session_state.messages) != old_len:
#                 diff = (len(st.session_state.messages) - old_len)
#                 if diff > 0:
#                     data = list(range(1, diff + 1))
#                     data.reverse()
#                     for i in data:
#                         # Debug
#                         # messages_container.chat_message("ai").write(f'{diff}, {i}, {data}')
#
#                         message = st.session_state.messages[i * -1]
#                         messages_container.chat_message(message["role"]).write(message["data"])
#                 old_len = len(st.session_state.messages)
#
#             time.sleep(1)
#
#     st.markdown(page_style, unsafe_allow_html=True)
#     st.write(f'Request serviced in {round(time.time() - START_TIME, 5)} secs')
#
#
# if __name__ == "__main__":
#     frontend()
