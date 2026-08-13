# services/netinfo/web_ui.py — веб-интерфейс сервиса диагностики сети
# Контракт: функция render(rpc) вызывается streamlit для рендеринга вкладки сервиса

import streamlit as st
import pandas as pd


def render(rpc):
    tab1, tab2, tab3 = st.tabs(["Соседи", "Узлы", "Поиск сервиса"])

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
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
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
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

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
                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
            else:
                st.warning(f"Сервис `{service_name}` не найден ни на одном узле")
