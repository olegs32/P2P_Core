# services/config/web_ui.py — веб-интерфейс редактирования config.yaml + браузер SecureStorage
# Контракт: функция render(rpc) вызывается streamlit для рендеринга вкладки сервиса

import logging

try:
    import streamlit as st
except ImportError:
    st = None

import yaml

logging.getLogger("streamlit.runtime.scriptrunner_utils.script_run_context").setLevel(logging.ERROR)
logging.getLogger("streamlit.runtime.state.session_state_proxy").setLevel(logging.ERROR)

from services.config.service import SECRET_PLACEHOLDER


def _fmt_ts(ts) -> str:
    from datetime import datetime
    try:
        return datetime.fromtimestamp(float(ts)).strftime('%d.%m.%Y %H:%M:%S')
    except (TypeError, ValueError, OSError):
        return '-'


def _fmt_size(n) -> str:
    if not n:
        return '-'
    for unit in ('Б', 'КБ', 'МБ'):
        if n < 1024 or unit == 'МБ':
            return f'{n:.0f} {unit}' if unit == 'Б' else f'{n:.1f} {unit}'
        n /= 1024
    return '-'


def _append_snippet_to_editor(snippet: str):
    st.session_state['cfg_pending_snippet'] = snippet.strip() + "\n"
    st.toast("Шаблон вставлен в редактор — проверьте и нажмите Сохранить")
    st.rerun()


def _deep_merge_yaml(target: dict, source: dict):
    for k, v in source.items():
        if k not in target or target[k] is None:
            target[k] = v
        elif isinstance(target[k], dict) and isinstance(v, dict):
            _deep_merge_yaml(target[k], v)
        elif isinstance(target[k], list) and isinstance(v, list):
            target[k].extend(v)
        else:
            pass


def _apply_pending_snippet():
    snippet = st.session_state.pop('cfg_pending_snippet', None)
    if not snippet:
        return
    snippet = snippet.strip() + "\n"
    cur = st.session_state.get('cfg_editor')
    if isinstance(cur, dict):
        cur_text = cur.get('text', '')
        if not cur_text:
            for k, v in list(st.session_state.items()):
                if k.startswith('cfg_editor_ace') and isinstance(v, str) and v.strip():
                    cur_text = v
                    break
    else:
        cur_text = cur if isinstance(cur, str) else ""
        if not cur_text:
            for k, v in list(st.session_state.items()):
                if k.startswith('cfg_editor_ace') and isinstance(v, str) and v.strip():
                    cur_text = v
                    break
    new_text = None
    try:
        cur_data = yaml.safe_load(cur_text) if cur_text.strip() else {}
        snip_data = yaml.safe_load(snippet)
        if isinstance(cur_data, dict) and isinstance(snip_data, dict):
            _deep_merge_yaml(cur_data, snip_data)
            new_text = yaml.safe_dump(cur_data, allow_unicode=True, sort_keys=False, default_flow_style=False)
        else:
            raise ValueError("not dict")
    except Exception:
        new_text = cur_text.rstrip() + "\n\n" + snippet
    ver = int(st.session_state.get('cfg_ace_version', 0)) + 1
    st.session_state['cfg_ace_version'] = ver
    new_ace_key = f"cfg_editor_ace_{ver}"
    if isinstance(cur, dict) or isinstance(st.session_state.get('cfg_editor'), dict):
        st.session_state['cfg_editor'] = {"text": new_text}
    else:
        st.session_state['cfg_editor'] = new_text
    st.session_state[new_ace_key] = new_text
    st.session_state['cfg_editor_ace'] = new_text


def _get_known_nodes(rpc):
    try:
        detail = rpc.call('system', 'node_detail', {}, timeout=10)
    except Exception:
        detail = {}
    own = detail.get('own', '')
    nodes = []
    for n in detail.get('connected', []) + detail.get('known', []):
        nid = n.get('node_id')
        if nid and nid != own:
            nodes.append({'node_id': nid, 'host': n.get('host', ''), 'port': n.get('port', 9000)})
    seen = {}
    uniq = []
    for n in nodes:
        if n['node_id'] not in seen:
            seen[n['node_id']] = True
            uniq.append(n)
    return own, uniq


