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
from streamlit.runtime.scriptrunner.script_run_context import get_script_run_ctx

START_TIME = time.time()
API_URL = "http://127.0.0.1:8081/events"
INTERACTIVE_TRESHOLD = 120

headings = ["Dashboard", "Workers", "Projects", "Deployment", "Console"]
st.set_page_config(page_title="CentralDeploymentCore", layout="wide", menu_items={})
if 'LAST_EVENT_ID' not in st.session_state:
    st.session_state.LAST_EVENT_ID = 0
if 'messages' not in st.session_state:
    st.session_state.messages = []
if 'fetch_thread' not in st.session_state:
    st.session_state.fetch_thread = None
if 'stop_thread' not in st.session_state:
    st.session_state.stop_thread = False
if 'started_thread' not in st.session_state:
    st.session_state.started_thread = False
global messages_container

# st.session_state.last_update = int(time.time())

# Устанавливаем стиль страницы
page_style = """
<style>
    .stApp {
        background-color: white;
    }

</style>
"""

st.markdown(page_style, unsafe_allow_html=True)

card_style = """
<style>
    .card {
        background-color: #ffffff; /* Белый фон */
        border: 2px solid #4CAF50; /* Зеленая граница */
        border-radius: 15px; /* Скругленные углы */
        padding: 20px;
        margin: 10px 0;
        box-shadow: 0px 4px 8px rgba(0, 0, 0, 0.1); /* Тень */
    }
    .card h2 {
        color: #4CAF50; /* Зеленый цвет заголовка */
        font-weight: bold; /* Жирный шрифт */
        margin-bottom: 10px;
    }
    .card p {
        color: #333333; /* Темно-серый текст */
    }
    body {
        background-color: #f0f2f6; /* Светло-серый фон страницы */
    }
</style>
"""

# menu, content = st.columns([1, 3])
# Вставляем CSS стили
# with menu:
st.markdown(card_style, unsafe_allow_html=True)

# show_sidebar = st.sidebar.checkbox("Показать форму добавления контента", False)

card_style = """
<style>
    .card {
        background-color: #FFFFFF;
        border-radius: 10px;
        padding: 20px;
        box-shadow: 0px 4px 8px rgba(0, 0, 0, 0.1);
        margin-bottom: 20px;
    }
    .card-warn {
        background-color: #CC5500;
        border-radius: 10px;
        padding: 20px;
        box-shadow: 0px 4px 8px rgba(0, 0, 0, 0.1);
        margin-bottom: 20px;
    }
    .card-title {
        color: #0000FF;
        font-weight: bold;
        font-size: 18px;
        margin-bottom: 10px;
    }
    .card-content {
        color: #000000;
    }
    .card-content-warn {
        color: #000000;
        background-color: #CC5500;
        
    }
</style>
"""

# Вставляем CSS стили
st.markdown(card_style, unsafe_allow_html=True)

button_container_style = """
<style>
    .button-container {
        display: flex;
        justify-content: space-between;
        flex-wrap: wrap;
        gap: 10px; /* Расстояние между кнопками */
        background-color: #87CEEB;
        
        
    }
    .button-container .stButton button {
        flex: 1;
        margin: 0; /* Убираем отступы */
    }
</style>
"""
st.markdown(button_container_style, unsafe_allow_html=True)


def display_messages():
    global messages_container
    for message in st.session_state.messages:
        messages_container.chat_message(message["role"]).st.write(message["data"])


# st.markdown(css_style, unsafe_allow_html=True)

def post_event(event):
    response = requests.post(API_URL, json={"data": event, "role": "user"})
    if response.status_code == 200:
        st.success("Event posted successfully")
    else:
        st.error("Failed to post event")


