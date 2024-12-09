import json
import random

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
# import uptime
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
# st.write(resp)

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

    def gen_action(self, item):
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


actions = Action()
# st.write(st.columns)

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
        # st.write(item)
        if item.get('type') == 'custom_table':
            rows = item.get('agents')
            # st.write(rows)
            for agent in rows:
                st.header(agent)

                with st.container(border=True):
                    columns = st.columns(item.get('cols'))
                    # st.write(rows[agent])
                    for index, row in enumerate(rows[agent]):
                        # st.write(index)
                        with columns[index]:
                            actions.gen_action(row)
                st.divider()
        else:
            actions.gen_action(item)
else:
    st.write(resp)
