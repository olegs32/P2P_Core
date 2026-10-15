# services/netinfo/web_ui.py — веб-интерфейс сервиса диагностики сети
# Контракт: функция render(rpc) вызывается streamlit для рендеринга вкладки сервиса
import logging

try:
    import streamlit as st
except ImportError:
    # Если мы в режиме Node без UI, streamlit не доступен
    st = None
import pandas as pd

try:
    from streamlit_agraph import agraph, Config, Edge, Node
except ImportError:
    # компонент может отсутствовать (headless/старое окружение) — фолбэк на таблицу
    agraph = None

logging.getLogger("streamlit.runtime.scriptrunner_utils.script_run_context").setLevel(logging.ERROR)
logging.getLogger("streamlit.runtime.state.session_state_proxy").setLevel(logging.ERROR)

# цвета карты
_MAP_NODE_COLORS = {
    'connected':   '#2ecc71',   # зелёный — живой mesh-узел
    'known':       '#f1c40f',   # жёлтый — известен через gossip
    'unreachable': '#e74c3c',   # красный — считался недоступным
}
_ROOT_COLOR = '#3498db'         # синий — узел, к которому подключена панель
_CLIENT_COLOR = '#d5dbdb'       # светло-серый — служебный WS-клиент (видно на тёмном фоне; + белая обводка)
_EDGE_OK = '#2ecc71'
_EDGE_BAD = '#e74c3c'
_EDGE_GOSSIP = '#f1c40f'

# общие стили узлов для тёмной темы: белый шрифт с тёмной обводкой + белая рамка узла
_NODE_FONT = {'color': '#ffffff', 'strokeWidth': 4, 'strokeColor': '#000000', 'size': 14}
_NODE_BORDER_WIDTH = 2
_NODE_BORDER_COLOR = '#ffffff'


def _node_color_kwargs(color: str) -> dict:
    """Возвращает kwargs для Node чтобы узел был видим на тёмном фоне."""
    return {
        'color': {
            'background': color,
            'border': _NODE_BORDER_COLOR,
            'highlight': {'background': color, 'border': _NODE_BORDER_COLOR},
            'hover': {'background': color, 'border': _NODE_BORDER_COLOR},
        },
        'borderWidth': _NODE_BORDER_WIDTH,
        'font': _NODE_FONT,
    }