def fetch_events():
    global messages_container
    with st.sidebar:

        # st.session_state.messages_container.chat_message("assistant").write('b4stop')

        # st.success(int(time.time()) - st.session_state.last_update)
        # st.success(int(time.time()) - START_TIME)
        while int(int(time.time()) - START_TIME) < INTERACTIVE_TRESHOLD:
            print(int(time.time()) - START_TIME)
            # while True:
            response = requests.get(API_URL, params={"last_event_id": st.session_state.LAST_EVENT_ID})
            # st.session_state.messages_container.chat_message('ai').write(response.json())
            # st.write(response.text)

            if response.status_code == 200:
                events = response.json().get("events", [])
                if events:
                    if events[-1]['id'] > st.session_state.LAST_EVENT_ID:
                        for event in events:
                            # st.write(event, st.session_state.LAST_EVENT_ID, events[-1]['id'])
                            if event['id'] > st.session_state.LAST_EVENT_ID:
                                # st.session_state.messages_container.chat_message("assistant").write(event)
                                st.session_state.messages.append(event)
                                # messages_container.success('Polled!')
                                messages_container.chat_message(event["role"]).write(event["data"])
                                # time.sleep(0.3)
                                # st.rerun()

                        st.session_state.LAST_EVENT_ID = events[-1]['id']
            # st.session_state.messages_container.chat_message("assistant").write('looped')

            time.sleep(2)
        st.session_state.started_thread = False
        st.warning("Longpoll finished")
        print("Longpoll finished:", int(time.time()) - START_TIME)

        # st.session_state.messages_container.chat_message("assistant").write('looped2')


def start_fetch_thread():
    st.session_state.fetch_thread = threading.Thread(target=fetch_events)
    add_script_run_ctx(st.session_state.fetch_thread)
    st.session_state.fetch_thread.start()
    st.write('LongPolling...', time.time())
    st.session_state.started_thread = True


def stop_fetch_thread():
    st.session_state.stop_thread = True
    if st.session_state.fetch_thread is not None:
        st.session_state.fetch_thread.join()


# st.on_session_end(stop_fetch_thread)

def show_card(title, content):
    st.markdown(
        f'<div class="card"><div class="card-title">{title}</div>'
        f'<div class="card-content">{content}</div></div>',
        unsafe_allow_html=True)


def show_card_warn(title, content):
    st.markdown(
        f'<div class="card-warn"><div class="card-title">{title}</div>'
        f'<div class="card-content-warn">{content}</div></div>',
        unsafe_allow_html=True)


def pub_project(executor, addition_files):
    if executor is not None:
        with open(f"{REPOS[0]}\\{codename}\\{executor.name}", 'wb') as f:
            f.write(executor.getvalue())
    if addition_files is not None:
        for file in addition_files:
            with open(f"{REPOS[0]}\\{codename}\\{file.name}", 'wb') as f:
                f.write(file.getvalue())
    st.write('Successfully published!')


def remove_project(path):
    st.write("Project removed")
    shutil.rmtree(path)
    requests.get('http://127.0.0.1:8081/get/projects?rescan=True')


def deploy_actions(clients, projects, action):
    bars = []
    log = []
    tasks = (len(clients) * len(projects))
    step = 1 / tasks
    cur_progress = 0
    for client in clients:
        for project in projects:
            result = (requests.get(f"http://127.0.0.1:8081/client/{client}/{project}/{action}"))
            st.write(result.text)
            cur_progress += step
            if cur_progress != tasks:
                st.progress(cur_progress, text='Submitting')
    # time.sleep(1)

    return log


with st.sidebar:
    # Создаем горизонтальное меню
    selected = option_menu(
        menu_title='Central Deployment Node',  # Заголовок меню
        options=headings,  # Варианты меню
        icons=["house", "gear", "tools", "pc", "terminal-plus"],  # Иконки для меню
        menu_icon="server",  # Иконка для меню
        default_index=0,  # Индекс по умолчанию
        orientation="vertical",  # Горизонтальное расположение
        styles={
            "nav-link": {"font-size": "20px", "text-align": "center", "margin": "0px"},
            "nav-link-selected": {"background-color": "green"},
            "menu_container_style": {
                "display": "flex",
                "justify-content": "center",
                "margin-top": "20px",
                "background-color": "transparent",
            },
            "menu_item_style": {
                "padding": "10px 20px",
                "margin": "0px 10px",
                "cursor": "pointer",
                "color": "black",
                "background-color": "transparent",
                "border-radius": "20px",
            },
            "menu_item_hover_style": {
                "background-color": "#87CEEB",  # Голубой при наведении
            },
            "menu_item_selected_style": {
                "background-color": "#FFFFFF",  # Белый при выборе
            },
            "menu_icon_style": {
                "color": "black",
                "font-size": "20px",
                "margin-right": "10px",
            },
        }
    )

    # if st.session_state.started_thread is False:
    #     start_fetch_thread()
    #     # st.write('th started')
    # # st.write(st.session_state.messages)
    # if len(st.session_state.messages) == 0:
    #     time.sleep(0.1)
    # for message in st.session_state.messages:
    #     messages_container.chat_message("assistant").write(message)
    # if prompt := st.chat_input("Enter command", ):
    #     post_event(prompt)
    #     time.sleep(0.5)
    #     messages_container.chat_message("assistant").write(st.session_state.messages[1])
    #     st.write(st.session_state.messages)
    #     messages_container.chat_message("user").write(prompt)

    # if st.session_state.fetch_thread is None:


    # Отображение чата

