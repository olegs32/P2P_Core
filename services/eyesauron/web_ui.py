# services/eyesauron/web_ui.py — вкладка EyeSauron в веб-панели
# Контракт: render(rpc); rpc — RPCProxy (dst = выбранный в панели узел)

import logging

try:
    import streamlit as st
except ImportError:
    # Node-сборка без UI
    st = None
import pandas as pd

logging.getLogger("streamlit.runtime.scriptrunner_utils.script_run_context").setLevel(logging.ERROR)
logging.getLogger("streamlit.runtime.state.session_state_proxy").setLevel(logging.ERROR)


def _fmt_size(n) -> str:
    if not n:
        return '0 Б'
    for unit in ('Б', 'КБ', 'МБ', 'ГБ'):
        if n < 1024 or unit == 'ГБ':
            return f'{n:.0f} {unit}' if unit == 'Б' else f'{n:.1f} {unit}'
        n /= 1024
    return '-'



"""
"Сбор и просмотр снимков экранов машин сети. Узел-коллектор принимает "
        "кадры от агентов и пишет raw PNG в хранилище; агент захватывает экраны "
        "своей машины через хелпер в сессии пользователя. По умолчанию сервис "
        "выключен — включается на узле в `config.yaml`: "
        "`eyesauron: { enabled: true, collect: true }`. "
        "Вкладка работает с выбранным в панели узлом."
"""
def render(rpc):
    if st is None:
        return
    st.subheader("👁 EyeSauron — мониторинг экранов")
    st.caption(
        "Всевидящее Око Саурона")

    # ---- статус выбранного узла ----
    try:
        s = rpc.call('eyesauron', 'status', {}, timeout=15)
    except Exception as e:
        st.error(f"Узел не ответил: {e}")
        return
    if not isinstance(s, dict) or not s.get('ok'):
        st.error(f"Ошибка статуса: {(s or {}).get('error', 'нет данных')}")
        return

    c1, c2, c3, c4 = st.columns(4)
    c1.metric('Сервис', 'включён' if s.get('enabled') else 'ВЫКЛЮЧЕН')
    c2.metric('Коллектор', 'да' if s.get('collect') else 'нет')
    c3.metric('Агент', 'да' if s.get('capture') else 'нет')
    spool_frames = (s.get('spool') or {}).get('frames')
    c4.metric('Очередь кадров', spool_frames if spool_frames is not None else '—')

    if not s.get('enabled'):
        st.warning(
            "Сервис **отключён** на этом узле. Включите в config.yaml:\n\n"
            "```yaml\n"
            "eyesauron:\n"
            "  enabled: true      # мастер-ключ (как purge.enabled)\n"
            "  collect: true      # этот узел — коллектор архива\n"
            "  capture: false     # захват экранов этой машины\n"
            "  store_path: \\\\nas\\photo\\screens\n"
            "  collector_node: <узел-коллектор>   # для capture: true\n"
            "```\n"
            "и перезапустите узел."
        )
        return

    tab_agent, tab_archive = st.tabs(["🤖 Агент (этот узел)", "🗄 Архив кадров"])

    with tab_agent:
        _render_agent(rpc, s)
    with tab_archive:
        if s.get('store'):
            _render_store_info(s['store'], s.get('telemetry'))
        _render_archive(rpc)


def _render_store_info(store: dict, telemetry: dict | None):
    """Состояние пакованного дедуп-хранилища + телеметрия скролла."""
    with st.expander("🧱 Пакованное дедуп-хранилище", expanded=False):
        c1, c2, c3 = st.columns(3)
        c1.metric('Режим', 'packstore')
        c2.metric('Staging',
                  f"{store.get('staging_mb')} МБ / "
                  f"{store.get('staging_chunks')} чанк.")
        c3.metric('NAS', 'доступен' if store.get('nas_ok') else 'недоступен')
        st.caption(f"Локальный корень: `{store.get('root')}` · "
                   f"NAS: `{store.get('nas_root')}`")
        states = store.get('states') or {}
        if states:
            rows = [{'Состояние томов': k, 'Шт.': v}
                    for k, v in sorted(states.items())]
            st.dataframe(pd.DataFrame(rows), use_container_width=True,
                         hide_index=True)
        st.caption(str(store.get('stats') or ''))
        if telemetry:
            share = telemetry.get('scroll_pct')
            st.caption(f"👁 Телеметрия скролла ({telemetry.get('period_days')} дн.): "
                       f"{telemetry.get('scroll_frames')} скролл-кадров из "
                       f"{telemetry.get('frames')} (**{share}%**) — метрика "
                       f"решает, когда включать CDC-тома (chunker v2).")


