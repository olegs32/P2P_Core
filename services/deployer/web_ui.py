"""Deployer Web UI — open base (packer pyarmor дефолт, чекбоксы сервисов, multi-target, remote_path)."""

def render(rpc):
    try:
        import streamlit as st
    except ImportError:
        return
    st.header("🚀 Deployer — сборка и деплой")

    # Загрузка данных
    try:
        svc_info = rpc.call("deployer", "list_services", {})
        dev_info = rpc.call("deployer", "list_devices", {})
        status = rpc.call("deployer", "get_status", {})
    except Exception as e:
        st.error(f"deployer unavailable: {e}")
        return

    packers = svc_info.get("packers", ["pyarmor", "pyinstaller"])
    default_packer = svc_info.get("default_packer", "pyarmor")
    services = svc_info.get("services", [])
    depends = svc_info.get("depends", {})
    devices = dev_info.get("devices", [])

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
    names = [s["name"] for s in services]
    # multiselect для выбора
    selected = st.multiselect("Включить сервисы (зависимости подтянутся транзитивно)", names, default=names)
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

    # Действие
    action = st.radio("Действие после сборки", ["save", "deploy"], format_func=lambda x: "Сохранить версионно (dist/<ver>/)" if x=="save" else "Деплой на ноды", horizontal=True)

    targets = []
    remote_path = ""
    if action == "deploy":
        st.subheader("Цели деплоя (мультивыбор)")
        if not devices:
            st.warning("devices.txt пуст и нет живых нод в mesh — добавьте хосты в devices.txt")
        targets = st.multiselect("Целевые узлы (host / node_id)", devices, help="EXE собирается единожды, конфиги пер-нодовые")
        # remote_path из LocalConfig.full_path цели — подставляем дефолт, можно править
        # Для MVP — один шаблон для всех, поддерживает {node}
        remote_path = st.text_input("Remote path (на цели)", value=r"C:\Core\Node_P2P_Core.exe", help="Можно {node}: C:\\Core\\{node}\\Node_P2P_Core.exe, иначе один для всех")
        st.caption("Путь создастся при необходимости, запуск через psexec -s \\\\host path")

    # Build button
    if st.button("🔨 Собрать" + (" и деплоить" if action=="deploy" else ""), type="primary", use_container_width=True):
        if action == "deploy" and not targets:
            st.error("Выберите хотя бы одну цель для деплоя")
            return
        payload = {
            "packer": packer,
            "services": selected,
            "extra_args": extra_args,
            "build_webui": build_webui,
            "action": action,
            "targets": targets,
            "remote_path": remote_path,
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
            if action == "deploy":
                for r in res.get("deploy", []):
                    if r.get("ok"):
                        st.success(f"{r['target']} → {r['remote_path']} OK")
                    else:
                        st.error(f"{r['target']} FAIL: {r.get('error')}")
            else:
                st.info(f"Сохранено: dist/{res.get('version')}/ — далее можно деплоить через deploy()")
            st.session_state["deployer_last_logs"] = res.get("logs", [])

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
                    # Показать деплой если был
                    for r in (result.get("deploy") or []):
                        if r.get("ok"):
                            st.success(f"{r['target']} → {r['remote_path']} OK")
                        else:
                            st.error(f"{r['target']} FAIL: {r.get('error')}")
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

    st.divider()
    st.subheader("📦 Только деплой (без сборки)")
    st.caption("Использует уже собранный dist/<версия>/Node_P2P_Core.exe — без вызова паковщика")
    if not builds:
        st.caption("Нет сборок — сначала соберите")
    else:
        versions = [b.get("version") for b in builds if b.get("version")]
        sel_ver = st.selectbox("Версия для деплоя", versions, key="deploy_only_ver")
        deploy_targets = st.multiselect("Цели (без сборки)", devices, key="deploy_only_targets")
        deploy_rpath = st.text_input("Remote path (без сборки)", value=r"C:\Core\Node_P2P_Core.exe", key="deploy_only_rpath")
        if st.button("🚀 Деплоить без сборки", type="secondary", use_container_width=True, key="deploy_only_btn"):
            if not deploy_targets:
                st.error("Выберите цели")
            elif not sel_ver:
                st.error("Выберите версию")
            else:
                payload = {"version": sel_ver, "targets": deploy_targets, "remote_path": deploy_rpath}
                with st.spinner(f"Деплой {sel_ver} → {deploy_targets} ..."):
                    try:
                        res = rpc.call("deployer", "deploy", payload)
                    except Exception as e:
                        st.error(f"deploy failed: {e}")
                        res = None
                if res is not None:
                    if not res.get("ok"):
                        st.error(f"Deploy error: {res.get('error')}")
                    else:
                        st.success(f"✅ Деплой {sel_ver} — {res.get('note','')}")
                        for r in res.get("deploy", []):
                            if r.get("ok"):
                                st.success(f"{r['target']} → {r['remote_path']} OK")
                            else:
                                st.error(f"{r['target']} FAIL: {r.get('error')}")
