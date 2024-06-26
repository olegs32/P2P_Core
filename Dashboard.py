from datetime import datetime
import os
import json
import shutil
import time

import requests
import uptime
import streamlit as st
from streamlit_option_menu import option_menu

start_time = time.time()

headings = ["Dashboard", "Workers", "Projects", "Deployment"]
st.set_page_config(page_title="CentralDeploymentCore", layout="wide", menu_items={})

# def generate_cards(data):
#     for card in data:
#         show_card(card['caption'], card['text'], attachments=card['attachments'])


# def parse_practics(folder):
#     path = os.getcwd() + f'\\data\\{folder}\\'
#     pract = []
#     # result = {'attachments': []}
#     dirs = list(os.walk(path))[0][1]
#     print(dirs)
#     if len(dirs) > 0:
#         for f in dirs:
#             print(f)
#             result = {'attachments': []}
#
#             with open(f'{path}{f}\\data.txt') as fi:
#                 file = fi.read()
#                 print(file)
#                 data = json.loads(file)
#                 result['caption'] = data['caption']
#                 result['text'] = data['text']
#             for attachment in list(os.walk(f'{path}{f}'))[0][2]:
#                 print('attachment', attachment)
#                 if attachment != 'data.txt':
#                     result['attachments'].append(f'{path}{f}\\{attachment}')
#             pract.append(result)
#     return pract
#
#
# def receive_new_post(caption, text, heading, attachments=None):
#     path = os.getcwd() + f'\\data\\uploaded\\{heading}\\'
#     os.makedirs(path, exist_ok=True)
#     count = len(list(os.walk(path)))
#     os.mkdir(path + f'{count + 1}')
#     with open(path + f'{count + 1}\\data.txt', 'w') as f:
#         f.write(json.dumps({"caption": caption, "text": text}))
#     if attachments:
#         for file in attachments:
#             with open(path + f'{count + 1}\\' + file.name, 'wb') as f:
#                 f.write(file.getvalue())
#         # file.save(path + f'{count + 1}')
#
#
# def public_post(path):
#     to = path.replace('uploaded\\', '') + str(time.time())
#     shutil.move(path, to)
#     pass
#
#
# def moder_projects_name():
#     result = []
#     path = os.getcwd() + f'\\data\\uploaded\\'
#     print(list(os.walk(path)))
#     for i in list(os.walk(path)):
#         if 'data.txt' in str(i):
#             result.append(list(i))
#     if result == {}:
#         return None
#     else:
#         return result


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