def _render_agent(rpc, s):
    if not s.get('capture'):
        st.info(
            "Роль агента на этом узле не активна "
            "(`eyesauron.capture: true` в config.yaml + перезапуск)."
        )
        return

    col1, col2 = st.columns(2)
    col1.caption(f"Интервал захвата: **{s.get('interval_sec')} с**")
    collector = s.get('collector_node') or '— не задан (кадры копятся локально) —'
    col2.caption(f"Коллектор: **{collector}**")

    helpers = s.get('helpers') or {}
    if helpers:
        rows = [{'Сессия': sid,
                 'PID': h.get('pid'),
                 'Жив': 'да' if h.get('alive') else 'нет'}
                for sid, h in sorted(helpers.items())]
        st.dataframe(pd.DataFrame(rows), width='stretch',
                     hide_index=True)
    else:
        st.info("Активных пользовательских сессий нет — хелперы не запущены.")

    spool = s.get('spool') or {}
    oldest = spool.get('oldest_age_sec')
    age = f", старейший {oldest // 60} мин назад" if oldest else ""
    st.caption(f"Spool: {spool.get('frames', 0)} кадр(ов), "
               f"{_fmt_size(spool.get('bytes'))}{age} — `{spool.get('dir', '')}`")

    if st.button("📸 Кадр сейчас (диагностика)", key='eye_test_cap'):
        res = {'ok': False, 'error': ''}
        try:
            res = rpc.call('eyesauron', 'test_capture', {}, timeout=30)
        except Exception as e:
            res = {'ok': False, 'error': str(e)}
        st.session_state['eye_test_result'] = res
        st.rerun()

    last = st.session_state.pop('eye_test_result', None)
    if last:
        if last.get('ok'):
            st.success(f"Кадр получен: {last.get('title')} @ {last.get('timestamp')} "
                       f"({_fmt_size(last.get('size'))})")
            st.image(last.get('png'))
        else:
            st.warning(f"Прямой захват недоступен: {last.get('error')}")

    st.divider()


def _render_archive(rpc):
    try:
        hosts = rpc.call('eyesauron', 'browse',
                         {'level': 'hosts'}, timeout=20)
    except Exception as e:
        st.error(f"Узел не ответил: {e}")
        return
    if not hosts.get('ok'):
        st.error(hosts.get('error', 'архив недоступен'))
        return
    host_list = hosts.get('items') or []
    if not host_list:
        st.info("Архив пуст.")
        return

    host = st.selectbox("Хост", host_list, key='eye_host')

    dates = rpc.call('eyesauron', 'browse',
                     {'level': 'dates', 'host': host}, timeout=20)
    date_list = (dates.get('items') or [])[::-1]   # свежие даты сверху
    if not date_list:
        st.info(f"Для {host} данных нет.")
        return
    date = st.selectbox("Дата", date_list, key='eye_date')

    flt = st.text_input("Фильтр по имени кадра (подстрока)",
                        key='eye_filter').strip()

    images = rpc.call('eyesauron', 'browse',
                      {'level': 'images', 'host': host, 'date': date,
                       'filter': flt}, timeout=20)
    items = images.get('items') or []
    if not items:
        st.info("Кадров по условию нет.")
        return

    st.caption(f"Найдено: {images.get('count', len(items))}")
    rows = [{'Кадр': r['name'], 'Файл': r['file'],
             'Размер': _fmt_size(r['size'])} for r in items]
    event = st.dataframe(
        pd.DataFrame(rows),
        width='stretch', hide_index=True, height=280,
        on_select='rerun', selection_mode='single-row', key='eye_img_sel',
    )
    picked = event.selection.rows
    if not picked:
        return
    rel = items[picked[0]]['file']
    shot = rpc.call('eyesauron', 'image',
                    {'file': rel}, timeout=30)
    if shot.get('ok'):
        st.image(shot.get('png'), caption=items[picked[0]]['name'])
    else:
        st.error(shot.get('error', 'кадр недоступен'))

    with st.expander("📊 Объёмы архива"):
        st.caption("Полный обход хранилища — может занять время (NAS).")
        if st.button("Пересчитать", key='eye_stats_btn'):
            with st.spinner("Подсчёт..."):
                st.session_state['eye_stats'] = rpc.call(
                    'eyesauron', 'stats', {}, timeout=120)
            st.rerun()
    stats = st.session_state.pop('eye_stats', None)
    if stats and stats.get('ok'):
        hrows = [{'Хост': k, 'Кадров': v['files'],
                  'Объём': _fmt_size(v['bytes'])}
                 for k, v in sorted(stats.get('hosts', {}).items())]
        st.dataframe(pd.DataFrame(hrows), width='stretch',
                     hide_index=True)
        st.caption(f"Итого: {stats.get('total_files')} кадров, "
                   f"{_fmt_size(stats.get('total_bytes'))}")