def _render_quick_templates(rpc):
    st.divider()
    st.subheader("🧩 Быстрые шаблоны")
    st.caption("Автоматизируйте добавление списков/словарей — выберите список, заполните поля и вставьте YAML в редактор.")
    kind = st.selectbox(
        "Список конфига",
        ["local.peers", "update.sources", "files.shares"],
        format_func=lambda x: {
            "local.peers": "Пиры — local.peers (подключения)",
            "update.sources": "Источники обновлений — update.sources",
            "files.shares": "Шары файлов — files.shares",
        }.get(x, x),
        key="cfg_tpl_kind",
    )
    if kind == "local.peers":
        own, nodes = _get_known_nodes(rpc)
        node_ids = [n['node_id'] for n in nodes]
        c1, c2 = st.columns([1, 1])
        with c1:
            mode = st.radio("Способ указания узла", ["Выбрать из сети", "Ввести вручную"], key="cfg_tpl_peer_mode", horizontal=True)
            if mode == "Выбрать из сети" and node_ids:
                sel = st.selectbox("node_id", node_ids, key="cfg_tpl_peer_sel")
                host, port = "", 9000
                for n in nodes:
                    if n['node_id'] == sel:
                        host, port = n['host'], n['port']
                        break
                node_id_val = sel
                _uri_default = f"ws://{host}:{port}/ws/" if host else f"ws://{sel}:9000/ws/"
                prev_sel = st.session_state.get('_cfg_tpl_peer_prev_sel')
                if prev_sel != sel:
                    st.session_state['_cfg_tpl_peer_prev_sel'] = sel
                    st.session_state['cfg_tpl_peer_uri'] = _uri_default
            else:
                node_id_val = st.text_input("node_id", placeholder="NodeX", key="cfg_tpl_peer_manual")
                host = st.text_input("host для uri", placeholder="192.168.53.53", key="cfg_tpl_peer_host")
                port = st.number_input("port", value=9000, min_value=1, max_value=65535, key="cfg_tpl_peer_port")
                _uri_default = f"ws://{host}:{port}/ws/" if host else "ws://HOST:PORT/ws/"
                if 'cfg_tpl_peer_uri' not in st.session_state:
                    st.session_state['cfg_tpl_peer_uri'] = _uri_default
                node_id_val = node_id_val.strip()
        with c2:
            uri_val = st.text_input("uri", key="cfg_tpl_peer_uri", help="Формат ws://ip:port/ws/ — дописывается автоматически")
            st.caption("Пример: `ws://192.168.53.53:9000/ws/`")
            preview = f'local:\n  peers:\n    - node_id: \"{node_id_val or "NodeX"}\"\n      uri: \"{uri_val or _uri_default}\"'
            st.code(preview, language="yaml")
        if st.button("➕ Вставить peers в редактор", key="cfg_tpl_peer_add", type="primary"):
            nid = node_id_val.strip() if isinstance(node_id_val, str) else ""
            uri = st.session_state.get('cfg_tpl_peer_uri', '').strip() or _uri_default
            if not nid:
                st.warning("Укажите node_id")
            elif not uri.startswith("ws://"):
                st.warning("uri должен начинаться с ws://")
            else:
                snippet = f"# peers — шаблон\nlocal:\n  peers:\n    - node_id: \"{nid}\"\n      uri: \"{uri}\""
                _append_snippet_to_editor(snippet)
    elif kind == "update.sources":
        own, nodes = _get_known_nodes(rpc)
        node_ids = [n['node_id'] for n in nodes] or ["NodeX"]
        c1, c2 = st.columns(2)
        with c1:
            sel_node = st.selectbox("node (источник релизов)", node_ids, key="cfg_tpl_upd_node")
            st.caption("Узел, где лежит шара с релизами")
        with c2:
            shares = []
            shares_err = None
            try:
                res = rpc.call('files', 'list_shares', {}, dst=sel_node, timeout=10)
                if isinstance(res, dict) and res.get('ok'):
                    shares = [s['name'] for s in res.get('shares', [])]
                elif isinstance(res, dict) and res.get('error'):
                    shares_err = res.get('error')
            except Exception as e:
                shares_err = str(e)
            if shares:
                sel_share = st.selectbox("share", shares, key="cfg_tpl_upd_share")
            else:
                sel_share = st.text_input("share", value="releases", key="cfg_tpl_upd_share_manual", help="Имя шары с релизами на узле-источнике")
                if shares_err:
                    st.caption(f"Шары не получены: {shares_err} — введите вручную")
            share_val = sel_share if isinstance(sel_share, str) else "releases"
        preview = f'update:\n  sources:\n    - node: \"{sel_node}\"\n      share: \"{share_val}\"'
        st.code(preview, language="yaml")
        if st.button("➕ Вставить sources в редактор", key="cfg_tpl_upd_add", type="primary"):
            if not sel_node or not share_val:
                st.warning("Укажите node и share")
            else:
                snippet = f"# update.sources — шаблон\nupdate:\n  sources:\n    - node: \"{sel_node}\"\n      share: \"{share_val}\""
                _append_snippet_to_editor(snippet)
    elif kind == "files.shares":
        c1, c2 = st.columns(2)
        with c1:
            name = st.text_input("name (имя шары)", placeholder="my_share", key="cfg_tpl_files_name")
            path = st.text_input("path (локальный путь)", placeholder=r"C:\data\share", key="cfg_tpl_files_path")
        with c2:
            allow_raw = st.text_input("allow (узлы, через запятую, пусто=всем)", placeholder="Node1, Node2", key="cfg_tpl_files_allow")
            chunk = st.select_slider("chunk_size", options=[64, 128, 256, 512, 1024, 2048], value=256, key="cfg_tpl_files_chunk", format_func=lambda k: f"{k} КБ")
        allow_list = [x.strip() for x in (allow_raw or "").split(",") if x.strip()]
        allow_yaml = "[]" if not allow_list else "[" + ", ".join(f'\"{a}\"' for a in allow_list) + "]"
        preview = f'files:\n  shares:\n    - name: \"{name or "my_share"}\"\n      path: \"{path or r"C:\\path"}\"\n      allow: {allow_yaml}\n      chunk_size: {chunk*1024}'
        st.code(preview, language="yaml")
        if st.button("➕ Вставить shares в редактор", key="cfg_tpl_files_add", type="primary"):
            if not name or not path:
                st.warning("Укажите name и path")
            else:
                snippet = f"# files.shares — шаблон\nfiles:\n  shares:\n    - name: \"{name}\"\n      path: \"{path}\"\n      allow: {allow_yaml}\n      chunk_size: {chunk*1024}"
                _append_snippet_to_editor(snippet)


