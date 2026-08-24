# services/system/web_ui.py — веб-интерфейс вкладки «Система»
# Две подвкладки: Управление узлами + Подключение

import json
import logging

try:
    import streamlit as st
except ImportError:
    # Если мы в режиме Node без UI, streamlit не доступен
    st = None
import pandas as pd
logging.getLogger("streamlit.runtime.scriptrunner_utils.script_run_context").setLevel(logging.ERROR)
logging.getLogger("streamlit.runtime.state.session_state_proxy").setLevel(logging.ERROR)


# ------------------------------------------------------------------ #
#  Известные RPC-методы по сервисам (для выпадающих списков)
# ------------------------------------------------------------------ #
KNOWN_METHODS = {
    'netinfo':       ['neighbors', 'nodes', 'services', 'find_service'],
    'certstool':     [
        'get_dashboard_data', 'list_certificates', 'network_certs',
        'install_from_node', 'get_install_history',
        'find_certificates_by_subject', 'export_certificate_pfx',
        'export_certificate_cer', 'delete_certificate',
        'install_pfx_from_base64', 'fix_certificate_link',
    ],
    'system':        ['connect_to_node', 'list_connectors', 'node_detail', 'config_peers', 'ctx_map'],
    'webpanel':      ['node_status', 'discover_ui_services'],
    'compute_full':  ['start_stream', 'compute_ranges', 'compute_squares', 'run_range'],
    'generator':     ['start_stream'],
    'test':          ['echo', 'echo_stream'],
    'spawner':       ['spawn', 'list_generators'],
}


def render(rpc):
    if st is None:
        return
    tab_nodes, tab_connect, tab_ctx = st.tabs(
        ["Управление узлами", "Подключение", "🧭 Контекст (ctx)"]
    )

    with tab_nodes:
        _render_node_panel(rpc)

    with tab_connect:
        _render_connect(rpc)

    with tab_ctx:
        _render_ctx(rpc)


# ------------------------------------------------------------------ #
#  Вкладка 3: карта контекста приложения (self.ctx)
# ------------------------------------------------------------------ #

CTX_ICONS = {
    'NODE':            '🏷️',
    'config':          '🗄️',
    'config_manager':  '🗄️',
    'peers':           '🔗',
    'services':        '📦',
    'certs_index':     '🔐',
    '_modules':        '🧩',
    'network':         '🌐',
    'memory':          '🧠',
    'spawn':           '⚡',
}


def _pick_into_console(service: str, method: str):
    """Клик по методу: отложить подстановку в RPC-консоль и перерисовать."""
    st.session_state['ctx_pick'] = {
        'dst': st.session_state.get('selected_node'),
        'service': service,
        'method': method,
    }
    st.rerun()


def _methods_table(methods: list, rpc_service, widget_key: str):
    """Таблица методов объекта. Если методы доступны по RPC — клик по строке
    подставляет сервис/метод в консоль на вкладке «Управление узлами»."""
    if not methods:
        return
    df = pd.DataFrame([
        {'Метод': m['name'], 'Сигнатура': m['sig']}
        for m in methods
    ])

    if rpc_service:
        st.markdown(f"**Методы ({len(methods)})** — кликните строку для вызова:")
        event = st.dataframe(
            df, use_container_width=True, hide_index=True,
            on_select='rerun', selection_mode='single-row', key=widget_key,
        )
        rows = event.selection.rows
        if rows:
            _pick_into_console(rpc_service, methods[rows[0]]['name'])
    else:
        st.caption(f"Методы ({len(methods)}, внутренние — по сети не вызываются):")
        st.dataframe(df, use_container_width=True, hide_index=True)


