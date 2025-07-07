"""
Streamlit admin interface for P2P Admin System
"""

import streamlit as st
import asyncio
import json
import time
from datetime import datetime, timedelta
import pandas as pd
import plotly.graph_objs as go
import plotly.express as px
from typing import Dict, List, Optional

import httpx
import websockets

from config.settings import get_admin_settings

# Получение настроек
settings = get_admin_settings()

# Конфигурация страницы
st.set_page_config(
    page_title=settings.page_title,
    page_icon=settings.page_icon,
    layout=settings.layout,
    initial_sidebar_state="expanded"
)

# Инициализация состояния сессии
if "api_client" not in st.session_state:
    st.session_state.api_client = None
if "network_status" not in st.session_state:
    st.session_state.network_status = None
if "selected_node" not in st.session_state:
    st.session_state.selected_node = None
if "metrics_history" not in st.session_state:
    st.session_state.metrics_history = []
if "last_update" not in st.session_state:
    st.session_state.last_update = None


class P2PAdminClient:
    """Клиент для взаимодействия с P2P API"""

    def __init__(self, api_url: str, token: Optional[str] = None):
        self.api_url = api_url.rstrip('/')
        self.token = token
        self.headers = {}
        if token:
            self.headers["Authorization"] = f"Bearer {token}"

    async def get_status(self) -> dict:
        """Получение статуса сети"""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.api_url}/api/stats",
                headers=self.headers
            )
            return response.json()

    async def get_network_info(self) -> dict:
        """Получение информации о сети"""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.api_url}/api/info",
                headers=self.headers
            )
            return response.json()

    async def get_processes(self, node_id: Optional[str] = None) -> List[dict]:
        """Получение списка процессов"""
        endpoint = f"{self.api_url}/api/v1/processes"
        if node_id:
            endpoint += f"?node_id={node_id}"

        async with httpx.AsyncClient() as client:
            response = await client.get(endpoint, headers=self.headers)
            return response.json()

    async def execute_command(self, command: str, node_id: Optional[str] = None) -> dict:
        """Выполнение команды"""
        data = {"command": command}
        if node_id:
            data["node_id"] = node_id

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.api_url}/api/v1/execute",
                json=data,
                headers=self.headers
            )
            return response.json()

    async def submit_task(self, task_type: str, task_data: dict,
                          target_node: Optional[str] = None) -> dict:
        """Отправка задачи"""
        data = {
            "type": task_type,
            "data": task_data,
            "target_node": target_node
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.api_url}/api/v1/tasks",
                json=data,
                headers=self.headers
            )
            return response.json()


async def update_network_status():
    """Обновление статуса сети"""
    if st.session_state.api_client:
        try:
            status = await st.session_state.api_client.get_status()
            network_info = await st.session_state.api_client.get_network_info()

            st.session_state.network_status = {
                "status": status,
                "info": network_info,
                "timestamp": time.time()
            }
            st.session_state.last_update = datetime.now()

            # Сохранение истории метрик
            if len(st.session_state.metrics_history) > 100:
                st.session_state.metrics_history.pop(0)

            st.session_state.metrics_history.append({
                "timestamp": time.time(),
                "cpu": status["system"]["cpu_percent"],
                "memory": status["system"]["memory"]["percent"],
                "disk": status["system"]["disk"]["percent"],
                "peers": status["p2p"]["peers"]
            })

        except Exception as e:
            st.error(f"Failed to update status: {e}")


