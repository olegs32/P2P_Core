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

    # Вариант A: пакет без key material; SE-админ принимает агента
    # на bootstrap-порту (agent_ws), certs — только в pre_config.
    st.caption(f"режим: `{info.get('mode')}` · агент идёт на `{info.get('agent_ws') or info.get('admin_ws') or '?'}`")

    st.subheader("🔨 Сборка агента")
    # Пакет встраивает КОПИЮ dist/DeployAgent.exe на момент генерации —
    # после правок services/deployer/agent/* пересоберите агента здесь,
    # затем сгенерируйте пакеты заново (retry развозит старый пакет)
    _mode = info.get("mode") or "open"
    _axe = info.get("agent_exe") or {}
    if _axe.get("exists"):
        st.caption(f"Сейчас в dist: `{_axe.get('name')}` "
                   f"{(_axe.get('size') or 0) / 1048576:.1f} МБ")
    else:
        st.warning(f"Агент не собран ({_axe.get('name') or 'DeployAgent.exe'}) — "
                   "генерация пакетов невозможна")
    _flavors = ["open", "se"]
    _flavor = st.selectbox("Flavor агента", _flavors,
                           index=_flavors.index(_mode) if _mode in _flavors else 0,
                           key="agp_agent_flavor",
                           help=f"Должен совпадать с режимом админа (сейчас: {_mode})")
    if st.button("🔨 Собрать агента", type="primary", key="agp_build_agent"):
        try:
            _br = rpc.call("deployer", "build_agent_exe",
                           {"flavor": _flavor}) or {}
        except Exception as e:
            _br = {"ok": False, "error": str(e)}
        st.session_state["agp_agent_build"] = _br
    _br = st.session_state.get("agp_agent_build")
    if _br:
        if not _br.get("ok"):
            st.error(_br.get("error"))
        elif _br.get("started"):
            st.info(f"🚀 Сборка агента ({_br.get('flavor')}) запущена — "
                    "статус ниже в «Прогрессе деплоев»")

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
                                 value=info.get("agent_ws") or f"ws://{sel_node}:{admin_port}",
                                 help="SE: bootstrap-порт без mTLS (agent_ws); open: mesh-порт. Поправьте, если агент в другой подсети")
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
        st.session_state["agp_rotate_result"] = rotated

    _rotated = st.session_state.get("agp_rotate_result")
    if _rotated:
        st.success(f"🔁 Ключ ротирован для: {', '.join(_rotated)}")

    res = st.session_state.get("agp_result")
    if res:
        if not res.get("ok"):
            st.error(res.get("error"))
        else:
            # живые записи доставки — info уже получен выше (agent_status);
            # карточка рендерится ОДИН раз, live-подробности и retry-кнопка
            # во фрагменте _agent_live ниже (урок прошлого ревью)
            live_deliv = info.get("deliveries") or {}

            def _show_delivery(p):
                """Карточка пакета: путь/ключ + маркер состояния доставки."""
                node = p["node"]
                d = live_deliv.get(node) or p.get("delivery")
                head = f"**{node}** → `{p['path']}` (ключ: {p['key_status']})"
                state = d.get("state") if isinstance(d, dict) else None
                if state in ("queued", "running"):
                    # async-контракт gen (Task 15): доставка ушла в фон
                    st.markdown(f"{head} · ⏳ в очереди на доставку")
                elif state == "done":
                    rd = d.get("result") or {}
                    mode = ("локально" if rd.get("local")
                            else f"psexec code {rd.get('psexec_code')}")
                    st.success(f"{head} · ✅ доставлено ({mode})")
                elif state == "error":
                    st.warning(f"{head} · ❌ доставка не выполнена — повтор "
                               "и логи ниже, в «Прогресс деплоев»")
                elif isinstance(d, dict) and "ok" in d:
                    # старый ответ (полный результат доставки) — прежняя семантика
                    if d.get("ok"):
                        mode = ("локально" if d.get("local")
                                else f"psexec code {d.get('psexec_code')}")
                        st.success(f"{head} · ✅ доставлено ({mode})")
                    else:
                        st.warning(f"{head} · ⚠ доставка не выполнена: "
                                   f"{d.get('error')}")
                        st.caption("Доставка: положите папку на цель любым каналом "
                                   "(SMB/USB/вручную) и запустите `agent.exe`.")
                else:
                    # старый ответ без поля delivery — не падаем
                    st.markdown(
                        f"{head}\n\n"
                        "Доставка: положите папку на цель любым каналом "
                        "(SMB/USB/вручную) и запустите `agent.exe`.")

            for p in res.get("packages") or []:
                _show_delivery(p)
            # карточка рендерится один раз и может отставать от живого
            # фрагмента до полного rerun'а — статус смотри ниже
            st.caption("Статус — в секции «Доставка пакетов» ниже")

    @st.fragment(run_every=st.session_state.get("agp_live", 2))
    def _agent_live():
        st.subheader("📡 Прогресс деплоев")
        info = rpc.call("deployer", "agent_status", {}) or {}
        statuses = info.get("statuses") or {}
        keys = info.get("keys") or {}
        # Живой статус фоновой доставки пакетов (Task 15): state + последняя
        # строка лога + retry при ошибке (сервер держит guard от двойного запуска)
        deliveries = info.get("deliveries") or {}
        if deliveries:
            st.markdown("**Доставка пакетов:**")
            for node in sorted(deliveries):
                d = deliveries.get(node) or {}
                state = d.get("state")
                logs = d.get("logs") or []
                last = logs[-1] if logs else ""
                rd = d.get("result") or {}
                if state in ("queued", "running"):
                    st.markdown(f"⏳ **{node}** — `{state}`"
                                + (f" · `{last}`" if last else ""))
                elif state == "done":
                    if rd.get("local"):
                        mode = "локально"
                    elif rd.get("remote_pid"):
                        # -d: errorlevel psexec = PID процесса, не код ошибки
                        mode = f"pid {rd['remote_pid']}"
                    else:
                        mode = f"psexec code {rd.get('psexec_code')}"
                    st.markdown(f"✅ **{node}** — доставлено ({mode})")
                else:
                    err = rd.get("error") or d.get("error") or "неизвестная ошибка"
                    st.markdown(f"❌ **{node}** — ошибка: {err}")
                    st.caption("Положите папку пакета на цель любым каналом "
                               "(SMB/USB/вручную) и запустите `agent.exe`.")
                    with st.expander("📜 полный лог доставки"):
                        st.text("\n".join(logs) if logs else "логов нет")
                        if rd.get("psexec_stdout"):
                            st.markdown("**psexec stdout:**")
                            st.code(rd["psexec_stdout"], language="text")
                        if rd.get("psexec_stderr"):
                            st.markdown("**psexec stderr:**")
                            st.code(rd["psexec_stderr"], language="text")
                    if st.button("🔁 Повторить доставку",
                                 key=f"agp_retry_{node}"):
                        try:
                            # версия — из записи доставки (не из selectbox):
                            # смена дропдауна не должна доставлять другую версию
                            r = rpc.call("deployer", "deliver_agent",
                                         {"node": node,
                                          "version": d.get("version")
                                          or (res or {}).get("version")}) or {}
                        except Exception as e:
                            r = {"ok": False, "error": str(e)}
                        if r.get("ok"):
                            # мгновенный started — копирование ушло в фон
                            st.info(f"🚀 {node}: доставка поставлена в очередь "
                                    "— статус обновится в этом блоке")
                        else:
                            # двойной клик -> серверный D9 «уже выполняется»
                            st.info(f"ℹ️ {r.get('error') or 'нет ответа от узла'}")
        ab = info.get("agent_build") or {}
        if ab.get("state") not in (None, "idle"):
            st.markdown("**Сборка агента:**")
            _abl = ab.get("logs") or []
            if ab.get("state") == "running":
                st.markdown(f"⏳ `{_abl[-1] if _abl else 'running'}`")
            elif ab.get("state") == "done":
                _ar = ab.get("result") or {}
                st.markdown(f"✅ `{_ar.get('exe', 'agent')}` "
                            f"{(_ar.get('size') or 0) / 1048576:.1f} МБ")
            else:
                _ab_err = ((ab.get("result") or {}).get("error")
                           or ab.get("error") or "failed")
                st.markdown(f"❌ сборка агента: {_ab_err}")
                if _abl:
                    with st.expander("лог сборки агента"):
                        st.code("\n".join(_abl[-50:]), language="text")
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
