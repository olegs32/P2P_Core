# services/logs/web_ui.py
# =============================================================================
#  Вкладка «Логи» в веб-панели.
#
#  Устройство:
#    * фильтры (severity, логгер, поиск/regex, период, лимит) — вне фрагмента,
#      их изменение перезапускает страницу и сбрасывает накопленную ленту;
#    * сама лента — st.fragment(run_every=2s) при включённом тумблере
#      «Автообновление»: каждые 2 секунды забирается только дельта
#      (since_id = id последней полученной записи);
#    * смена любого фильтра меняет «сигнатуру» (lv_sig) — лента копится заново;
#    * экспорт — кнопки download под таблицей (CSV/TXT по отфильтрованной
#      ленте, которая лежит в session_state.lv_rows).
# =============================================================================

try:
    import streamlit as st
except ImportError:
    # headless-сборка (Node_P2P_Core.exe): streamlit не установлен
    st = None

if st is not None:

    import json
    import time

    import pandas as pd

    LEVELS = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
    LEVEL_ICON = {
        'DEBUG': '🔵', 'INFO': '🟢', 'WARNING': '🟡',
        'ERROR': '🔴', 'CRITICAL': '💥',
    }
    DEFAULT_LEVELS = ['INFO', 'WARNING', 'ERROR', 'CRITICAL']
    MAX_ROWS = 2500          # сколько записей держать в ленте панели

    def render(rpc):
        st.header("📜 Логи узла")
        st.caption(
            "Кольцевой буфер консоли выбранного узла (RPC `logs.get_logs`). "
            "Буфер видит записи, доходящие до root logger — уровень задаёт "
            "`logging.level` в config.yaml."
        )

        # ------------------------- фильтры ------------------------------ #
        top = st.columns([1.2, 1.5, 1.5])
        levels = top[0].multiselect("Severity", LEVELS,
                                    default=DEFAULT_LEVELS, key="lv_levels")
        search = top[1].text_input("Содержит текст",
                                   key="lv_search",
                                   placeholder="подстрока...")
        regex_src = top[2].text_input("Regex (приоритетнее поиска)",
                                      key="lv_regex",
                                      placeholder=r"напр. Node\d+ error")

        bottom = st.columns([1.5, 1, 1, 1])
        known_loggers = _fetch_loggers(rpc)
        logger_pick = bottom[0].selectbox(
            "Логгер", ["(все)"] + known_loggers, key="lv_logger")
        minutes = bottom[1].select_slider(
            "Период", options=[0, 1, 5, 15, 60, 240],
            format_func=lambda m: "весь буфер" if m == 0 else f"{m} мин",
            key="lv_minutes")
        limit = bottom[2].slider("Записей за загрузку",
                                 100, 2000, 500, step=100, key="lv_limit")
        auto = bottom[3].toggle("Автообновление (2 сек)", key="lv_auto")

        act = st.columns([1, 1, 4])
        if act[0].button("🔄 Обновить сейчас", key="lv_refresh"):
            st.rerun()
        if act[1].button("🗑 Очистить буфер узла", key="lv_clear"):
            try:
                rpc.call("logs", "clear_buffer", {})
                _reset_tape()
                st.toast("Буфер узла очищен")
            except Exception as e:
                st.error(f"Ошибка очистки: {e}")

        # сигнатура фильтров: изменилась — начинаем копить ленту заново
        sig = json.dumps(
            [sorted(levels), logger_pick, search, regex_src, minutes, limit],
            sort_keys=True)

        params = {
            "levels": levels or None,
            "loggers": [logger_pick] if logger_pick != "(все)" else None,
            "search": search or None,
            "regex": regex_src or None,
            "limit": int(limit),
            "since_ts": (time.time() - minutes * 60) if minutes else None,
        }

        # ------------------------- лента -------------------------------- #
        if auto:
            @st.fragment(run_every="2s")
            def tape():
                _draw_tape(rpc, sig, params)
        else:
            @st.fragment
            def tape():
                _draw_tape(rpc, sig, params)

        tape()

        # ------------------------ экспорт ------------------------------- #
        rows = st.session_state.get("lv_rows") or []
        if rows:
            left, right, _ = st.columns([1, 1, 4])
            node = st.session_state.get("selected_node", "node")
            stamp = time.strftime("%Y%m%d_%H%M%S")
            df = pd.DataFrame(rows)
            left.download_button(
                "⬇ CSV", data=df.to_csv(index=False).encode("utf-8-sig"),
                file_name=f"logs_{node}_{stamp}.csv", key="lv_dl_csv")
            txt = "\n".join(
                f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(r['ts']))} "
                f"[{r['level']}] {r['logger']}: {r['msg']}" for r in reversed(rows))
            right.download_button(
                "⬇ TXT", data=txt.encode("utf-8"),
                file_name=f"logs_{node}_{stamp}.log", key="lv_dl_txt")

    # ------------------------------------------------------------------ #
    #  Внутренности
    # ------------------------------------------------------------------ #

    def _reset_tape():
        st.session_state["lv_rows"] = []
        st.session_state["lv_last_id"] = 0

    def _fetch_loggers(rpc):
        cached = st.session_state.get("lv_loggers")
        if cached is not None:
            return cached
        try:
            res = rpc.call("logs", "get_loggers", {}) or {}
            names = res.get("loggers", []) if res.get("ok") else []
        except Exception:
            names = []
        st.session_state["lv_loggers"] = names
        return names

    def _draw_tape(rpc, sig, params):
        # смена фильтров → копим ленту заново
        if st.session_state.get("lv_sig") != sig:
            st.session_state["lv_sig"] = sig
            _reset_tape()

        try:
            res = rpc.call("logs", "get_logs",
                           {**params, "since_id": st.session_state.get("lv_last_id", 0)})
        except Exception as e:
            st.error(f"Ошибка получения логов: {e}")
            return

        if not isinstance(res, dict) or not res.get("ok"):
            st.error(f"Узел ответил ошибкой: {(res or {}).get('error', 'нет данных')}")
            return

        new_entries = res.get("entries", [])
        if new_entries:
            # entries старые→новые; лента хранится новые→старые:
            # свежая порция разворачивается и встаёт поверх старых записей
            tape_rows = st.session_state.get("lv_rows") or []
            tape_rows = (list(reversed(new_entries)) + tape_rows)[:MAX_ROWS]
            st.session_state["lv_rows"] = tape_rows

        st.session_state["lv_last_id"] = res.get("last_id", 0)

        rows = st.session_state.get("lv_rows") or []
        info = st.columns(2)
        info[0].caption(f"В буфере узла: {res.get('buffer_size', '?')}")
        info[1].caption(f"Показано: {len(rows)}"
                        f" (совпало с фильтром за загрузку:"
                        f" {res.get('total_matched', '?')})")
        if res.get("gap"):
            info[2].caption("⚠ Часть записей вытеснилась из буфера между опросами")

        if not rows:
            st.info("Нет записей по заданным фильтрам")
            return

        df = pd.DataFrame([{
            "время": time.strftime("%H:%M:%S", time.localtime(r["ts"]))
                     + f".{int(r['ts'] * 1000) % 1000:03d}",
            "уровень": f"{LEVEL_ICON.get(r['level'], '▫')} {r['level']}",
            "логгер": r["logger"],
            "сообщение": r["msg"],
        } for r in rows])

        st.dataframe(
            df,
            width='stretch', hide_index=True, height=420,
            column_config={
                "время": st.column_config.TextColumn(width="small"),
                "уровень": st.column_config.TextColumn(width="small"),
                "логгер": st.column_config.TextColumn(width="small"),
                "сообщение": st.column_config.TextColumn(width="large"),
            },
        )