def render_sidebar():
    """Отрисовка боковой панели"""
    with st.sidebar:
        st.title("🌐 P2P Admin System")

        # Подключение к API
        st.subheader("API Connection")

        api_url = st.text_input("API URL", value=settings.api_url)
        api_token = st.text_input("API Token", type="password", value=settings.api_token)

        if st.button("Connect"):
            st.session_state.api_client = P2PAdminClient(api_url, api_token)
            st.success("Connected to API")
            st.rerun()

        # Статус подключения
        if st.session_state.api_client:
            st.success("✅ Connected")

            if st.session_state.last_update:
                st.caption(f"Last update: {st.session_state.last_update.strftime('%H:%M:%S')}")

            # Кнопка обновления
            if st.button("🔄 Refresh"):
                asyncio.run(update_network_status())
                st.rerun()
        else:
            st.error("❌ Not connected")

        # Выбор узла
        if st.session_state.network_status:
            st.subheader("Select Node")

            info = st.session_state.network_status["info"]
            peers = st.session_state.network_status["status"]["p2p"]["peers"]

            nodes = ["Current Node"] + [f"Peer {i + 1}" for i in range(peers)]
            selected = st.selectbox("Node", nodes)

            if selected != st.session_state.selected_node:
                st.session_state.selected_node = selected
                st.rerun()

        # Настройки отображения
        st.subheader("Display Settings")

        auto_refresh = st.checkbox("Auto Refresh", value=True)
        refresh_interval = st.slider(
            "Refresh Interval (seconds)",
            min_value=1,
            max_value=60,
            value=settings.status_update_interval
        )

        if auto_refresh:
            st.caption(f"Auto-refreshing every {refresh_interval}s")


def render_overview():
    """Отрисовка обзорной страницы"""
    st.title("System Overview")

    if not st.session_state.network_status:
        st.warning("No data available. Please connect to API.")
        return

    status = st.session_state.network_status["status"]
    info = st.session_state.network_status["info"]

    # Метрики в колонках
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.metric(
            "CPU Usage",
            f"{status['system']['cpu_percent']:.1f}%",
            delta=None
        )

    with col2:
        st.metric(
            "Memory Usage",
            f"{status['system']['memory']['percent']:.1f}%",
            delta=None
        )

    with col3:
        st.metric(
            "Disk Usage",
            f"{status['system']['disk']['percent']:.1f}%",
            delta=None
        )

    with col4:
        st.metric(
            "Connected Peers",
            status['p2p']['peers'],
            delta=None
        )

    # Информация о узле
    st.subheader("Node Information")

    col1, col2 = st.columns(2)

    with col1:
        st.info(f"**Node ID:** {info['node_id'][:16]}...")
        st.info(f"**Version:** {info['version']}")
        st.info(f"**Services:** {len(info['services'])}")

    with col2:
        st.info(f"**Active Tasks:** {status['p2p']['active_tasks']}")
        st.info(f"**Completed Tasks:** {status['p2p']['completed_tasks']}")
        st.info(f"**Failed Tasks:** {status['p2p']['failed_tasks']}")

    # График метрик
    if st.session_state.metrics_history:
        st.subheader("System Metrics")

        df = pd.DataFrame(st.session_state.metrics_history)
        df['time'] = pd.to_datetime(df['timestamp'], unit='s')

        # CPU и Memory график
        fig = go.Figure()

        fig.add_trace(go.Scatter(
            x=df['time'],
            y=df['cpu'],
            mode='lines',
            name='CPU %',
            line=dict(color='blue')
        ))

        fig.add_trace(go.Scatter(
            x=df['time'],
            y=df['memory'],
            mode='lines',
            name='Memory %',
            line=dict(color='red')
        ))

        fig.update_layout(
            title="CPU and Memory Usage",
            xaxis_title="Time",
            yaxis_title="Usage %",
            height=400
        )

        st.plotly_chart(fig, use_container_width=True)

        # Peers график
        fig2 = go.Figure()

        fig2.add_trace(go.Scatter(
            x=df['time'],
            y=df['peers'],
            mode='lines+markers',
            name='Connected Peers',
            line=dict(color='green')
        ))

        fig2.update_layout(
            title="Network Connections",
            xaxis_title="Time",
            yaxis_title="Peer Count",
            height=300
        )

        st.plotly_chart(fig2, use_container_width=True)