def _render_storage_browser(rpc):
    st.subheader("🔐 SecureStorage — файловый браузер")
    st.caption("Только SE-режим: содержимое `p2p_secure.bin` (PerFileArchive). Пути — `/config/app.yaml`, `/certs/*`, `/keys/*` и т.д. Бинарные файлы — base64.")
    try:
        info = rpc.call('config', 'storage_info', {}, timeout=10)
    except Exception as e:
        st.error(f"storage_info failed: {e}")
        return
    if not isinstance(info, dict) or not info.get('ok'):
        st.warning(f"Хранилище недоступно: {(info or {}).get('error','not SE')}")
        st.caption("Откройте эту вкладку на SE-ноде (собранной с `src/se`). В open-режиме хранилища нет.")
        return
    c1, c2, c3 = st.columns(3)
    c1.metric("Файлов", info.get('files', 0))
    c2.metric("bin", _fmt_size(info.get('bin_size', 0)))
    c3.metric("key", _fmt_size(info.get('key_size', 0)))
    st.caption(f"`{info.get('bin_path','')}`")
    # префикс
    prefix = st.text_input("Префикс", value="/", key="store_prefix", help="/, /config, /certs, /keys")
    col_a, col_b = st.columns([1, 1])
    do_list = col_a.button("📂 Показать", key="store_list")
    do_refresh = col_b.button("🔄 Обновить", key="store_refresh")
    if do_list or do_refresh or 'store_files' not in st.session_state:
        try:
            lst = rpc.call('config', 'storage_list', {"prefix": prefix}, timeout=10)
            if isinstance(lst, dict) and lst.get('ok'):
                st.session_state['store_files'] = lst.get('files', [])
                st.session_state['store_prefix'] = prefix
            else:
                st.error((lst or {}).get('error','no data'))
        except Exception as e:
            st.error(f"list failed: {e}")
    files = st.session_state.get('store_files', [])
    if files:
        rows = [{'Путь': f['path'], 'Размер': _fmt_size(f['size'])} for f in files]
        st.dataframe(rows, width='stretch', hide_index=True)
        names = [f['path'] for f in files]
        sel = st.selectbox("Файл для просмотра", names, key="store_sel")
        if st.button("👁 Показать содержимое", key="store_read"):
            try:
                res = rpc.call('config', 'storage_read', {"path": sel}, timeout=15)
                if isinstance(res, dict) and res.get('ok'):
                    st.caption(f"{res['path']} — {res['size']} Б, {'text' if res['is_text'] else 'base64'}")
                    # для больших файлов — ограничим
                    txt = res['text'] or ""
                    if len(txt) > 8000:
                        st.code(txt[:8000] + "\n… (обрезано)", language="yaml" if res['is_text'] else "text")
                    else:
                        st.code(txt, language="yaml" if res['is_text'] and sel.endswith(('.yaml','.yml','.json')) else "text")
                    if not res['is_text']:
                        st.download_button("⬇ Скачать base64", data=txt, file_name=sel.replace('/','_') + ".b64", key="store_dl_b64")
                    else:
                        st.download_button("⬇ Скачать", data=txt, file_name=sel.replace('/','_'), key="store_dl_txt")
                else:
                    st.error((res or {}).get('error','no data'))
            except Exception as e:
                st.error(f"read failed: {e}")
    else:
        st.caption("Файлов нет по префиксу")