def _render_ctx(rpc):
    st.subheader("Карта контекста приложения")
    st.caption(
        "Что лежит в `self.ctx` любого сервиса или модуля и какие методы у него "
        "вызываются. Источник — RPC `system.ctx_map()`; описания атрибутов "
        "заданы в `services/system/service.py` (CTX_ATTR_DOCS). "
        "Клик по строке с методом сервиса подставляет его в RPC-консоль "
        "(вкладка «Управление узлами»)."
    )

    try:
        data = rpc.call('system', 'ctx_map')
    except Exception as e:
        st.error(f"Ошибка получения карты контекста: {e}")
        return

    entries = (data or {}).get('entries', [])
    if not entries:
        st.info("Контекст пуст")
        return

    for entry in entries:
        icon = CTX_ICONS.get(entry.get('name'), '📦')
        header = f"{icon} ctx.{entry['name']} · {entry.get('type', '?')}"
        with st.expander(header):
            doc = entry.get('doc')
            if doc:
                st.caption(doc)

            value = entry.get('value')
            if value is not None:
                st.code(value, language='python')

            registry = entry.get('registry')
            if registry:
                rpc_rows, gen_rows = [], []
                for svc_name, info in sorted(registry.items()):
                    for m in info['methods']:
                        rpc_rows.append({'Сервис': svc_name, 'Метод': m})
                    for g in info['generators']:
                        gen_rows.append(f"{svc_name}.{g}")

                if rpc_rows:
                    st.markdown("**RPC-методы сервисов** — кликните строку для вызова:")
                    event = st.dataframe(
                        pd.DataFrame(rpc_rows),
                        use_container_width=True, hide_index=True,
                        on_select='rerun', selection_mode='single-row',
                        key=f"sel_reg_{entry['name']}",
                    )
                    rows = event.selection.rows
                    if rows:
                        row = rpc_rows[rows[0]]
                        _pick_into_console(row['Сервис'], row['Метод'])

                if gen_rows:
                    st.caption("@generator (вызываются через Spawner): "
                               + ", ".join(f"`{g}`" for g in gen_rows))

            _methods_table(entry.get('methods', []),
                           entry.get('rpc_service'),
                           f"sel_{entry['name']}")

            attrs = entry.get('attrs', [])
            if attrs:
                st.caption("Атрибуты: " + " · ".join(attrs))

            for child in entry.get('children', []):
                # Streamlit не разрешает expander внутри expander —
                # вложенные подсистемы рендерим как подзаголовок с таблицей
                st.markdown(f"**↳ `ctx.{child['name']}` · {child.get('type', '?')}**")
                if child.get('doc'):
                    st.caption(child['doc'])
                _methods_table(child.get('methods', []),
                               child.get('rpc_service'),
                               f"sel_{child['name']}")
                if child.get('attrs'):
                    st.caption("Атрибуты: " + " · ".join(child['attrs']))


