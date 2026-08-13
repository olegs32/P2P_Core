# services/webpanel/_streamlit_app.py — Streamlit entry point
# Единая панель управления: sidebar навигация + выбор ноды + динамический рендер

import os
import sys
from pathlib import Path

import streamlit as st

# ------------------------------------------------------------------ #
#  sys.path — чтобы импорты из корня проекта работали в subprocess
# ------------------------------------------------------------------ #
PROJECT_ROOT = os.environ.get('P2P_PROJECT_ROOT', str(Path(__file__).parent.parent.parent))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from services.webpanel.rpc_client import NodeRPC
from services.webpanel.service_meta import SERVICE_META, GROUP_ORDER

# ------------------------------------------------------------------ #
#  Директория сервисов
# ------------------------------------------------------------------ #
SERVICES_DIR = Path(__file__).parent.parent


# ------------------------------------------------------------------ #
#  NodeRPC — singleton через session_state
# ------------------------------------------------------------------ #
def get_rpc() -> NodeRPC:
    rpc_exists = 'rpc' in st.session_state
    if rpc_exists and (st.session_state.rpc.connected or st.session_state.rpc.reconnecting):
        return st.session_state.rpc
    host = '127.0.0.1'
    port = int(os.environ.get('P2P_WS_PORT', 9000))
    target = os.environ.get('P2P_NODE_ID', 'Node0')
    node_id = f"webpanel_{target}"
    st.session_state.rpc = NodeRPC(
        host=host, port=port,
        node_id=node_id, target_node=target,
    )
    return st.session_state.rpc


# ------------------------------------------------------------------ #
#  Wrapper RPC с поддержкой dst — пробрасывает выбранную ноду
# ------------------------------------------------------------------ #
class RPCProxy:
    """Обёртка над NodeRPC: подставляет dst из session_state."""

    def __init__(self, rpc: NodeRPC):
        self._rpc = rpc

    def call(self, service: str, method: str, data=None, timeout: int = 10):
        dst = st.session_state.get('selected_node')
        local_node = self._rpc.node
        if dst is None or dst == local_node:
            dst = None
        return self._rpc.call(service, method, data, dst=dst, timeout=timeout)

    @property
    def connected(self):
        return self._rpc.connected

    @property
    def local_node(self):
        return self._rpc.node


# ------------------------------------------------------------------ #
#  Page config
# ------------------------------------------------------------------ #
st.set_page_config(
    layout="wide",
    page_title="P2P Node Panel",
    page_icon="⬡",
)

# ------------------------------------------------------------------ #
#  Sidebar
# ------------------------------------------------------------------ #
with st.sidebar:
    try:
        rpc = get_rpc()
        local_node = rpc.node
    except Exception as e:
        st.error(f"Ошибка подключения: {e}")
        st.stop()

    # ---- Выбор узла ----
    try:
        status = rpc.call('webpanel', 'node_status')
        connected_nodes = [n.get('node_id', '?') for n in status.get('connected', [])]
        known_nodes = [n.get('node_id', '?') for n in status.get('known', [])]
        all_nodes = [local_node] + \
                    [n for n in connected_nodes if n != local_node] + \
                    [n for n in known_nodes if n not in connected_nodes and n != local_node]
    except Exception:
        all_nodes = [local_node]

    selected_node = st.selectbox(
        "Узел",
        all_nodes,
        index=0,
        key="selected_node_select",
    )

    # При смене узла — очистить кешированные данные всех сервисов
    prev_node = st.session_state.get('_prev_selected_node')
    st.session_state['_prev_selected_node'] = selected_node

    if prev_node is not None and prev_node != selected_node:
        _PRESERVED = {'rpc', 'current_page', '_prev_selected_node',
                       'selected_node_select'}
        for key in list(st.session_state.keys()):
            if key not in _PRESERVED:
                del st.session_state[key]
        st.rerun()

    st.session_state['selected_node'] = selected_node

    if selected_node == local_node:
        st.caption("📍 Локальный")
    else:
        st.caption(f"🌐 Удалённый → через {local_node}")

    st.divider()

    # ---- Сервисы с UI ----
    try:
        if selected_node == local_node:
            ui_services = rpc.call('webpanel', 'discover_ui_services')
        else:
            svc_list = rpc.call('netinfo', 'services', dst=selected_node)
            ui_services = list(svc_list or [])
    except Exception as e:
        st.warning(f"Сервисы недоступны: {e}")
        ui_services = []

    # ---- Текущая страница ----
    if 'current_page' not in st.session_state:
        st.session_state.current_page = 'home'

    # ---- Главная — кнопка ----
    is_home = st.session_state.current_page == 'home'
    if st.button("🏠  Главная", use_container_width=True, type="primary" if is_home else "secondary"):
        st.session_state.current_page = 'home'
        st.rerun()

    # ---- Группировка сервисов ----
    groups: dict[str, list[tuple[str, str, str]]] = {}  # group → [(name, icon, desc)]
    ungrouped: list[tuple[str, str, str]] = []

    for svc_name in sorted(ui_services):
        meta = SERVICE_META.get(svc_name)
        if meta:
            icon, group, desc = meta
            groups.setdefault(group, []).append((svc_name, icon, desc))
        else:
            ungrouped.append((svc_name, '📦', svc_name))

    # Рендер групп
    for group_name in GROUP_ORDER:
        items = groups.get(group_name, [])
        if not items:
            continue
        st.markdown(f"**{group_name}**")
        for svc_name, icon, desc in items:
            is_active = st.session_state.current_page == svc_name
            btn_type = "primary" if is_active else "secondary"
            if st.button(f"{icon}  {svc_name}", use_container_width=True, type=btn_type,
                         key=f"nav_{svc_name}"):
                st.session_state.current_page = svc_name
                st.rerun()

    # Без группы
    if ungrouped:
        if groups:
            st.markdown("**Другие**")
        for svc_name, icon, desc in ungrouped:
            is_active = st.session_state.current_page == svc_name
            btn_type = "primary" if is_active else "secondary"
            if st.button(f"{icon}  {svc_name}", use_container_width=True, type=btn_type,
                         key=f"nav_{svc_name}"):
                st.session_state.current_page = svc_name
                st.rerun()

    # ---- Статус (компактно) ----
    st.divider()
    st.caption(f"🌐 {len(all_nodes)} узлов  •  📦 {len(ui_services)} сервисов")

# ------------------------------------------------------------------ #
#  Content — роутинг
# ------------------------------------------------------------------ #
proxy = RPCProxy(rpc)

if st.session_state.current_page == 'home':
    from services.webpanel.views.home import render
    render(proxy)
else:
    from services.webpanel.views.service_view import render
    render(proxy, st.session_state.current_page)
