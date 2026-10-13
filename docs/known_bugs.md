

- webpanel:
  - в главной странице при выборе другой ноды без UI Ошибка получения данных: Method not found: webpanel.node_status
- eye_sauron: 
  - [eyesauron] INFO: отключён (config.yaml → eyesauron.enabled: true), RPC отвечают отказом
  - в архив кадров нужно добавить возможность просмотра по отфильтрованным элементам в выводимой таблице в виде галереи, текущий просмотр очень неудобен

- deploy:

    - [fixed 2026-10-12] обрезка логов доставки [:300] + скрытый psexec code: _deliver_agent хранит полный stdout/stderr (psexec_stdout/psexec_stderr), UI — expander «полный лог доставки»
    - [fixed 2026-10-12] psexec -d возвращает в errorlevel PID («with process ID N»), а не код ошибки: «started on» в stdout = успех при любом returncode (remote_pid парсится, done показывает pid, не «psexec code»); мёртвая гипотеза «гонка exit-кода» и _AGENT_EXIT_CODES удалены
    - [fixed 2026-10-12] agent «connect failed: ...» без адреса: connect() теперь показывает полный URI + тип исключения + INFO-строку connecting to (иначе exit=11 недиагностируем)
    - [superseded 2026-10-12 → вариант A] open-агент против SE-админа: mTLS-triple в пакете УДАЛЁН (это была та же дыра — plaintext ключи на цели, ради закрытия которой создавался агент). Вместо triple — bootstrap-приёмник SE без клиентского серта (отдельный порт mesh+100 / se.yaml agent_port, только deployer.agent_api, авторизация HMAC); пакет = agent.conf + agent.exe; жертве в peers пишется mesh-порт
    - [fixed 2026-10-12] нет кнопки пересборки агента в UI (приходилось вручную `compile.py --type agent`, иначе пакет везёт stale-копию): RPC build_agent_exe (фон, guard с _build_lock против параллельного PyInstaller) + agent_build_status + agent_build/agent_exe в agent_status; UI — «🔨 Сборка агента» с flavor и live-статусом в «Прогрессе деплоев»
    - [fixed 2026-10-12] agent exit=11 к wss-админу (SSLCertVerificationError: Missing Authority Key Identifier): self-signed mesh PKI не проходит дефолтную верификацию websockets — connect() передаёт bootstrap SSL-контекст без верификации (личность админа проверяет HMAC-конверт, прецедент — webpanel rpc_client fallback). Требует: пересборка агента (кнопка «🔨 Собрать агента») → regen пакетов → redeliver
    - [removed 2026-10-12 → вариант A] triple-диагностика (deployer_class/se_ca/tls в карточке пакета) удалена вместе с triple
    - [closed 2026-10-12 → вариант A] gen без PEM/exit=11: больше не баг отсутствия triple, а неверный порт — агент должен идти на agent_ws (bootstrap, wss без mTLS),(targets regen с новым agent.conf; доставка старого пакета = redeliver)
    - [fixed 2026-10-12] bootstrap HTTP 403 (InvalidStatus): endpoint делал проверки ДО accept(), а Starlette первым отдаёт websocket.connect без bytes → close до accept uvicorn превращает в HTTP 403 (websockets_impl: initial_response=FORBIDDEN), transport.send до accept — RuntimeError. Фикс: accept() строго первым (как mesh-эндпоинт), проверки после; регресс-тесты handshake во src/se/tests/test_agent_bootstrap.py; агент при InvalidStatus теперь пишет http-статус+тело ответа (_http_status_detail)
    - [fixed 2026-10-13] bootstrap HTTP 403, часть 2: у agent_endpoint не было аннотации `websocket: WebSocket` (в отличие от mesh) → FastAPI считал его query-параметром → WebSocketRequestValidationError до accept → тот же пустой 403, молча (диагностика: curl GET / → наш FastAPI 404, а WS-upgrade → 403/empty). Фикс: аннотация + e2e-тест handshake через TestClient (без аннотации падает тем же WebSocketDisconnect, проверено)
    - [retracted 2026-10-12] гипотеза «порт 9000 держит сквоттер» — НЕВЕРНА (ошибка диагностики: смотрел только open network.py, а админ — SE: SecureNetworkModule wss-only mTLS; plain HTTP он роняет молча по дизайну, серт CN=sysadmin-pc — его собственный). Урок: preflight `banner='<empty>'` + tls_cert с валидным CN админа = mTLS, а не чужак
    - Сборка failed: agent artifact not found: DeployAgent_se.exe — соберите: python compile.py --type agent. Даже после ручной сборки агента
      - автоматическая сборка должна автоматически собирать деплой агента
    - _dummy_build() -?
      - if os.environ.get("P2P_DUMMY_BUILD") == "1": - удалить  
    - Параллельная сборка Web-UI NODE если галка - собирается последовательно, баг
    - \# exe_src теперь указывает на версионный exe - удалить
    - custom Exception to exceptions  
    - async def agent_status(self, data: dict) -> dict: - в возврате жестко прописана схема ws://
    - добавление флагов не работает должным образом из UI, жестко прописанные флаги для сборки
    - так же не меняется замена packer по факту

- netinfo:
  - вкладка Узлы содержит список узлов без какой либо дополнительной информации, малоинформативная вкладка
    - к тому же - только одна webpanel локально подключенная

- system:
  - вкладку подключение нужно перенести в netinfo (более логично)
    - здесь же поправить: при написании хостнейма поле Node ID удалённого узла должно подтягивать значение хост, но быть независимым с возможностью редактирования
  - и управление узлами туда же

- certstool
  - сертификаты с других узлов не отображаются на разных версиях через mesh

 - speedtest
   - режим по умолчанию bidirectional


- Главная:
  - вместо списка сервисов создать информативный дашборд, отображающий данные сети
