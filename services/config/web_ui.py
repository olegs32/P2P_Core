# services/config/web_ui.py — веб-интерфейс редактирования config.yaml узла
# Контракт: функция render(rpc) вызывается streamlit для рендеринга вкладки сервиса

import logging

try:
    import streamlit as st
except ImportError:
    st = None

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


def render(rpc):
    if st is None:
        return
    st.subheader("🛠️ Конфигурация узла")
    st.caption(
        "Редактирование **config.yaml** выбранного узла. Перед записью конфиг "
        "валидируется на узле — битый файл не сохранится. Каждая запись "
        "создаёт резервную копию (ротация 10 штук). Уровни логирования "
        "применяются сразу, остальные секции полностью вступают в силу после "
        "перезапуска узла."
    )

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

    # синхронизация редактора с серверным файлом: первая загрузка,
    # смена узла, восстановление бэкапа или внешнее изменение файла
    if st.session_state.get('cfg_loaded_mtime') != info.get('mtime') \
            or 'cfg_editor' not in st.session_state:
        st.session_state['cfg_editor'] = info['text']
        st.session_state['cfg_loaded_mtime'] = info['mtime']

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

    _render_backups(rpc, info)


def _run_save(rpc, text: str, base_mtime: float, restart: bool):
    """Сохранить конфиг; потеря связи ожидаема при перезапуске узла."""
    payload = {'text': text, 'base_mtime': base_mtime, 'restart': restart}
    result, error = None, None
    with st.spinner("Сохранение..." if not restart
                    else "Сохранение и перезапуск..."):
        try:
            result = rpc.call('config', 'save', payload, timeout=30)
        except Exception as e:
            error = str(e)

    if restart and not (isinstance(result, dict) and result.get('ok')):
        # RESPONSE не дождались — вероятнее всего узел уже ушёл в перезапуск
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


def _run_restore(rpc, name: str):
    result, error = None, None
    with st.spinner(f"Восстановление {name}..."):
        try:
            result = rpc.call('config', 'restore', {'name': name}, timeout=30)
        except Exception as e:
            error = str(e)

    if isinstance(result, dict) and result.get('ok'):
        st.session_state['cfg_result'] = ('ok', f"✅ Восстановлено из {name}")
        # заставить редактор перечитать текст с сервера на следующем реране
        st.session_state.pop('cfg_loaded_mtime', None)
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
