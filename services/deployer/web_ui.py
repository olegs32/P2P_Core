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
    extra_args = st.text_input("Доп. аргументы для pack -e", placeholder="--onedir --noconsole", help="Проверяются whitelist, только безопасные символы")

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
            "action": action,
            "targets": targets,
            "remote_path": remote_path,
        }
        with st.spinner("Сборка..."):
            try:
                res = rpc.call("deployer", "build", payload)
            except Exception as e:
                st.error(f"build failed: {e}")
                return
        if not res.get("ok"):
            st.error(f"Build error: {res.get('error')}")
            if res.get("trace"):
                st.code(res["trace"][-1500:])
            return
        st.success(f"✅ v{res.get('version')} packer={res.get('packer')} services={res.get('services')}")
        if res.get("logs"):
            st.code("\n".join(res["logs"][-10:]))
        if action == "deploy":
            for r in res.get("deploy", []):
                if r.get("ok"):
                    st.success(f"{r['target']} → {r['remote_path']} OK")
                else:
                    st.error(f"{r['target']} FAIL: {r.get('error')}")
        else:
            st.info(f"Сохранено: dist/{res.get('version')}/ — далее можно деплоить через deploy()")

    st.divider()
    # История сборок
    st.subheader("История сборок")
    builds = status.get("builds", [])
    if builds:
        for b in builds[:5]:
            st.code(f"{b.get('version')} — {b.get('path')} — {b.get('manifest', {}).get('services', [])}")
    else:
        st.caption("Пока нет сборок в dist/")