if selected == "Dashboard":
    req = requests.get('http://127.0.0.1:8081/get/dashboard').json()
    ffirst, fsecond, fthird, fetc = st.columns([1, 1, 1, 1])
    with ffirst:
        with st.container(border=True):
            show_card('System', f"""
                        Response time: {round(time.time() - START_TIME, 5)}
                        """, )

    with fsecond:
        with st.container(border=True):
            show_card('Uptime:', f'{req["uptime"]}')

            # Отображение uptime
            # st.write(f'Response time: {round(time.time() - start_time, 5)}')
    with fthird:
        with st.container(border=True):
            show_card(f'Summary Agents: {req["agents"]}', 'online: {online}')

            # Отображение uptime
            # st.write(f'Response time: {round(time.time() - start_time, 5)}')
    with fetc:
        with st.container(border=True):
            show_card(f'Repository projects: {len(req["repos_projects"])}', req["repos_projects"])

            # Отображение uptime


elif selected == "Workers":
    req = requests.get('http://127.0.0.1:8081/get/workers').json()
    data = {}
    left_column, right_column = st.columns([2, 2])
    with left_column:
        worker = {}
        # for i in req['workers']:
        #     worker[f"{i}_{req['workers'][i]['hostname']}"] = i
        # worker = st.selectbox('select workers', options=worker)
        with st.container(border=True):
            st.markdown('<div class="button-container">', unsafe_allow_html=True)

            for i in req['workers']:
                btype = 'primary'
                if req['workers'][i]['status'] == 'Online':
                    btype = 'secondary'
                btn = st.button(f"{i}_{req['workers'][i]['hostname']}", type=btype)
                if btn:
                    data = req['workers'][i]
            st.markdown('</div>', unsafe_allow_html=True)

        # bworkers

    with right_column:
        if data:
            with st.container(border=True):
                for key in data:
                    st.write(f"{key}:", data[key])

    # st.write(f'Summary processing time: {round(time.time() - start_time, 5)} secs')


elif selected == "Projects":
    req = requests.get('http://127.0.0.1:8081/get/projects?rescan=False').json()
    data = {}
    REPOS = req['REPOS']
    project = {}
    conf = configparser.ConfigParser()

    # new_proj_persist = False

    new_proj = st.checkbox('Create new project')
    rescan = st.button('Rescan_projects')
    if rescan:
        req = requests.get('http://127.0.0.1:8081/get/projects?rescan=True').json()
    left_column, right_column = st.columns([2, 2])

    with left_column:

        with right_column:
            with st.container(border=True):

                if new_proj:
                    name = st.text_input('Name')

                    if name:
                        cn = name.replace(' ', '_')
                        codename = st.text_input('Code_Name', value=cn)
                        if os.path.exists(f"{REPOS[0]}\\{codename}"):
                            show_card_warn("Warning!", "Project with same name already existing!")

                        executor = st.file_uploader('Execution file', accept_multiple_files=False, type='exe')
                        #     st.form_submit_button('Register new project')
                        addition_files = st.file_uploader('Addition files', accept_multiple_files=True, )
                        service = st.checkbox('Service (enable watchdog)')
                        version = st.number_input('Version', step=1, )
                        args = st.text_input('Run arguments')
                        publish = st.button('Publicate!')

                        if publish:
                            if executor is not None:
                                try:
                                    os.mkdir(f"{REPOS[0]}\\{codename}")
                                    pub_project(executor, addition_files)
                                except FileExistsError:

                                    # pub_pr = st.checkbox('Continue')
                                    # if pub_pr:
                                    pub_project(executor, addition_files)
                                project[codename] = {}
                                project[codename]['loader'] = executor.name
                                project[codename]['version'] = version
                                project[codename]['parameters'] = args
                                project[codename]['name'] = name
                                project[codename]['service'] = service
                                conf.read_dict(project)
                                with open(f"{REPOS[0]}\\{codename}\\project.ini", 'w') as f:
                                    conf.write(f)
                                st.write(executor, publish)
                                req = requests.get('http://127.0.0.1:8081/get/projects?rescan=True').json()

        # with right_column:

        with st.container(border=True):
            # st.markdown('<div class="button-container">', unsafe_allow_html=True)
            for i in req['projects']:
                btn = st.button(i)
                if btn:
                    data = req['projects'][i]
            # st.markdown('</div>', unsafe_allow_html=True)

        # bworkers
        with right_column:
            # st.write(data)
            if data:
                # with st.container(border=True):
                # st.markdown(f'<div class="card">', unsafe_allow_html=True,)
                st.button('Remove project', on_click=remove_project, args=(f"{REPOS[0]}\\{data['codename']}",))

                for key in data:
                    st.write(f"{key}:", data[key])

                # st.markdown('</div>', unsafe_allow_html=True)

    # st.write(f'Summary processing time: {round(time.time() - start_time, 5)} secs')