def render_processes():
    """Отрисовка страницы процессов"""
    st.title("Process Management")

    if not st.session_state.api_client:
        st.warning("Please connect to API first.")
        return

    # Получение списка процессов
    try:
        processes = asyncio.run(st.session_state.api_client.get_processes())

        if processes:
            # Таблица процессов
            df = pd.DataFrame(processes)

            # Фильтры
            col1, col2, col3 = st.columns(3)

            with col1:
                name_filter = st.text_input("Filter by name")

            with col2:
                status_filter = st.selectbox(
                    "Status",
                    ["All", "running", "sleeping", "stopped"]
                )

            with col3:
                managed_only = st.checkbox("Managed processes only")

            # Применение фильтров
            if name_filter:
                df = df[df['name'].str.contains(name_filter, case=False)]

            if status_filter != "All":
                df = df[df['status'] == status_filter]

            if managed_only:
                df = df[df['is_managed'] == True]

            # Отображение таблицы
            st.dataframe(
                df[['pid', 'name', 'status', 'cpu_percent',
                    'memory_percent', 'is_managed']],
                use_container_width=True
            )

            # Управление процессами
            st.subheader("Process Control")

            col1, col2 = st.columns(2)

            with col1:
                process_name = st.text_input("Process Name")
                command = st.text_area("Command", height=100)

            with col2:
                cwd = st.text_input("Working Directory")
                restart_policy = st.selectbox(
                    "Restart Policy",
                    ["none", "on-failure", "always"]
                )

            col1, col2, col3 = st.columns(3)

            with col1:
                if st.button("▶️ Start Process"):
                    # Запуск процесса
                    st.info("Starting process...")

            with col2:
                if st.button("⏹️ Stop Process"):
                    # Остановка процесса
                    st.info("Stopping process...")

            with col3:
                if st.button("🔄 Restart Process"):
                    # Перезапуск процесса
                    st.info("Restarting process...")

        else:
            st.info("No processes found.")

    except Exception as e:
        st.error(f"Failed to load processes: {e}")


def render_tasks():
    """Отрисовка страницы задач"""
    st.title("Task Management")

    if not st.session_state.network_status:
        st.warning("No data available. Please connect to API.")
        return

    # Создание новой задачи
    st.subheader("Submit New Task")

    col1, col2 = st.columns(2)

    with col1:
        task_type = st.selectbox(
            "Task Type",
            ["execute_command", "collect_logs", "update_config", "custom"]
        )

        if task_type == "custom":
            task_type = st.text_input("Custom Task Type")

    with col2:
        target_node = st.selectbox(
            "Target Node",
            ["Any Node", "Current Node"] +
            [f"Peer {i + 1}" for i in range(st.session_state.network_status["status"]["p2p"]["peers"])]
        )

    # Параметры задачи
    st.subheader("Task Parameters")

    task_data = {}

    if task_type == "execute_command":
        task_data["command"] = st.text_input("Command")
        task_data["args"] = st.text_input("Arguments").split()

    elif task_type == "collect_logs":
        task_data["path"] = st.text_input("Log Path")
        task_data["lines"] = st.number_input("Number of Lines", value=100)

    elif task_type == "update_config":
        task_data["path"] = st.text_input("Config Path")
        config_json = st.text_area("Configuration (JSON)", height=200)
        try:
            task_data["data"] = json.loads(config_json) if config_json else {}
        except json.JSONDecodeError:
            st.error("Invalid JSON")

    else:
        # Произвольные параметры
        params_json = st.text_area("Task Parameters (JSON)", height=200)
        try:
            task_data = json.loads(params_json) if params_json else {}
        except json.JSONDecodeError:
            st.error("Invalid JSON")

    if st.button("Submit Task"):
        if st.session_state.api_client and task_type:
            try:
                result = asyncio.run(
                    st.session_state.api_client.submit_task(
                        task_type,
                        task_data,
                        None if target_node == "Any Node" else target_node
                    )
                )
                st.success(f"Task submitted: {result.get('task_id')}")
            except Exception as e:
                st.error(f"Failed to submit task: {e}")

    # Список активных задач
    st.subheader("Active Tasks")

    # Здесь должен быть код для отображения активных задач
    st.info("Task list would be displayed here")


