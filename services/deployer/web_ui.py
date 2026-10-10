"""Deployer Web UI — open base: сборка (packer pyarmor, чекбоксы сервисов) + генерация agent-пакетов."""

def render(rpc):
    try:
        import streamlit as st
    except ImportError:
        return
    st.header("🚀 Deployer — сборка и деплой")

    # Загрузка данных
    try:
        svc_info = rpc.call("deployer", "list_services", {})
        status = rpc.call("deployer", "get_status", {})
    except Exception as e:
        st.error(f"deployer unavailable: {e}")
        return

    packers = svc_info.get("packers", ["pyarmor", "pyinstaller"])
    default_packer = svc_info.get("default_packer", "pyarmor")
    services = svc_info.get("services", [])
    depends = svc_info.get("depends", {})

    # Packer dropdown — pyarmor дефолт
    packer = st.selectbox("Packer", packers, index=packers.index(default_packer) if default_packer in packers else 0)

    # Доп. аргументы — чекбоксы + custom
    st.subheader("Доп. аргументы для pack -e (чекбоксы)")
    PACK_EXTRAS = [
        ("--clean", "Очистка кэша"),
        ("--noconsole", "Без консоли (windowed)"),
        ("--noupx", "Отключить UPX"),
        ("--strip", "Strip символов"),
        ("--debug=all", "Отладка"),
        ("--onedir", "Папкой (onedir)"),
    ]
    cols = st.columns(3)
    _selected_extras: list[str] = []
    for i, (flag, desc) in enumerate(PACK_EXTRAS):
        with cols[i % 3]:
            if st.checkbox(flag, help=desc, key=f"pack_extra_{flag}"):
                _selected_extras.append(flag)
    _custom_extra = st.text_input("Свои аргументы (дополнительно)", placeholder="--exclude-module ...", key="pack_extra_custom")
    if _custom_extra and _custom_extra.strip():
        _selected_extras.append(_custom_extra.strip())
    extra_args = " ".join(_selected_extras)

    # Параллельная сборка Web-UI
    build_webui = st.checkbox("Собирать Web-UI NODE (параллельно)", value=True, help="Собрать WebUI_P2P_Core.exe параллельно с Node")

    # Services чекбоксы с транзитивностью
    st.subheader("Сервисы в сборке")
    # Группировка по наличию
    exclude_default = ['deployer', 'eyesauron', 'webpanel']
    names = [s["name"] for s in services ]
    defaults = [name for name in names if name not in exclude_default]
    # multiselect для выбора
    selected = st.multiselect("Включить сервисы (зависимости подтянутся транзитивно)", names, default=defaults)
    # Показать расширенный список
    if selected:
        expanded = set(selected)
        stack = list(selected)
        while stack:
            cur = stack.pop()
            for dep in depends.get(cur, []):
                if dep not in expanded:
                    expanded.add(dep)
                    stack.append(dep)
        if expanded != set(selected):
            st.caption(f"Транзитивно добавлены: {sorted(expanded - set(selected))}")
            st.caption(f"Итого в сборке: {sorted(expanded)}")

    # Build button
    if st.button("🔨 Собрать", type="primary", use_container_width=True):
        payload = {
            "packer": packer,
            "services": selected,
            "extra_args": extra_args,
            "build_webui": build_webui,
            "action": "save",
        }
        with st.spinner("Запуск сборки..."):
            try:
                # Увеличенный таймаут не нужен — build теперь фоновый и возвращается сразу
                res = rpc.call("deployer", "build", payload)
            except Exception as e:
                st.error(f"build failed: {e}")
                return
        if not res.get("ok"):
            st.error(f"Build error: {res.get('error')}")
            if res.get("trace"):
                st.code(res["trace"][-1500:])
            return
        if res.get("started"):
            st.info(f"🚀 Сборка запущена (packer={packer}) — логи ниже обновляются каждую 1с (авто) или кнопкой «Обновить»")
            st.session_state["deployer_last_logs"] = res.get("logs", [])
            st.session_state["deployer_build_started"] = True
        else:
            st.success(f"✅ v{res.get('version')} packer={res.get('packer')} services={res.get('services')}")
            if res.get("logs"):
                st.code("\n".join(res["logs"][-20:]))
            st.info(f"Сохранено: dist/{res.get('version')}/ — далее можно сгенерировать пакеты ниже")
            st.session_state["deployer_last_logs"] = res.get("logs", [])

    st.divider()
    st.subheader("📦 Генерация agent-пакетов")
    info = rpc.call("deployer", "agent_status", {}) or {}
    devices = rpc.call("deployer", "list_devices", {}) or {}
    builds = (rpc.call("deployer", "get_status", {}) or {}).get("builds") or []

    cols = st.columns(2)
    with cols[0]:
        targets = st.multiselect("Целевые ноды",
                                 options=devices.get("devices") or devices.get("list") or [],
                                 key="agp_targets")
        versions = [b.get("version") for b in builds] or [""]
        version = st.selectbox("Версия", versions, key="agp_version")
    with cols[1]:
        # порт: admin_port из agent_status, иначе парсим admin_ws, фоллбэк 9000
        try:
            admin_port = int(info.get("admin_port")
                             or str(info.get("admin_ws") or "").rsplit(":", 1)[1])
        except (IndexError, ValueError):
            admin_port = 9000
        sel_node = st.session_state.get("selected_node") or info.get("admin_node") or ""
        admin_ws = st.text_input("Адрес админа (admin_ws)",
                                 value=f"ws://{sel_node}:{admin_port}",
                                 help="ws://<имя ноды>:<port> — поправьте, если агент в другой подсети")
        # default только при первом создании — пользовательский ввод не затирается
        st.session_state.setdefault("agp_admin_node",
                                    st.session_state.get("selected_node") or "")
        admin_node = st.text_input("Имя админ-ноды (admin_node)",
                                   key="agp_admin_node",
                                   help="HELLO dst: должен совпасть с NODE/alias сервера")
        st.caption(f"режим сборки: `{info.get('mode')}`")

    col_a, col_b = st.columns(2)
    if col_a.button("📦 Сгенерировать пакеты", type="primary",
                    disabled=not targets, key="agp_gen"):
        res = rpc.call("deployer", "gen_agent_package",
                       {"targets": targets, "version": version,
                        "admin_ws": admin_ws, "admin_node": admin_node})
        st.session_state["agp_result"] = res
    if col_b.button("🔁 Rotate ключа", key="agp_rotate"):
        rotated = []
        for t in targets:
            r = rpc.call("deployer", "rotate_agent_key", {"node": t})
            if (r or {}).get("ok"):
                rotated.append(t)
        st.session_state["agp_rotate"] = rotated

    res = st.session_state.get("agp_result")
    if res:
        if not res.get("ok"):
            st.error(res.get("error"))
        else:
            def _show_delivery(p, d):
                """Карточка пакета: статус авто-доставки либо ручная инструкция."""
                node = p["node"]
                if d and d.get("ok"):
                    mode = ("локально" if d.get("local")
                            else f"psexec code {d.get('psexec_code')}")
                    st.success(f"**{node}** → `{p['path']}` · ✅ доставлено ({mode})")
                    st.caption(f"ключ: {p['key_status']}")
                elif d:
                    st.warning(f"**{node}** → `{p['path']}` · "
                               f"⚠ доставка не выполнена: {d.get('error')}")
                    st.caption(f"ключ: {p['key_status']} · Доставка: положите папку "
                               "на цель любым каналом (SMB/USB/вручную) и запустите "
                               "`agent.exe`.")
                else:
                    # старый ответ без поля delivery — не падаем
                    st.success(
                        f"**{node}** → `{p['path']}`  (ключ: {p['key_status']})\n\n"
                        "Доставка: положите папку на цель любым каналом (SMB/USB/вручную) "
                        "и запустите `agent.exe`.")

            for p in res.get("packages") or []:
                node = p["node"]
                # результат повторной доставки переживает ререны, иначе — из gen
                d = st.session_state.get(f"agp_deliv_{node}") or p.get("delivery")
                _show_delivery(p, d)
                if d and not d.get("ok"):
                    if st.button("🔁 Повторить доставку",
                                 key=f"agp_redeliver_{node}"):
                        try:
                            r = rpc.call("deployer", "deliver_agent",
                                         {"node": node, "version": version}) or \
                                {"ok": False, "error": "нет ответа от узла"}
                        except Exception as e:
                            r = {"ok": False, "error": str(e)}
                        st.session_state[f"agp_deliv_{node}"] = r
                        _show_delivery(p, r)   # показ в том же реренере

    @st.fragment(run_every=st.session_state.get("agp_live", 2))
    def _agent_live():
        st.subheader("📡 Прогресс деплоев")
        info = rpc.call("deployer", "agent_status", {}) or {}
        statuses = info.get("statuses") or {}
        keys = info.get("keys") or {}
        if not statuses:
            st.caption("отчётов пока нет")
        for node, s in statuses.items():
            mark = "✅" if s.get("ok") and s.get("stage") == "done" else "⏳"
            st.markdown(f"{mark} **{node}** — `{s.get('stage')}` "
                        f"{('⚠ ' + str(s.get('error'))) if s.get('error') else ''}"
                        f"{' · ⚠ ' + str(s.get('warning')) if s.get('warning') else ''}"
                        f" · ключ: {'created' if node in keys else '?'}")
            with st.expander("история стадий"):
                st.json(s.get("history") or [])
        det = rpc.call("system", "node_detail", {}) or {}
        connected = {n.get("node_id") for n in det.get("connected") or [] if isinstance(n, dict)}
        for node in statuses:
            if node in connected:
                st.markdown(f"✅ `{node}` — online (в connected на админе)")
    _agent_live()

    st.divider()
    # Логи паковщика — чекбокс автообновления 1с + ручная кнопка
    st.subheader("📜 Логи паковщика")
    auto = st.checkbox("Автообновление логов (каждую 1с)", value=True, key="deployer_log_auto")
    col1, col2 = st.columns([1, 4])
    with col1:
        if st.button("🔄 Обновить", key="deployer_log_manual"):
            st.rerun()
    with col2:
        st.caption("Авто — fragment 1с, ручная — кнопка")

    def _render_logs():
        try:
            st2 = rpc.call("deployer", "build_status", {})
            state = st2.get("state", "idle")
            logs = st2.get("logs") or []
            result = st2.get("result")
            # Fallback на last_build после смены вкладки / завершения
            if not logs:
                st3 = rpc.call("deployer", "get_status", {})
                last = st3.get("last_build") or {}
                logs = last.get("logs") or st.session_state.get("deployer_last_logs", [])
                result = result or last
                if last.get("version"):
                    st.caption(f"Последняя: {last.get('version')} webui={last.get('webui_exe') or '—'} state={state}")
            else:
                if st2.get("version"):
                    st.caption(f"Сборка: {st2.get('version')} state={state}")
            if logs:
                st.code("\n".join(logs[-100:]), language="text")
                if state == "running":
                    st.caption("⏳ Сборка идёт — логи обновляются...")
                elif state == "failed":
                    st.error(f"Сборка failed: {(result or {}).get('error','')}")
                elif state == "done" and result:
                    if result.get("version"):
                        st.success(f"✅ Готово v{result.get('version')} packer={result.get('packer')}")
            else:
                st.caption("Логов нет — выполните сборку")
        except Exception as e:
            st.caption(f"logs unavailable: {e}")

    if auto:
        @st.fragment(run_every=1)
        def _auto_logs():
            _render_logs()
        _auto_logs()
    else:
        _render_logs()

    st.divider()
    # История сборок
    st.subheader("История сборок")
    builds = status.get("builds", [])
    if builds:
        for b in builds[:5]:
            st.code(f"{b.get('version')} — {b.get('path')} — {b.get('manifest', {}).get('services', [])}")
    else:
        st.caption("Пока нет сборок в dist/")