# ------------------------------------------------------------------ #
#  Вкладка 1: Управление узлами
# ------------------------------------------------------------------ #
def _render_node_panel(rpc):
    st.subheader("Панель управления узлами")

    # ---- Обзор сети ----
    if st.button("🔄 Обновить", key="sys_refresh"):
        st.session_state.pop('sys_node_detail', None)
        st.session_state.pop('sys_neighbors', None)

    try:
        detail = rpc.call('system', 'node_detail')
        st.session_state.sys_node_detail = detail
    except Exception as e:
        st.error(f"Ошибка получения данных: {e}")
        return

    detail = st.session_state.sys_node_detail
    own = detail.get('own', '?')

    # Метрики
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Узел", own)
    c2.metric("Подключено", len(detail.get('connected', [])))
    c3.metric("Известно", len(detail.get('known', [])))
    c4.metric("WS-сессии", len(detail.get('ws_connections', [])))

    st.divider()

    # ---- Таблица соседей ----
    connected = detail.get('connected', [])
    known = detail.get('known', [])

    if connected:
        st.markdown("**🟢 Подключены**")
        rows = []
        for n in connected:
            rows.append({
                'Node ID': n.get('node_id', '?'),
                'Host': n.get('host', '?'),
                'Port': n.get('port', '?'),
                'Services': ', '.join(n.get('services', [])) or '-',
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    if known:
        st.markdown("**🟡 Известны (через gossip)**")
        rows = []
        for n in known:
            rows.append({
                'Node ID': n.get('node_id', '?'),
                'Host': n.get('host', '?'),
                'Port': n.get('port', '?'),
                'Via': n.get('via', '-'),
                'Services': ', '.join(n.get('services', [])) or '-',
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    if not connected and not known:
        st.info("Нет известных узлов")

    st.divider()

    # ---- RPC-консоль ----
    st.subheader("RPC-консоль")

    # Выбор целевого узла
    all_node_ids = [own] + [
        n.get('node_id') for n in connected if n.get('node_id') != own
    ] + [
        n.get('node_id') for n in known
        if n.get('node_id') not in all_node_ids and n.get('node_id') != own
    ] if connected or known else [own]

    # убираем дубликаты, сохраняя порядок
    seen = set()
    unique_nodes = []
    for nid in all_node_ids:
        if nid and nid not in seen:
            seen.add(nid)
            unique_nodes.append(nid)

    # ---- Подстановка из вкладки «Контекст» (клик по методу) ----
    # ctx_pick кладётся в session_state таблицей методов, значения
    # применяются к виджетам ниже ДО их создания в этом ране
    pick = st.session_state.pop('ctx_pick', None)
    if pick and pick.get('dst') in unique_nodes:
        st.session_state['rpc_dst_node'] = pick['dst']

    dst_node = st.selectbox(
        "Целевой узел",
        unique_nodes,
        key="rpc_dst_node",
    )

    # Выбор сервиса
    local_services = detail.get('services', [])

    # Если удалённый узел — попробовать получить его сервисы
    remote_services = []
    if dst_node != own:
        try:
            remote_svc = rpc.call('netinfo', 'services', dst=dst_node)
            if isinstance(remote_svc, list):
                remote_services = remote_svc
        except Exception:
            pass

    available_services = remote_services if remote_services else local_services

    svc_options = available_services if available_services else list(KNOWN_METHODS.keys())

    if pick and pick.get('service'):
        if pick['service'] not in svc_options:
            svc_options.append(pick['service'])
        st.session_state['rpc_service'] = pick['service']

    selected_svc = st.selectbox(
        "Сервис",
        svc_options,
        key="rpc_service",
    )

    # Выбор метода
    method_options = KNOWN_METHODS.get(selected_svc, [])
    if not method_options:
        method_options = ["(ввести вручную)"]

    if pick and pick.get('service') == selected_svc and pick.get('method'):
        if pick['method'] in method_options:
            st.session_state['rpc_method'] = pick['method']
        else:
            if '(ввести вручную)' not in method_options:
                method_options.append('(ввести вручную)')
            st.session_state['rpc_method'] = '(ввести вручную)'
            st.session_state['rpc_method_manual'] = pick['method']
        st.toast(f"Подставлено в консоль: {selected_svc}.{pick['method']}")

    selected_method = st.selectbox(
        "Метод",
        method_options,
        key="rpc_method",
    )

    if selected_method == "(ввести вручную)":
        selected_method = st.text_input("Имя метода", key="rpc_method_manual")

    # Аргументы (JSON)
    arg_hint = _get_arg_hint(selected_svc, selected_method)
    if arg_hint:
        st.caption(f"💡 Ожидаемые аргументы: `{arg_hint}`")

    args_text = st.text_area(
        "Аргументы (JSON)",
        value="{}",
        height=100,
        key="rpc_args",
    )

    timeout = st.slider("Таймаут (сек)", 1, 60, 10, key="rpc_timeout")

    if st.button("▶ Выполнить", type="primary", key="rpc_execute"):
        if not selected_svc or not selected_method:
            st.warning("Выберите сервис и метод")
        else:
            try:
                call_data = json.loads(args_text) if args_text.strip() else {}
            except json.JSONDecodeError as e:
                st.error(f"Некорректный JSON: {e}")
                return

            target = dst_node if dst_node != own else None
            try:
                with st.spinner(f"Вызов {selected_svc}.{selected_method} → {dst_node}..."):
                    result = rpc.call(selected_svc, selected_method, call_data,
                                      timeout=timeout, dst=target)
                st.session_state.sys_last_result = result
                st.session_state.sys_last_call = f"{selected_svc}.{selected_method} → {dst_node}"
                st.success("✅ Успешно")
            except Exception as e:
                st.session_state.sys_last_result = {"__error__": str(e)}
                st.error(f"❌ Ошибка: {e}")

    # ---- Результат ----
    last_result = st.session_state.get('sys_last_result')
    if last_result is not None:
        last_call = st.session_state.get('sys_last_call', '')
        st.markdown(f"**Результат:** `{last_call}`")

        if isinstance(last_result, dict) and "__error__" in last_result:
            st.code(last_result["__error__"], language="text")
        elif isinstance(last_result, (dict, list)):
            st.json(last_result)
        else:
            st.code(str(last_result), language="text")


def _get_arg_hint(service: str, method: str) -> str:
    """Подсказка по аргументам для известных методов."""
    hints = {
        'netinfo.find_service':    '{"service": "certstool"}',
        'system.connect_to_node':  '{"host": "192.168.1.10", "port": 9000, "node_id": "Node1"}',
        'certstool.find_certificates_by_subject': '{"subject_pattern": "CN=Иванов"}',
        'certstool.install_from_node': '{"thumbprint": "...", "node_id": "Node1"}',
        'test.echo':               '{"message": "hello"}',
        'spawner.spawn':           '{"dst": "Node1", "service": "generator", "method": "start_stream"}',
    }
    return hints.get(f"{service}.{method}", "")


# ------------------------------------------------------------------ #
#  Вкладка 2: Подключение к удалённому узлу
# ------------------------------------------------------------------ #
def _render_connect(rpc):
    st.subheader("Подключение к узлу")

    st.markdown(
        "Инициировать исходящее WebSocket-подключение к удалённому узлу.  \n"
        "Подключение разрешено, если удалённый узел **не подключен** к локальному.  \n"
        "При успехе узел сохраняется в конфиг для автоматического переподключения."
    )

    # ---- Текущие подключения ----
    try:
        detail = rpc.call('system', 'node_detail')
    except Exception as e:
        st.error(f"Ошибка получения данных: {e}")
        return

    connected = detail.get('connected', [])
    if connected:
        st.markdown("**Текущие подключения:**")
        connected_ids = [n.get('node_id', '?') for n in connected]
        st.write(", ".join(f"`{n}`" for n in connected_ids))
    else:
        st.info("Нет активных подключений")

    st.divider()

    # ---- Форма подключения ----
    col1, col2 = st.columns([2, 1])

    with col1:
        host = st.text_input("Хост (адрес)", value="", placeholder="192.168.1.10",
                             key="connect_host")

    with col2:
        port = st.number_input("Порт", value=9000, min_value=1, max_value=65535,
                               key="connect_port")

    node_id = st.text_input("Node ID удалённого узла", value="",
                            placeholder="Node1", key="connect_node_id")

    # Подсказка: подстановка из известных узлов
    known = detail.get('known', [])
    if known:
        st.markdown("**Известные узлы (для справки):**")
        for n in known:
            nid = n.get('node_id', '?')
            h = n.get('host', '?')
            p = n.get('port', '?')
            is_conn = nid in [c.get('node_id') for c in connected]
            badge = "🟢" if is_conn else "🟡"
            if st.button(f"{badge} {nid} — {h}:{p}", key=f"fill_{nid}"):
                st.session_state.connect_host = h if h != '?' else ''
                st.session_state.connect_port = int(p) if p != '?' else 9000
                st.session_state.connect_node_id = nid
                st.rerun()

    st.divider()

    if st.button("🔌 Подключиться", type="primary", key="connect_btn"):
        if not host or not node_id:
            st.warning("Укажите хост и Node ID")
            return

        # Проверяем, не подключен ли уже
        already = any(n.get('node_id') == node_id for n in connected)
        if already:
            st.warning(f"Узел `{node_id}` уже подключен")
            return

        try:
            with st.spinner(f"Подключение к {node_id} ({host}:{port})..."):
                result = rpc.call('system', 'connect_to_node', {
                    'host': host,
                    'port': int(port),
                    'node_id': node_id,
                })

            if result.get('ok'):
                if result.get('saved'):
                    st.success(f"✅ Подключен → `{node_id}` (сохранено в конфиг)")
                else:
                    st.success(f"✅ Подключение инициировано → `{node_id}`")
                    if result.get('note'):
                        st.info(result['note'])
            else:
                st.error(f"❌ {result.get('error', 'Неизвестная ошибка')}")
        except Exception as e:
            st.error(f"❌ Ошибка: {e}")

    # ---- Активные коннекторы ----
    st.divider()
    st.subheader("Исходящие коннекторы")

    try:
        connectors = rpc.call('system', 'list_connectors')
    except Exception:
        connectors = []

    if connectors:
        rows = []
        for c in connectors:
            rows.append({
                'Имя': c.get('name', '?'),
                'Узел': c.get('peer', '?'),
                'URI': c.get('uri', '?'),
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.caption("Нет зарегистрированных коннекторов")

    # ---- Пиры из конфига ----
    st.divider()
    st.subheader("Соседи из конфига")

    try:
        status = rpc.call('webpanel', 'node_status')
        peers_data = rpc.call('system', 'config_peers', {})
    except Exception:
        peers_data = []

    if peers_data:
        rows = []
        connected_ids = [n.get('node_id') for n in detail.get('connected', [])]
        for p in peers_data:
            nid = p.get('node_id', '?')
            uri = p.get('uri', '?')
            is_conn = nid in connected_ids
            rows.append({
                'Node ID': nid,
                'URI': uri,
                'Статус': '🟢 Подключен' if is_conn else '⚪ Ожидание',
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.caption("Нет сохранённых пиров")