def render(rpc):
    if st is None:
        return
    tab1, tab2, tab3, tab4 = st.tabs(["Соседи", "Узлы", "Поиск сервиса", "🗺 Карта сети"])

    # ------------------------------------------------------------------ #
    #  Tab 1: Таблица соседей
    # ------------------------------------------------------------------ #
    with tab1:
        if st.button("Обновить", key="refresh_neighbors"):
            st.session_state.pop('netinfo_neighbors', None)

        if 'netinfo_neighbors' not in st.session_state:
            try:
                st.session_state.netinfo_neighbors = rpc.call('netinfo', 'neighbors')
            except Exception as e:
                st.error(f"Ошибка: {e}")
                return

        data = st.session_state.netinfo_neighbors
        own = data.get('own', '?')
        st.caption(f"Узел: `{own}`")

        connected = data.get('connected', [])
        known = data.get('known', [])

        if connected:
            st.subheader(f"Подключены ({len(connected)})")
            rows = []
            for n in connected:
                rows.append({
                    'Node ID': n.get('node_id', '?'),
                    'Host': n.get('host', '?'),
                    'Port': n.get('port', '?'),
                    'Via': n.get('via', '-'),
                    'Services': ', '.join(n.get('services', [])) or '-',
                })
            st.dataframe(pd.DataFrame(rows), width='stretch', hide_index=True)
        else:
            st.info("Нет подключённых соседей")

        if known:
            st.subheader(f"Известны ({len(known)})")
            rows = []
            for n in known:
                rows.append({
                    'Node ID': n.get('node_id', '?'),
                    'Host': n.get('host', '?'),
                    'Port': n.get('port', '?'),
                    'Via': n.get('via', '-'),
                    'Services': ', '.join(n.get('services', [])) or '-',
                })
            st.dataframe(pd.DataFrame(rows), width='stretch', hide_index=True)

    # ------------------------------------------------------------------ #
    #  Tab 2: Активные узлы
    # ------------------------------------------------------------------ #
    with tab2:
        if st.button("Обновить", key="refresh_nodes"):
            st.session_state.pop('netinfo_nodes', None)

        if 'netinfo_nodes' not in st.session_state:
            try:
                st.session_state.netinfo_nodes = rpc.call('netinfo', 'nodes')
            except Exception as e:
                st.error(f"Ошибка: {e}")
                return

        nodes = st.session_state.netinfo_nodes
        if nodes:
            for node_id in nodes:
                st.success(f"● {node_id}")
        else:
            st.info("Нет активных узлов")

    # ------------------------------------------------------------------ #
    #  Tab 3: Поиск сервиса
    # ------------------------------------------------------------------ #
    with tab3:
        service_name = st.text_input("Имя сервиса для поиска", key="search_svc")
        if st.button("Найти", key="find_svc") and service_name:
            try:
                result = rpc.call('netinfo', 'find_service', {'service': service_name})
                st.session_state['netinfo_search_result'] = result
            except Exception as e:
                st.error(f"Ошибка: {e}")

        result = st.session_state.get('netinfo_search_result')
        if result is not None:
            if result:
                st.subheader(f"Сервис `{service_name}` найден на узлах:")
                rows = []
                for n in result:
                    rows.append({
                        'Node ID': n.get('node_id', '?'),
                        'Status': n.get('status', '?'),
                        'Via': n.get('via', '-'),
                    })
                st.dataframe(pd.DataFrame(rows), width='stretch', hide_index=True)
            else:
                st.warning(f"Сервис `{service_name}` не найден ни на одном узле")

    # ------------------------------------------------------------------ #
    #  Tab 4: Карта сети
    # ------------------------------------------------------------------ #
    with tab4:
        _render_network_map(rpc)


# ------------------------------------------------------------------ #
#  Карта сети (направленные физические WS-связи)
# ------------------------------------------------------------------ #

def _render_network_map(rpc):
    st.subheader("Карта сети")
    st.caption(
        "Направленные **физические WS-связи** всей сети (не логические "
        "маршруты): стрелка `A → B` означает «A держит outbound WS к B». "
        "Источник — рекурсивный BFS через RPC `netinfo.topology()`.  \n"
        f"🟢 подтверждено обоими концами · 🔴 half-open (видно с одной "
        f"стороны — возможен зомби-сокет) · 🟡 gossip-пунктир (известен "
        f"через via, направления нет) · ⚪ клиент панели · "
        f"🔵 узел, к которому подключена панель"
    )

    auto = st.toggle("Автообновление (5 сек)", value=False, key="net_map_auto")
    st.toggle(
        "Жёлтые gossip-связи",
        value=True,
        key="net_map_show_gossip",
        help="Пунктирные линии «известен через via» — не физические связи, "
             "а записи из gossip-таблицы. Выключи, чтобы осталась только физика.",
    )

    if auto:
        @st.fragment(run_every="5s")
        def map_view():
            _draw_network_map(rpc)
    else:
        @st.fragment
        def map_view():
            _draw_network_map(rpc)

    map_view()