def render(rpc):
    if st is None:
        return
    st.subheader("🛠️ Конфигурация узла")
    st.caption(
        "Редактирование **config.yaml** выбранного узла. Перед записью конфиг "
        "валидируется на узле — битый файл не сохранится. Каждая запись "
        "создаёт резервную копию (ротация 10 штук). Уровни логирования "
        "применяются сразу, остальные секции полностью вступают в силу после "
        "перезапуска узла. Вкладка «Хранилище» — файловый браузер SecureStorage (только SE)."
    )
    tab_cfg, tab_store = st.tabs(["🛠️ Конфиг", "🔐 Хранилище"])
    with tab_cfg:
        last = st.session_state.pop('cfg_result', None)
        if last:
            kind, text = last
            (st.success if kind == 'ok' else st.error)(text)
        try:
            info = rpc.call('config', 'get', {}, timeout=15)
        except Exception as e:
            st.error(f"Ошибка получения конфига: {e}")
            return
        if not isinstance(info, dict) or not info.get('ok'):
            st.error(f"Узел ответил ошибкой: {(info or {}).get('error', 'нет данных')}")
            return
        _use_code_editor = hasattr(st, "code_editor")
        _current_node = st.session_state.get('selected_node')
        _prev_node = st.session_state.get('cfg_loaded_node')
        _node_changed = _prev_node is not None and _prev_node != _current_node
        if _use_code_editor:
            need_sync = (
                _node_changed
                or st.session_state.get('cfg_loaded_mtime') != info.get('mtime')
                or 'cfg_editor' not in st.session_state
            )
            if need_sync or not isinstance(st.session_state.get('cfg_editor'), dict):
                st.session_state['cfg_editor'] = {"text": info['text']}
                st.session_state['cfg_loaded_mtime'] = info['mtime']
                st.session_state['cfg_loaded_node'] = _current_node
                for _k in list(st.session_state.keys()):
                    if _k.startswith('cfg_editor_ace'):
                        st.session_state[_k] = info['text']
            _apply_pending_snippet()
            _initial = st.session_state['cfg_editor'].get("text", info['text']) if isinstance(st.session_state['cfg_editor'], dict) else info['text']
            resp = st.code_editor(
                _initial,
                language="yaml",
                key="cfg_editor",
                height=600,
            )
            if isinstance(resp, dict):
                edited = resp.get("text", _initial)
            elif isinstance(resp, str):
                edited = resp
            else:
                cur = st.session_state.get('cfg_editor')
                edited = cur.get("text", _initial) if isinstance(cur, dict) else str(cur or "")
        else:
            need_sync = (
                _node_changed
                or st.session_state.get('cfg_loaded_mtime') != info.get('mtime')
                or 'cfg_editor' not in st.session_state
                or isinstance(st.session_state.get('cfg_editor'), dict)
            )
            if need_sync:
                _txt = info['text']
                if isinstance(st.session_state.get('cfg_editor'), dict):
                    _txt = st.session_state['cfg_editor'].get("text", info['text'])
                st.session_state['cfg_editor'] = _txt
                st.session_state['cfg_loaded_mtime'] = info['mtime']
                st.session_state['cfg_loaded_node'] = _current_node
                for _k in list(st.session_state.keys()):
                    if _k.startswith('cfg_editor_ace'):
                        st.session_state[_k] = _txt
            _apply_pending_snippet()
            try:
                from streamlit_ace import st_ace  # type: ignore
                _ace_val = st.session_state.get('cfg_editor', info['text'])
                _ace_theme = "monokai"
                _ace_version = int(st.session_state.get('cfg_ace_version', 0))
                _ace_key = f"cfg_editor_ace_{_ace_version}" if _ace_version else "cfg_editor_ace"
                edited = st_ace(
                    value=_ace_val if isinstance(_ace_val, str) else info['text'],
                    language="yaml",
                    theme=_ace_theme,
                    key=_ace_key,
                    height=600,
                    auto_update=True,
                    show_gutter=True,
                    wrap=False,
                )
                if edited is not None:
                    st.session_state['cfg_editor'] = edited
            except Exception:
                edited = st.text_area(info['path'], height=600, key='cfg_editor')
        st.caption(f"Секрет замаскирован как `{SECRET_PLACEHOLDER}` — при "
                   f"сохранении подставится реальное значение.")
        col1, col2, col3 = st.columns([1, 2, 1.6])
        do_save = col1.button("💾 Сохранить", type="primary", key='cfg_btn_save')
        confirm_restart = col2.checkbox(
            "Я понимаю, что узел перезапустится и панель потеряет связь",
            key='cfg_confirm_restart',
        )
        do_save_restart = col3.button(
            "🔄 Сохранить и перезапустить",
            disabled=not confirm_restart,
            key='cfg_btn_save_restart',
            help="Сохранить config.yaml и перезапустить узел (detached-стартер)",
        )
        if do_save or do_save_restart:
            _run_save(rpc, edited, info['mtime'], restart=do_save_restart)
            return
        changed_hint = st.session_state.pop('cfg_changed_sections', None)
        if changed_hint:
            st.warning("⚠️ Изменённые секции требуют рестарта: **"
                       + ", ".join(changed_hint) + "**. Нажмите «Сохранить и "
                       "перезапустить» или перезапустите узел позже.")
        hot_hint = st.session_state.pop('cfg_applied_hot', None)
        if hot_hint:
            st.info("Горячо применено: " + "; ".join(hot_hint))
        _render_quick_templates(rpc)
        _render_backups(rpc, info)
    with tab_store:
        _render_storage_browser(rpc)