with st.sidebar:
    # Создаем горизонтальное меню
    selected = option_menu(
        menu_title='Central Deployment Node',  # Заголовок меню
        options=headings,  # Варианты меню
        icons=["house", "gear", "tools"],  # Иконки для меню
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
# show_sidebar = st.sidebar.checkbox("Показать форму добавления контента", False)

# css_style = """
#     .custom-container {
#         background-color: #87CEEB; /* Синеватый фон */
#         padding: 20px;
#         border-radius: 15px; /* Скругленные углы */
#         box-shadow: 0px 4px 8px rgba(0, 0, 0, 0.1); /* Тень */
#         margin: 20px;
#     }
# """
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


# st.markdown(css_style, unsafe_allow_html=True)

def show_card(title, content):
    st.markdown(
        f'<div class="card"><div class="card-title">{title}</div><div class="card-content">{content}</div></div>',
        unsafe_allow_html=True)


if selected == "Dashboard":
    req = requests.get('http://127.0.0.1:8081/get/dashboard').json()
    first, second, third, etc = st.columns([1, 1, 1, 1])
    with first:
        with st.container(border=True):
            show_card('System', f"""
                        Response time: {round(time.time() - start_time, 5)}
                        """, )

    with second:
        with st.container(border=True):
            show_card('Uptime:', f'{req["uptime"]}')

            # Отображение uptime
            # st.write(f'Response time: {round(time.time() - start_time, 5)}')
    with third:
        with st.container(border=True):
            show_card(f'Summary Agents: {req["agents"]}', 'online: {online}')

            # Отображение uptime
            # st.write(f'Response time: {round(time.time() - start_time, 5)}')
    with etc:
        with st.container(border=True):
            show_card(f'Repository projects: {len(req["repos_projects"])}', req["repos_projects"])

            # Отображение uptime
    st.write(f'Summary processing time: {round(time.time() - start_time, 5)} secs')


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

    st.write(f'Summary processing time: {round(time.time() - start_time, 5)} secs')


elif selected == "Projects":
    req = requests.get('http://127.0.0.1:8081/get/projects').json()
    data = {}
    # new_proj_persist = False

    new_proj = st.checkbox('Create new project')
    left_column, right_column = st.columns([2, 2])

    with left_column:

        with right_column:
            with st.container(border=True):

                if new_proj:
                    name = st.text_input('Name')

                    if name:
                        cn = name.replace(' ', '_')
                        codename = st.text_input('Code_Name', value=cn)
                    executor = st.file_uploader('Execution file', accept_multiple_files=False, type='exe')
                    #     st.form_submit_button('Register new project')
                    addition_files = st.file_uploader('Addition files', accept_multiple_files=True, )
                    version = st.number_input('Version', step=0.1, )

        # with right_column:

        with st.container(border=True):
            st.markdown('<div class="button-container">', unsafe_allow_html=True)

            for i in req['projects']:
                btype = 'primary'
                # if req['workers'][i]['status'] == 'Online':
                #     btype = 'secondary'
                btn = st.button(i, )
                if btn:
                    data = req['projects'][i]
            st.markdown('</div>', unsafe_allow_html=True)

        # bworkers
        with right_column:

            if data:
                with st.container(border=True):
                    for key in data:
                        st.write(f"{key}:", data[key])

    st.write(f'Summary processing time: {round(time.time() - start_time, 5)} secs')


elif selected == "Deployment":
    pass
    st.write(f'Summary processing time: {round(time.time() - start_time, 5)} secs')

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

# # Отображаем значок карандаша
# # show_sidebar = st.sidebar.checkbox("Показать форму добавления контента", False)
# # show_moderate = st.sidebar.button("Показать форму модерации контента", False)
# # if show_sidebar:
# #     st.sidebar.header("Форма добавления контента")
# #
# #     # Поля для ввода заголовка и текста
# #     title = st.sidebar.text_input("Заголовок", key="title")
# #     content = st.sidebar.text_area("Текст", key="content")
# #
# #     # Выпадающий список для выбора раздела
# #     section = st.sidebar.selectbox("Выберите раздел", headings, key="section")
# #
# #     # Кнопка для прикрепления файлов
# #     uploaded_files = st.sidebar.file_uploader("Прикрепить файлы", accept_multiple_files=True, key="files")
# #
# #     # Кнопка для отправки формы
# #     if st.sidebar.button("Отправить"):
# #         # Отображаем введенные данные
# #         st.sidebar.write(f"**Успешно отправлено!** ")
# #         # st.sidebar.write(f"**Текст:** {content}")
# #         # st.sidebar.write(f"**Раздел:** {section}")
# #
# #         # Отображаем прикрепленные файлы
# #         if uploaded_files:
# #             st.sidebar.write("**Прикрепленные файлы:**")
# #             for file in uploaded_files:
# #                 st.sidebar.write(file.name)
# #             receive_new_post(title, content, section, uploaded_files)
# #         else:
# #             receive_new_post(title, content, section)
# #
# #
# #
# # if show_moderate:
# #     st.sidebar.header("Форма постмодернизма контента :)")
# #     projects = moder_projects_name()
# #     print(projects)
# #     if projects:
# #         # for project in projects:
# #         project = projects.pop(0)
# #         print('project', project)
# #         heading = str(project[0]).split('\\')[-2]
# #         # title = ''
# #         # text = ''
# #         with open(f'{project[0]}\\data.txt') as f:
# #             file = f.read()
# #             print(file)
# #             data = json.loads(file)
# #
# #         # Поля для ввода заголовка и текста
# #         title_text = st.sidebar.text_input("Заголовок", key="loaded_title", value=data['caption'])
# #         content_text = st.sidebar.text_area("Текст", key="loaded_content", value=data['text'])
# #
# #         # Выпадающий список для выбора раздела
# #         section = st.sidebar.selectbox("Выберите раздел", headings, key="loaded_section",)
# #
# #         st.sidebar.header("Доступные файлы для скачивания")
# #         # Отображаем ссылки для скачивания файлов
# #         for filename in project[2]:
# #             if filename != 'data.txt':
# #
# #                 filepath = os.path.join(project[0], filename)
# #                 with open(filepath, "rb") as file:
# #                     if filename.endswith('.jpg'):
# #                         st.sidebar.image(filepath)
# #                     btn = st.sidebar.download_button(
# #                         label=f"Скачать {filename}",
# #                         data=file,
# #                         file_name=filename,
# #                         mime="application/octet-stream"
# #                     )
# #                     print(filename)
# #
# #         # Кнопка для прикрепления файлов
# #         uploaded_files = st.sidebar.file_uploader("Перезаписать исправленные файлы", accept_multiple_files=True,
# #                                                   key="loaded_files")
# #
# #         # Кнопка для отправки формы
# #         if st.sidebar.button("Разгласить"):
# #             # Отображаем введенные данные
# #             st.sidebar.write(f"**Успешно опубликовано:** ")
# #             # st.sidebar.write(f"**Текст:** {content}")
# #             # st.sidebar.write(f"**Раздел:** {section}")
# #             #
# #             # Отображаем прикрепленные файлы
# #             # if uploaded_files:
# #             #     st.sidebar.write("**Прикрепленные файлы:**")
# #             #     for file in uploaded_files:
# #             #         st.sidebar.write(file.name)
# #             #     receive_new_post(title, content, section, uploaded_files)
# #             # # receive_new_post(title, content, section)
#             public_post(project[0])