def _draw_network_map(rpc):
    if st.button("🔄 Обновить карту", key="net_map_refresh"):
        pass  # клик перезапускает fragment — данные ниже запросятся заново

    try:
        with st.spinner("Опрос узлов сети..."):
            topo = rpc.call('netinfo', 'topology', {}, timeout=30)
    except Exception as e:
        st.error(f"Ошибка получения карты: {e}")
        return

    if not isinstance(topo, dict) or not topo.get('ok'):
        st.error(f"Узел ответил ошибкой: {(topo or {}).get('error', 'нет данных')}")
        return

    root = topo.get('root', '?')
    nodes_data = topo.get('nodes', [])
    clients = topo.get('clients', [])
    edges_data = topo.get('edges', [])
    errors = topo.get('errors', {}) or {}
    cache_age = topo.get('cache_age_sec')

    # метрики (half-open считается без клиентских рёбер — они всегда
    # неподтверждённые по определению)
    client_ids = {c['node_id'] for c in clients}
    half_open = sum(
        1 for e in edges_data if not e.get('verified')
        and e.get('src') not in client_ids and e.get('dst') not in client_ids)
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Узлы", len(nodes_data))
    m2.metric("Связи", len(edges_data))
    m3.metric("Half-open", half_open)
    m4.metric("Клиенты", len(clients))
    age = f"{cache_age:.0f}с" if cache_age is not None else "0с"
    m5.metric("Кэш", age, help="Возраст снимка на опрашиваемом узле")

    all_records = nodes_data + clients
    by_id = {n['node_id']: n for n in all_records}

    if agraph is None:
        st.info("Компонент streamlit-agraph недоступен — карта таблицей")
        _edges_table(edges_data, {c['node_id'] for c in clients})
        return

    a_nodes, a_edges = [], []

    # узлы mesh
    for n in nodes_data:
        nid = n['node_id']
        if nid == root:
            color, size = _ROOT_COLOR, 34
        else:
            color = _MAP_NODE_COLORS.get(n.get('status'), _CLIENT_COLOR)
            size = 26
        tooltip = (f"{nid}\n{n.get('host', '?')}:{n.get('port', '?')} · "
                   f"{n.get('status', '?')}")
        a_nodes.append(Node(
            id=nid, label=nid, size=size,
            shape='dot', title=tooltip,
            **_node_color_kwargs(color),
        ))

    # клиенты панели — светлые с белой обводкой (видно на тёмном фоне)
    for c in clients:
        cid = c['node_id']
        a_nodes.append(Node(
            id=cid, label=cid, size=18,
            shape='dot', title=f"{cid} (клиент панели)",
            **_node_color_kwargs(_CLIENT_COLOR),
        ))

    # физические рёбра: verified — зелёное, half-open — красное,
    # рёбра с участием клиента панели — серые (клиент не отвечает на
    # topology, поэтому они всегда «неподтверждены» — это норма)
    # Убраны текстовые плашки на рёбрах (label='') и отключен renderLabel
    # чтобы gossip и half-open не перекрывали друг друга.
    for e in edges_data:
        rep = ', '.join(e.get('reported_by', []))
        if e['src'] in client_ids or e['dst'] in client_ids:
            a_edges.append(Edge(
                source=e['src'], target=e['dst'],
                color=_CLIENT_COLOR, width=1.5, label='',
                title=f"{e['src']} → {e['dst']} · клиент панели",
            ))
            continue
        ok = bool(e.get('verified'))
        if ok:
            a_edges.append(Edge(
                source=e['src'],
                target=e['dst'],
                color=_EDGE_OK,
                width=2.5,
                label='',
                title=f"{e['src']} → {e['dst']} · "
                      f"подтверждено обоими"
                      f" (докладчики: {rep})",
            ))
        else:
            # half-open — пунктир + скругление чтобы не сливалось с gossip-пунктиром
            a_edges.append(Edge(
                source=e['src'],
                target=e['dst'],
                color=_EDGE_BAD,
                width=1.5,
                dashes=True,
                smooth={'type': 'curvedCW', 'roundness': 0.15},
                label='',
                title=f"{e['src']} → {e['dst']} · "
                      f"half-open"
                      f" (докладчики: {rep})",
            ))

    # gossip-пунктир для known-узлов (не физическая связь!) — с противоположным скруглением
    # выключатель — в _render_network_map перед картой (ключ net_map_show_gossip)
    show_gossip = st.session_state.get("net_map_show_gossip", True)
    if show_gossip:
        for n in nodes_data:
            via = n.get('via')
            if n.get('status') != 'known' or via not in by_id:
                continue
            a_edges.append(Edge(
                source=via,
                target=n['node_id'],
                color=_EDGE_GOSSIP,
                width=1,
                dashes=True,
                smooth={'type': 'curvedCCW', 'roundness': 0.15},
                label='',
                title=f"{n['node_id']} известен через {via} (gossip)",
            ))

    # Настройка физики под размер сети: дефолтный barnesHut vis-network
    # (centralGravity ~0.3, springLength ~95, avoidOverlap 0) при большом
    # числе нод стягивает всё в центр с наслоением. Ослабляем центральную
    # гравитацию, усиливаем отталкивание, удлиняем пружины и включаем
    # avoidOverlap; стабилизацию масштабируем от числа узлов. Высоту
    # холста тоже растим с числом узлов, чтобы было куда разложиться.
    n_total = len(nodes_data) + len(clients)
    crowded = n_total > 12
    map_height = 480 if n_total <= 10 else min(900, 480 + (n_total - 10) * 14)
    physics_opts = {
        "enabled": True,
        "solver": "barnesHut",
        "barnesHut": {
            # чем больше сеть — тем сильнее расталкивание и слабее стягивание в центр
            "gravitationalConstant": -8000 if not crowded else -22000,
            "centralGravity": 0.25 if not crowded else 0.08,
            "springLength": 180 if not crowded else 260,
            "springConstant": 0.04,
            "damping": 0.09,
            "avoidOverlap": 0 if not crowded else 1,
        },
        "stabilization": {
            "enabled": True,
            "iterations": max(400, n_total * 40),
            "fit": True,
        },
        "minVelocity": 0.75,
        "maxVelocity": 100,
        "timestep": 0.5,
    }

    config = Config(
        height=map_height,
        width=900,
        directed=True,
        physics=True,
        hierarchical=False,
        nodeHighlightBehavior=True,
        highlightColor='#F7A7A6',
        collapsible=False,
        node={'labelProperty': 'label'},
        link={'labelProperty': 'label', 'renderLabel': False},
        interaction={'hover': True, 'zoomView': True, 'dragView': True},
        layout={'hierarchical': {'enabled': False}, 'improvedLayout': True},
    )
    # Config.__init__ принимает physics только как bool-флаг (кладёт его в
    # physics.enabled), поэтому готовый словарь подменяем после конструктора —
    # иначе dict попал бы в enabled и физика сломалась бы.
    config.physics = physics_opts
    # Ширина — на всю ширину рамки/экрана: Config форматирует width как
    # f"{width}px", поэтому фиксированные 900px оставляли пустоты справа
    # (широкий layout) или обрезались (узкий). vis-network/react-graph-vis
    # принимает и проценты — подменяем после конструктора, до agraph().
    config.width = "100%"

    # Растягиваем iframe кастомного компонента и canvas на всю ширину
    # колонки/рамки; селектор stVerticalBlockBorderWrapper сужен до карты,
    # чтобы не задевать остальные border-контейнеры панели.
    st.markdown(
        """<style>
        /* пунктирная тонкая рамка вокруг карты сети */
        div[data-testid="stVerticalBlockBorderWrapper"]:has(div[data-testid="stCustomComponentV1"]) {
            border: 1px dashed #888 !important;
            border-radius: 8px !important;
        }
        /* карта на всю ширину рамки: компонент + iframe + canvas */
        div[data-testid="stCustomComponentV1"],
        div[data-testid="stCustomComponentV1"] > iframe,
        div[data-testid="stCustomComponentV1"] div.vis-network,
        div[data-testid="stCustomComponentV1"] canvas {
            width: 100% !important;
            max-width: 100% !important;
        }
        </style>""",
        unsafe_allow_html=True,
    )
    with st.container(border=True):
        selected = agraph(nodes=a_nodes, edges=a_edges, config=config)

    # клик по узлу — карточка с деталями из снимка топологии
    if selected:
        info = by_id.get(selected)
        if info:
            st.markdown(f"**Узел `{selected}`**")
            st.json(info)


def _edges_table(edges_data, client_ids=frozenset()):
    rows = []
    for e in edges_data:
        if e.get('src') in client_ids or e.get('dst') in client_ids:
            status = '⚪ клиент'
        elif e.get('verified'):
            status = '✅ подтверждено'
        else:
            status = '⚠️ half-open'
        rows.append({
            'Откуда': e.get('src', '?'),
            'Куда': e.get('dst', '?'),
            'Статус': status,
            'Докладчики': ', '.join(e.get('reported_by', [])),
        })
    if rows:
        st.dataframe(pd.DataFrame(rows), width='stretch', hide_index=True)
    else:
        st.info("Нет связей")