def _run_save(rpc, text: str, base_mtime: float, restart: bool):
    payload = {'text': text, 'base_mtime': base_mtime, 'restart': restart}
    result, error = None, None
    with st.spinner("Сохранение..." if not restart
                    else "Сохранение и перезапуск..."):
        try:
            result = rpc.call('config', 'save', payload, timeout=30)
        except Exception as e:
            error = str(e)
    if restart and not (isinstance(result, dict) and result.get('ok')):
        st.session_state['cfg_result'] = (
            'ok',
            "✅ Команда отправлена; узел перезапускается (связь прервалась — "
            f"это ожидаемо){': ' + error if error else ''}.")
    elif isinstance(result, dict) and result.get('ok'):
        parts = []
        if result.get('restored'):
            parts.append(f"восстановлен бэкап {result['restored']}")
        if result.get('note'):
            parts.append(result['note'])
        body = "✅ Сохранено" + (" — " + "; ".join(parts) if parts else "")
        warnings = result.get('warnings') or []
        if warnings:
            body += "\n\n⚠️ " + "\n⚠️ ".join(warnings)
        st.session_state['cfg_result'] = ('ok', body)
        st.session_state['cfg_loaded_mtime'] = result.get('mtime')
        if result.get('restart_required') and not restart:
            st.session_state['cfg_changed_sections'] = \
                result.get('restart_required_sections') or []
        if result.get('applied_hot'):
            st.session_state['cfg_applied_hot'] = result['applied_hot']
    elif isinstance(result, dict) and result.get('conflict'):
        st.session_state['cfg_result'] = (
            'error', f"❌ {result.get('error', 'конфликт изменений')}")
    elif isinstance(result, dict) and result.get('errors'):
        st.session_state['cfg_result'] = (
            'error', "❌ Валидация не прошла:\n\n- "
                     + "\n- ".join(result['errors']))
    else:
        msg = ((result or {}).get('error')
               if isinstance(result, dict) else '') or error or 'нет данных'
        st.session_state['cfg_result'] = ('error', f"❌ {msg}")
    st.rerun()