elif selected == "Deployment":
    projects = requests.get('http://127.0.0.1:8081/get/projects?rescan=False').json()
    clients = requests.get('http://127.0.0.1:8081/get/workers').json()
    data = {}
    REPOS = projects['REPOS']
    project = {}

    left_column, middle, right_column = st.columns([1, 1, 1])

    with left_column:
        with st.container(border=True):
            selected_projects = st.multiselect('Projects', projects['projects'])
            # for i in projects['projects']:
            #     btn = st.checkbox(i)
            #     if btn:
            #         data = projects['projects'][i]
    with middle:
        with st.container(border=True):
            selected_clients = st.multiselect('Clients', clients['workers'])

            for i in clients['workers']:
                btn = st.checkbox(i)
                if btn:
                    data = projects['projects'][i]

    with right_column:
        with st.container(border=True):
            if selected_clients and selected_projects:

                # st.write(data)
                #     if data:
                # with st.container(border=True):
                # st.markdown(f'<div class="card">', unsafe_allow_html=True,)
                st.title('Actions:')
                actions = ['deploy', 'remove', 'start', 'stop', 'restart']
                for i in actions:
                    st.button(i.capitalize(), on_click=deploy_actions, args=(selected_clients, selected_projects, i))
                # st.button('Remove', on_click=deploy_actions, args=(selected_clients, selected_projects, 'remove'))
                # st.button('Start', on_click=deploy_actions, args=(selected_clients, selected_projects, 'start'))
                # st.button('Stop', on_click=deploy_actions, args=(selected_clients, selected_projects, 'stop'))
                # st.button('Restart', on_click=deploy_actions, args=(selected_clients, selected_projects, 'restart'))
                st.write()

    # st.write(f'Summary processing time: {round(time.time() - start_time, 5)} secs')
elif selected == "Console":
    if st.session_state.started_thread is False:
        start_fetch_thread()
        # st.write('th started')
    old_len = 0
    messages_container = st.container(height=600)
    if prompt := st.chat_input("Enter command", ):
        post_event(prompt)
    while int(int(time.time()) - START_TIME) < INTERACTIVE_TRESHOLD:
        if len(st.session_state.messages) != old_len:
            diff = (len(st.session_state.messages) - old_len) * -1
            if diff < 0:
                for i in range(-1, diff):
                    message = st.session_state.messages[i]
                    messages_container.chat_message(message["role"]).st.write(message["data"])
            old_len = len(st.session_state.messages)
        #     display_messages()
        time.sleep(1)

page_style = """
<style>
    body {
        background-color: #0000FF; /* Синий фон страницы */
    }
    .edit-icon {
        position: fixed;
        bottom: 20px;
        right: 20px;
        width: 60px;
        height: 60px;
        background-color: #87CEEB; /* Синеватый фон иконки */
        color: #FFFFFF; /* Белый цвет иконки */
        border-radius: 50%; /* Круглый края */
        display: flex;
        justify-content: center;
        align-items: center;
        cursor: pointer;
        box-shadow: 0px 4px 8px rgba(0, 0, 0, 0.3); /* Тень */
        z-index: 1000; /* Должно быть выше контента */
    }
    .edit-icon:hover {
        background-color: #6495ED; /* Темно-голубой при наведении */
    }
</style>
"""

st.markdown(page_style, unsafe_allow_html=True)
st.write(f'Request serviced in {round(time.time() - START_TIME, 5)} secs')