def render_monitoring():
    """Отрисовка страницы мониторинга"""
    st.title("System Monitoring")

    if not st.session_state.network_status:
        st.warning("No data available. Please connect to API.")
        return

    # Вкладки для различных метрик
    tab1, tab2, tab3, tab4 = st.tabs(["System", "Network", "Disk", "Alerts"])

    with tab1:
        render_system_metrics()

    with tab2:
        render_network_metrics()

    with tab3:
        render_disk_metrics()

    with tab4:
        render_alerts()


def render_system_metrics():
    """Отрисовка системных метрик"""
    status = st.session_state.network_status["status"]

    col1, col2 = st.columns(2)

    with col1:
        # CPU метрики
        st.subheader("CPU")
        cpu_gauge = go.Figure(go.Indicator(
            mode="gauge+number+delta",
            value=status["system"]["cpu_percent"],
            domain={'x': [0, 1], 'y': [0, 1]},
            title={'text': "CPU Usage %"},
            gauge={
                'axis': {'range': [None, 100]},
                'bar': {'color': "darkblue"},
                'steps': [
                    {'range': [0, 50], 'color': "lightgray"},
                    {'range': [50, 80], 'color': "yellow"},
                    {'range': [80, 100], 'color': "red"}
                ],
                'threshold': {
                    'line': {'color': "red", 'width': 4},
                    'thickness': 0.75,
                    'value': 90
                }
            }
        ))
        cpu_gauge.update_layout(height=300)
        st.plotly_chart(cpu_gauge, use_container_width=True)

    with col2:
        # Memory метрики
        st.subheader("Memory")
        memory = status["system"]["memory"]

        memory_pie = go.Figure(data=[go.Pie(
            labels=['Used', 'Available'],
            values=[memory['used'], memory['available']],
            hole=.3
        )])
        memory_pie.update_layout(
            title="Memory Usage",
            height=300
        )
        st.plotly_chart(memory_pie, use_container_width=True)

    # Детальная информация
    st.subheader("Detailed Information")

    col1, col2, col3 = st.columns(3)

    with col1:
        st.metric("Total Memory", f"{memory['total'] / (1024 ** 3):.1f} GB")
        st.metric("Used Memory", f"{memory['used'] / (1024 ** 3):.1f} GB")

    with col2:
        st.metric("Available Memory", f"{memory['available'] / (1024 ** 3):.1f} GB")
        st.metric("Memory Percent", f"{memory['percent']:.1f}%")

    with col3:
        st.metric("Swap Total", f"{memory['swap_total'] / (1024 ** 3):.1f} GB")
        st.metric("Swap Used", f"{memory['swap_percent']:.1f}%")


def render_network_metrics():
    """Отрисовка сетевых метрик"""
    network = st.session_state.network_status["status"]["network"]

    # Трафик
    st.subheader("Network Traffic")

    col1, col2 = st.columns(2)

    with col1:
        st.metric(
            "Bytes Sent",
            f"{network['bytes_sent'] / (1024 ** 3):.2f} GB",
            delta=None
        )
        st.metric(
            "Packets Sent",
            f"{network['packets_sent']:,}",
            delta=None
        )

    with col2:
        st.metric(
            "Bytes Received",
            f"{network['bytes_recv'] / (1024 ** 3):.2f} GB",
            delta=None
        )
        st.metric(
            "Packets Received",
            f"{network['packets_recv']:,}",
            delta=None
        )

    # Ошибки
    st.subheader("Network Errors")

    error_data = {
        'Type': ['Input Errors', 'Output Errors', 'Input Drops', 'Output Drops'],
        'Count': [network['errin'], network['errout'], network['dropin'], network['dropout']]
    }

    error_df = pd.DataFrame(error_data)

    fig = px.bar(error_df, x='Type', y='Count', title="Network Errors and Drops")
    st.plotly_chart(fig, use_container_width=True)

    # Соединения
    st.subheader("Active Connections")
    st.metric("Total Connections", network['connections'])