def _render_backups(rpc, info):
    backups = info.get('backups') or []
    with st.expander(f"🗂 Резервные копии ({len(backups)})"):
        if not backups:
            st.caption("Пока нет ни одной копии — появятся при первой записи")
            return
        rows = [{
            'Имя': b['name'],
            'Создана': _fmt_ts(b['ts']),
            'Размер': _fmt_size(b['size']),
        } for b in backups]
        st.dataframe(rows, width='stretch', hide_index=True)
        picked = st.selectbox("Копия", [b['name'] for b in backups],
                              key='cfg_backup_pick')
        c1, c2, c3 = st.columns([1, 1.4, 2.6])
        view_clicked = c1.button("👁 Показать", key='cfg_btn_view_backup')
        confirm_restore = c3.checkbox(
            "Подтверждаю восстановление (текущий конфиг перед этим тоже "
            "попадёт в бэкап)",
            key='cfg_confirm_restore',
        )
        restore_clicked = c2.button("♻️ Восстановить", key='cfg_btn_restore',
                                    disabled=not confirm_restore,
                                    type="primary")
        if view_clicked:
            try:
                res = rpc.call('config', 'read_backup',
                               {'name': picked}, timeout=15)
                if isinstance(res, dict) and res.get('ok'):
                    st.code(res['text'], language='yaml')
                else:
                    st.error((res or {}).get('error', 'нет данных'))
            except Exception as e:
                st.error(f"Ошибка чтения бэкапа: {e}")
        if restore_clicked:
            _run_restore(rpc, picked)


