# services/files/web_ui.py — вкладка «Файлы» в веб-панели
#
#  Сценарий: панель подключена к выбранному узлу (= куда скачиваем).
#  Выбираем узел-источник из подключенных, смотрим его шары/файлы,
#  качаем на текущий узел. Статус загрузок — автообновляемый фрагмент.

import time

try:
    import streamlit as st
except ImportError:
    # headless-сборка (Node_P2P_Core.exe): streamlit не установлен
    st = None

if st is not None:

    import pandas as pd

    def _human(n: float) -> str:
        for unit in ('Б', 'КБ', 'МБ', 'ГБ', 'ТБ'):
            if n < 1024 or unit == 'ТБ':
                return f"{n:.0f} {unit}" if unit == 'Б' else f"{n:.1f} {unit}"
            n /= 1024
        return f"{n:.1f} ТБ"

    def _status_icon(s: str) -> str:
        return {'running': '🟡', 'done': '🟢', 'error': '🔴',
                'cancelled': '⚪'}.get(s, '⚪')

    def render(rpc):
        st.header("🗂 Файловый транспорт")
        st.caption(
            "Передача файлов между узлами через mesh (`files.stat` → "
            "`files.serve` → push-стрим чанков с ACK). Загрузки выполняет "
            "**выбранный узел**, файлы приходят в `files.download_dir` "
            "из config.yaml. Раздача настраивается там же в `files.shares`."
        )

        # ---------------- узел-источник ---------------- #
        try:
            detail = rpc.call('system', 'node_detail')
        except Exception as e:
            st.error(f"Нет связи с выбранным узлом: {e}")
            return

        me = detail.get('own', '?')
        peers = [n.get('node_id') for n in detail.get('connected', [])
                 if n.get('node_id') and n.get('node_id') != me]
        known = [n.get('node_id') for n in detail.get('known', [])
                 if n.get('node_id') and n.get('node_id') != me]

        c1, c2 = st.columns([2, 1])
        src = c1.selectbox(
            "Узел-источник (подключенные)", ['(не выбран)'] + peers,
            key="fl_src", help="Файл тянем с этого узла на текущий."
        )
        auto = c2.toggle("Статус: авто (3 сек)", value=True, key="fl_auto")

        if not peers:
            st.info("Нет подключенных узлов — подключите источник во "
                    "вкладке «Система».")
            _downloads_block(rpc, auto)
            return

        if src == '(не выбран)':
            _downloads_block(rpc, auto)
            return

        # ---------------- каталог источника ---------------- #
        try:
            shares_res = rpc.call('files', 'list_shares', {}, dst=src)
        except Exception as e:
            st.warning(f"Узел `{src}` не отвечает на files.list_shares: {e}")
            _downloads_block(rpc, auto)
            return

        shares = shares_res.get('shares', []) if shares_res.get('ok') else []
        if not shares:
            st.info(f"На узле `{src}` нет раздаваемых шар "
                    f"(config.yaml → files.shares).")
            _downloads_block(rpc, auto)
            return

        top = st.columns([2, 2])
        share_pick = top[0].selectbox(
            "Шара", [s['name'] for s in shares], key="fl_share",
            format_func=lambda n: next(
                (f"{n} — {s['files']} файл(ов), {_human(s['bytes'])}"
                 for s in shares if s['name'] == n), n))
        pattern = top[1].text_input(
            "Маска имени", key="fl_pattern", placeholder="*.mp4, report*…")

        if st.button("🔎 Найти файлы", key="fl_find_btn"):
            st.session_state['fl_entries'] = None
        if st.session_state.get('fl_entries') is None or \
           st.session_state.get('fl_src_used') != (src, share_pick):
            try:
                res = rpc.call('files', 'find',
                               {'share': share_pick,
                                'pattern': pattern or '*'},
                               dst=src)
                st.session_state['fl_entries'] = res.get('entries', []) \
                    if res.get('ok') else []
                st.session_state['fl_src_used'] = (src, share_pick)
            except Exception as e:
                st.error(f"Ошибка поиска: {e}")
                return

        entries = st.session_state.get('fl_entries') or []
        if not entries:
            st.info("Ничего не найдено по маске.")
            _downloads_block(rpc, auto)
            return

        df = pd.DataFrame([{
            'Файл': e['path'],
            'Размер': _human(e['size']),
            'Изменён': time.strftime('%Y-%m-%d %H:%M',
                                     time.localtime(e['mtime'])),
        } for e in entries])

        st.markdown("**Файлы источника** — выделите строку:")
        event = st.dataframe(
            df, use_container_width=True, hide_index=True,
            on_select='rerun', selection_mode='single-row', key='fl_rows',
            height=min(420, 35 * (len(entries) + 1)),
        )
        rows = event.selection.rows

        btn_col, hint_col = st.columns([1, 3])
        pick_disabled = not rows
        picked = entries[rows[0]] if rows else None
        if btn_col.button("⬇ Скачать на этот узел",
                          type="primary", disabled=pick_disabled,
                          key="fl_dl_btn"):
            try:
                res = rpc.call('files', 'download',
                               {'dst': src, 'share': picked['share'],
                                'path': picked['path']})
                if res.get('ok'):
                    if res.get('done'):
                        st.toast(f"Уже скачан и цел: {picked['path']}")
                    else:
                        st.toast(f"Загрузка запущена: {picked['path']}")
                else:
                    st.error(res.get('error', 'ошибка запуска'))
            except Exception as e:
                st.error(f"Ошибка: {e}")
        hint_col.caption(
            "Файл придёт в `files.download_dir` текущего узла. Повторный "
            "запуск докачивает `.part` с места обрыва.")

        if known and not peers:
            st.caption(f"Известны, но не подключены: {', '.join(known)}")

        _downloads_block(rpc, auto)

    # ------------------------------------------------------------------ #

    def _downloads_block(rpc, auto: bool):
        """Лента загрузок текущего узла."""
        st.divider()
        st.subheader("📥 Загрузки этого узла")

        if auto:
            @st.fragment(run_every="3s")
            def view():
                _draw_downloads(rpc)
        else:
            @st.fragment
            def view():
                _draw_downloads(rpc)

        view()

    def _draw_downloads(rpc):
        try:
            res = rpc.call('files', 'downloads')
        except Exception as e:
            st.error(f"Ошибка получения статусов: {e}")
            return
        items = res.get('downloads', []) if res.get('ok') else []
        if not items:
            st.caption("Загрузок нет")
            return

        for d in items:
            icon = _status_icon(d['status'])
            title = (f"{icon} {d['name']} — {_human(d.get('received', 0))} / "
                     f"{_human(d['size'])} ← {d['src']}")
            box = st.expander(title, expanded=(d['status'] == 'error'))
            with box:
                if d['status'] == 'running':
                    st.progress(min(d.get('pct', 0), 100) / 100.0)
                if d.get('error'):
                    st.error(d['error'])
                if d['status'] == 'done':
                    st.success(f"Готово → `{d['path']}`")
                col1, col2, _ = st.columns([1, 1, 4])
                col1.caption(
                    f"sha256 `{str(d.get('id'))[:16]}…`")
                if d['status'] == 'running' and col2.button(
                        "✖ Отменить", key=f"fl_cancel_{d['label']}"):
                    try:
                        rpc.call('files', 'cancel_download',
                                 {'label': d['label']})
                        st.rerun(scope="fragment")
                    except Exception as e:
                        st.error(str(e))