def render_disk_metrics():
    """Отрисовка дисковых метрик"""
    disk = st.session_state.network_status["status"]["system"]["disk"]

    # Использование диска
    st.subheader("Disk Usage")

    disk_gauge = go.Figure(go.Indicator(
        mode="gauge+number",
        value=disk["percent"],
        domain={'x': [0, 1], 'y': [0, 1]},
        title={'text': "Disk Usage %"},
        gauge={
            'axis': {'range': [None, 100]},
            'bar': {'color': "darkgreen"},
            'steps': [
                {'range': [0, 70], 'color': "lightgray"},
                {'range': [70, 90], 'color': "yellow"},
                {'range': [90, 100], 'color': "red"}
            ]
        }
    ))
    disk_gauge.update_layout(height=300)
    st.plotly_chart(disk_gauge, use_container_width=True)

    # Детали
    col1, col2, col3 = st.columns(3)

    with col1:
        st.metric("Total Space", f"{disk['total'] / (1024 ** 3):.1f} GB")

    with col2:
        st.metric("Used Space", f"{disk['used'] / (1024 ** 3):.1f} GB")

    with col3:
        st.metric("Free Space", f"{disk['free'] / (1024 ** 3):.1f} GB")


def render_alerts():
    """Отрисовка алертов"""
    st.subheader("System Alerts")

    # Здесь должен быть код для отображения алертов
    st.info("No active alerts")


def render_command_execution():
    """Отрисовка страницы выполнения команд"""
    st.title("Command Execution")

    if not st.session_state.api_client:
        st.warning("Please connect to API first.")
        return

    # Ввод команды
    command = st.text_area("Command", height=100)

    col1, col2 = st.columns(2)

    with col1:
        target_node = st.selectbox(
            "Execute on",
            ["Current Node"] +
            ([f"Peer {i + 1}" for i in range(st.session_state.network_status["status"]["p2p"]["peers"])]
             if st.session_state.network_status else [])
        )

    with col2:
        timeout = st.number_input("Timeout (seconds)", value=30, min_value=1)

    if st.button("Execute"):
        if command:
            with st.spinner("Executing command..."):
                try:
                    result = asyncio.run(
                        st.session_state.api_client.execute_command(
                            command,
                            None if target_node == "Current Node" else target_node
                        )
                    )

                    if result.get("status") == "success":
                        st.success("Command executed successfully")

                        # Вывод результатов
                        if result.get("stdout"):
                            st.subheader("Output")
                            st.code(result["stdout"])

                        if result.get("stderr"):
                            st.subheader("Errors")
                            st.code(result["stderr"], language="bash")

                        st.info(f"Return code: {result.get('returncode', 'N/A')}")
                    else:
                        st.error(f"Command failed: {result.get('message', 'Unknown error')}")

                except Exception as e:
                    st.error(f"Failed to execute command: {e}")


def main():
    """Главная функция"""

    # Отрисовка боковой панели
    render_sidebar()

    # Основное меню
    if st.session_state.api_client:
        pages = {
            "📊 Overview": render_overview,
            "🔧 Processes": render_processes,
            "📋 Tasks": render_tasks,
            "📈 Monitoring": render_monitoring,
            "💻 Execute Command": render_command_execution
        }

        # Навигация
        selected_page = st.sidebar.selectbox("Navigation", list(pages.keys()))

        # Отрисовка выбранной страницы
        pages[selected_page]()

        # Автообновление
        # if st.sidebar.checkbox("Auto Refresh", value=True, key=time.time()):
        #     time.sleep(settings.status_update_interval)
        #     st.rerun()

    else:
        # Страница приветствия
        st.title("Welcome to P2P Admin System")
        st.info("Please connect to the API using the sidebar.")

        st.markdown("""
        ### Features:
        - 🌐 **Distributed P2P Architecture** - Manage services across multiple nodes
        - 🔧 **Process Management** - Start, stop, and monitor processes
        - 📋 **Task Distribution** - Submit and track tasks across the network
        - 📈 **Real-time Monitoring** - System metrics and alerts
        - 💻 **Remote Execution** - Execute commands on any node
        - 🔒 **Secure Communication** - JWT authentication and encrypted connections
        """)


if __name__ == "__main__":
    main()