from streamlit_option_menu import option_menu

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
page_style = """
<style>
    .stApp {
        background-color: white;
    }

</style>
"""


locale = {'id': 'ID',
          'codename': 'Coding project name',
          'name': 'Name',
          'service': 'Service',
          'parameters': 'Parameters to run',
          'version': 'Version',
          'loader': 'Loader',
          'path': 'Repository',
          'status': 'Status',
          'files': 'Files',
          'last_connect': 'Last ping',
          'hostname': 'Hostname',
          'ttl': 'TTL',
          }

menu_style = {
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