def _render_storage_browser(rpc):
    st.subheader("🔐 SecureStorage — файловый браузер")
    st.caption("Только SE-режим: содержимое `p2p_secure.bin` (PerFileArchive). Пути — `/config/app.yaml`, `/certs/*`, `/keys/*` и т.д. Бинарные файлы — base64.")
    try:
        info = rpc.call('config', 'storage_info', {}, timeout=10)
    except Exception as e:
        st.error(f"storage_info failed: {e}")
        return
    if not isinstance(info, dict) or not info.get('ok'):
        st.warning(f"Хранилище недоступно: {(info or {}).get('error','not SE')}")
        st.caption("Откройте эту вкладку на SE-ноде (собранной с `src/se`). В open-режиме хранилища нет.")
        return
    c1, c2, c3 = st.columns(3)
    c1.metric("Файлов", info.get('files', 0))
    c2.metric("bin", _fmt_size(info.get('bin_size', 0)))
    c3.metric("key", _fmt_size(info.get('key_size', 0)))
    st.caption(f"`{info.get('bin_path','')}`")
    prefix = st.text_input("Префикс", value="/", key="store_prefix", help="/, /config, /certs, /keys")
    col_a, col_b = st.columns([1, 1])
    do_list = col_a.button("📂 Показать", key="store_list")
    do_refresh = col_b.button("🔄 Обновить", key="store_refresh")
    if do_list or do_refresh or 'store_files' not in st.session_state:
        try:
            lst = rpc.call('config', 'storage_list', {"prefix": prefix}, timeout=10)
            if isinstance(lst, dict) and lst.get('ok'):
                st.session_state['store_files'] = lst.get('files', [])
            else:
                st.error((lst or {}).get('error','no data'))
        except Exception as e:
            st.error(f"list failed: {e}")
    files = st.session_state.get('store_files', [])
    if files:
        rows = [{'Путь': f['path'], 'Размер': _fmt_size(f['size'])} for f in files]
        st.dataframe(rows, width='stretch', hide_index=True)
        names = [f['path'] for f in files]
        sel = st.selectbox("Файл для просмотра", names, key="store_sel")
        if st.button("👁 Показать содержимое", key="store_read"):
            try:
                res = rpc.call('config', 'storage_read', {"path": sel}, timeout=15)
                if isinstance(res, dict) and res.get('ok'):
                    st.caption(f"{res['path']} — {res['size']} Б, {'text' if res['is_text'] else 'base64'}")
                    txt = res['text'] or ""
                    if len(txt) > 8000:
                        st.code(txt[:8000] + "\n… (обрезано)", language="yaml" if res['is_text'] else "text")
                    else:
                        st.code(txt, language="yaml" if res['is_text'] and sel.endswith(('.yaml','.yml','.json')) else "text")
                    if not res['is_text']:
                        st.download_button("⬇ Скачать base64", data=txt, file_name=sel.replace('/','_') + ".b64", key="store_dl_b64")
                    else:
                        st.download_button("⬇ Скачать", data=txt, file_name=sel.replace('/','_'), key="store_dl_txt")
                else:
                    st.error((res or {}).get('error','no data'))
            except Exception as e:
                st.error(f"read failed: {e}")
    else:
        st.caption("Файлов нет по префиксу")


def _run_restore(rpc, name: str):
    result, error = None, None
    with st.spinner(f"Восстановление {name}..."):
        try:
            result = rpc.call('config', 'restore', {'name': name}, timeout=30)
        except Exception as e:
            error = str(e)
    if isinstance(result, dict) and result.get('ok'):
        st.session_state['cfg_result'] = ('ok', f"✅ Восстановлено из {name}")
        st.session_state.pop('cfg_loaded_mtime', None)
        st.session_state.pop('cfg_loaded_node', None)
        if result.get('restart_required'):
            st.session_state['cfg_changed_sections'] = \
                result.get('restart_required_sections') or []
        if result.get('applied_hot'):
            st.session_state['cfg_applied_hot'] = result['applied_hot']
    else:
        msg = ((result or {}).get('error')
               if isinstance(result, dict) else '') or error or 'нет данных'
        st.session_state['cfg_result'] = ('error', f"❌ {msg}")
    st.rerun()
