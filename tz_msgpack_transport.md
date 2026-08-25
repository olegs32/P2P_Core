# ТЗ: перевод wire-протокола P2P_Core с JSON на бинарный MessagePack

> Статус: к реализации. Блокирует: mesh-транспорт файлов (ТЗ files/updater),
> т.к. передача бинарных чанков через JSON+base64 даёт +33% объёма и лишний CPU.
> Ветвление версий: PROTOCOL_VERSION 1.0 (JSON) → 2.0 (msgpack-only, финал миграции).

---

## 1. Цель

Заменить JSON-сериализацию пакетов `MsgPack` на бинарный MessagePack во всех
WS-соединениях (node↔node, webpanel↔node), сохранив:

- совместимость в разнородной сети на время перехода (узел умеет говорить
  и по-старому, и по-новому);
- весь текущий набор PackType и семантику Router без изменений;
- возможность класть в `MsgPack.data` **байты** (главная мотивация).

Модернизация транспорт-зависимых подсистем (files-транспорт, updater)
начинается только после приёмки этого ТЗ.

## 2. Текущее состояние — карта всех точек JSON

### Ядро (обязательно к переводу)

| # | Место | Что сейчас |
|---|---|---|
| 1 | `src/networking/protocol.py` | Модель `MsgPack(BaseModel)`, `data: Any`; нет функций кодирования |
| 2 | `src/networking/transport.py:21-27` | `WebSocketTransport.send`: `model_dump_json()` / `send_json()` (FastAPI) / `send(str)` (websockets) |
| 3 | `src/networking/network.py:93` | HELLO: `websocket.receive_json()` |
| 4 | `src/networking/network.py:175` | Основной цикл: `receive_json()` |
| 5 | `src/networking/network.py:45` | `ConnectionManager.broadcast` — `send_json()` (класс помечен DEAD CODE) |
| 6 | `src/networking/node_connector.py:142` | `ws.send(hello.model_dump_json())` |
| 7 | `src/networking/node_connector.py:146` | Ответ handshake: `json.loads(raw)` |
| 8 | `src/networking/node_connector.py:101-103` | Приёмный цикл: `async for raw in ws` + `json.loads(raw)` |
| 9 | `services/webpanel/rpc_client.py:89,92` | NodeRPC HELLO: `model_dump_json` / `json.loads` |
| 10 | `services/webpanel/rpc_client.py:146,162,218` | Приёмный цикл, PING/PONG, `call()` |

### Периферия

| # | Место | Действие |
|---|---|---|
| 11 | `debug_client.py:146-237` | Dev-инструмент; перевести тем же паттерном или явно объявить legacy (не блокирует приёмку) |
| 12 | `compile.py:31-42` | hiddenimports: добавить `msgpack` (+ подпакеты при необходимости) |
| 13 | `requirements.txt` | Добавить зависимость `msgpack` |
| 14 | Служебный JSON вне протокола (`web_ui.py`: сигнатуры фильтров, аргументы RPC-консоли, st.json) | **НЕ трогать** — это не wire-протокол |

## 3. Целевой wire-формат

### 3.1 Фрейминг

- Один WebSocket **binary-frame = один msgpack-словарь** `MsgPack.model_dump()`.
- Префиксы длины не нужны: WS message-oriented.
- Ограничение размера кадра задать явно (см. §7.1).

### 3.2 Правила кодирования

```
encode:  msgpack.packb(pack.model_dump(), use_bin_type=True)
decode:  msgpack.unpackb(raw, raw=False)  →  MsgPack(**d)
```

- `use_bin_type=True` / `raw=False` — обязательная пара: `bytes` ↔ bin-type,
  строки всегда UTF-8.
- `type` (`PackType`, str-enum) пакуется своим строковым значением
  (`"stream_chunk"`); при декодировании pydantic сам приводит `str → enum`.
- В `data` разрешены типы msgpack-натуральные: dict/list/str/int/float/
  bool/None/**bytes**. Запрещено: datetime, объекты, ExtType (не использовать,
  чтобы не закрыть дверь другим реализациям клиента).
- Неопознанный `type` после декода: лог + дроп пакета, соединение НЕ рвать
  (forward-compat для будущих PackType).

### 3.3 Переговоры кодирования (переходный период)

Авторитетный механизм — **sniff первого кадра**, явное поле — для диагностики:

1. Первый кадр от клиента: `bytes` → соединение работает в msgpack;
   `str` → legacy JSON.
2. Все ответы сервера на этом соединении — в том же кодировании.
3. HELLO.data несёт `"enc": "msgpack"` — информационно (видно в логах/отладке),
   на маршрут не влияет.
4. HELLO_ACK уходит в кодировании входящего HELLO.
5. Кодирование фиксируется на соединение целиком (не попакетно).

Кто где хранит режим:
- сервер: локальная переменная в `websocket_endpoint` → передаётся в
  `WebSocketTransport(ws, encoding=...)`;
- клиент (NodeConnector, NodeRPC): определяет сам по конфигу/константе,
  хранит в объекте коннектора, использует и при отправке, и при декодировании.

## 4. Совместимость версий (матрица перехода)

| Клиент ↓ / Сервер → | Старый (JSON-only) | Новый (dual) | Будущий (mp-only) |
|---|---|---|---|
| Старый клиент (JSON) | ✅ | ✅ (sniff=str) | ❌ осознанно, с понятным HELLO_REJECT |
| Новый клиент | ✅ (по умолчанию JSON до переключения конфига) | ✅ | ✅ |
| Новый клиент (enc=msgpack) | ❌ недопустимо конфигом против старого пира | ✅ | ✅ |

Правило безопасности: клиент НЕ должен слать msgpack серверу, который о нём
не знает. До этапа 3 дефолт новых клиентов — JSON; msgpack включается
осознанно (конфиг `network.encoding`) или после подтверждения версии пира
через gossip/HELLO_ACK (`protocol_version >= 2.0`).

## 5. План работ (этапы)

### Этап 0 — подготовка (без изменения поведения)
- [ ] `requirements.txt`: `msgpack`.
- [ ] `protocol.py`: функции `encode_pack(pack) -> bytes`,
      `decode_pack(raw: bytes | str) -> MsgPack` + константа
      `MAX_FRAME_SIZE`. Вся работа с сериализацией в других модулях —
      только через эти функции (никаких прямых packb/unpackb).
- [ ] Юнит-тесты round-trip: bytes, юникод, вложенные dict/list, None,
      большие int, все значения PackType, невалидный ввод → исключение.

### Этап 1 — dual-stack (ядро)
- [ ] `WebSocketTransport`: параметр `encoding` ('msgpack'|'json'),
      метод `send()` через `encode_pack`; авто-режим убран — кодирование
      выбирает владелец соединения.
- [ ] Сервер (`network.py`): приём через `websocket.receive()` (различать
      text/bytes кадры), sniff первого кадра, далее цикл в согласованном
      формате. Ошибка декода → закрытие соединения с логом (не падение).
- [ ] `node_connector.py`: отправка/приём через encode/decode; режим из
      конфига, дефолт `'json'` (этап 1).
- [ ] `webpanel/rpc_client.py`: тот же паттерн (NodeRPC ходит в узел).
- [ ] `ConnectionManager` — DEAD CODE: удалить класс вместе с `send_json`
      (вместо миграции), либо мигрировать; решение за исполнителем,
      удаление предпочтительнее.
- [ ] HELLO/HELLO_ACK: поле `enc` в data (информационное).

### Этап 2 — включение msgpack
- [ ] Конфиг `network.encoding: json|msgpack` (дефолт пока json);
      значение читают NodeConnector и NodeRPC.
- [ ] Клиент перед включением msgpack к пиру проверяет его готовность
      (версия протокола пира ≥ 2.0-transition из HELLO/gossip); иначе —
      откат на json с warning в лог.
- [ ] Интеграционный тест: два узла в одном процессе, полный сценарий
      (HELLO, RPC, mesh-стрим из 10 000 байтовых чанков, GOSSIP, CERT_SYNC).

### Этап 3 — дефолт и чистка (отдельный релиз, после обновления сети)
- [ ] Дефолт `network.encoding: msgpack` для новых развёртываний.
- [ ] `PROTOCOL_VERSION` → `2.0`; сервер принимает оба формата, клиенты
      по умолчанию msgpack.
- [ ] Этап 4 (финал, когда в сети не осталось JSON-узлов): удалить
      JSON-ветку из decode/encode и sniff; JSON-HELLO → HELLO_REJECT
      с причиной «upgrade required».

## 6. Детали по компонентам

### protocol.py
- Только модель + encode/decode + MAX_FRAME_SIZE. Никакой логики роутинга.
- `decode_pack` валидирует в `MsgPack`; ошибки pydantic пробрасывать как есть.

### transport.py
- `WebSocketTransport(websocket, encoding='json')`.
- FastAPI-ветка: `await ws.send_bytes(encode_pack(pack))`;
  websockets-ветка: `await ws.send(encode_pack(pack))` (bytes → binary frame).
- Убрать ветку `send_json`.

### network.py (сервер)
- Замена обоих `receive_json()` на единый helper «recv_pack(connection_state)»,
  который знает текущее кодирование соединения.
- Первое чтение — через raw `receive()` c таймаутом HELLO как сейчас.
- Text-кадр при первом контакте → легаси-режим всего соединения.

### node_connector.py / rpc_client.py
- Единый helper encode/recv на сторону клиента (код почти одинаков —
  допустимо продублировать, общий базовый класс не обязателен).
- `websockets.connect(..., max_size=MAX_FRAME_SIZE)` — см. §7.1.

### compile.py / сборка
- `hiddenimports += ['msgpack', 'msgpack.fallback']`.
- Пересобрать оба бинаря; smoke-test frozen-нодой.

## 7. Подводные камни (обязательно учесть)

### 7.1 Лимиты размеров кадров
- Клиентская библиотека `websockets`: `max_size` по умолчанию **1 МБ** —
  чанки больше уронят соединение. Явно ставить `max_size` (= MAX_FRAME_SIZE,
  рекомендовано 32 МБ) в `websockets.connect()` у NodeConnector и NodeRPC.
- uvicorn: `uvicorn.Config(..., ws_max_size=MAX_FRAME_SIZE)` (по умолчанию
  16 МБ — проверить и зафиксировать явно).

### 7.2 Pydantic: режимы model_dump
- Использовать `model_dump()` (python-режим). `mode='json'` запрещён:
  он не представит `bytes`, а это ломает главный смысл миграции.
- str-Enum пакуется корректно содержимым строки; после decode pydantic
  восстанавливает enum. Проверить тестом все значения PackType.

### 7.3 Запрет ExtType/timestamp
- Не использовать ext-типы msgpack: клиенты на других языках/старые сборки
  должны уметь читать всё. Нужен timestamp — float epoch (как сейчас `ts`).

### 7.4 Ошибки декода = граница доверия
- `unpackb` на мусоре бросает исключения — оборачивать, логировать hexdump
  первых N байт, закрывать соединение. Не пытаться «перечитать как JSON»
  вне этапа sniff первого кадра.

### 7.5 Локальный shortcut не меняется
- `router.call(dst=self)` идёт мимо кодирования (executor напрямую) —
  регресс там не ожидается, но покрыть тестом.

### 7.6 debug_client.py
- После этапа 2 перестанет понимать узел в режиме msgpack. Либо перевести
  тем же паттерном (предпочтительно), либо пометить в README как legacy
  до отдельной задачи.

## 8. Тестирование

| Уровень | Что проверяем |
|---|---|
| Unit | encode/decode round-trip (все типы data, включая bytes 1Б..8МБ); невалидные кадры; все PackType |
| Integration | 2 узла in-process: HELLO→RPC→mesh-стрим 10k чанков→GOSSIP→CERT_SYNC в режиме msgpack |
| Interop | новый узел ↔ старый формат: JSON-клиент подключается к новому серверу и наоборот (матрица §4) |
| Robustness | случайные байты в кадр → чистое закрытие, узел живёт |
| Perf (замер, не gate) | стрим 100 МБ файла: throughput/CPU msgpack vs JSON+base64, зафиксировать цифры в отчёте |
| Frozen | PyInstaller-сборки: обе ноды обмениваются в msgpack-режиме |

Регресс перед приёмкой: полный ручной сценарий веб-панели (все вкладки,
включая logs-стрим и certstool) против узла в режиме msgpack.

## 9. Критерии приёмки

1. Все существующие функции работают между двумя узлами в режиме msgpack
   (RPC, стримы с ACK/backpressure, gossip, announce, CERT_SYNC, ping).
2. Разнородная сеть: новая нода и старая нода соединяются и работают
   (в согласованном JSON), смешанные пары не падают.
3. `data` может нести `bytes` end-to-end без ручного base64 (тест стримом).
4. Битый кадр не валит процесс; соединение закрывается с внятным логом.
5. Обе PyInstaller-сборки пересобраны и проходят smoke-test.
6. Документация glm.md обновлена (§10).

## 10. Документация для разработчика (glm.md)

Обязательно внести в `glm.md` в рамках этой задачи:

1. **Новый раздел «Wire-формат»** (рядом с описанием MsgPack):
   - фрейминг «1 binary WS frame = 1 msgpack dict»;
   - правила encode/decode (`encode_pack`/`decode_pack`, use_bin_type/raw);
   - какие типы допустимы в `data` (bytes — да, ExtType/datetime — нет);
   - механика переговоров (sniff первого кадра, `enc` в HELLO, режим
     фиксируется на соединение);
   - матрица совместимости и план deprecation JSON.
2. **Обновить раздел 2 (Стек)**: строка Protocol → «MessagePack (binary frames),
   JSON только как legacy в переходный период».
3. **Обновить описание MsgPack + PackType**: упомянуть encode/decode хелперы,
   MAX_FRAME_SIZE, правило «не использовать mode='json'».
4. **Раздел «Известные проблемы»**: убрать устаревшее, добавить ограничение
   переходного периода (смешанные сети говорят JSON).
5. **Конвенции для сервисов**: если метод кладёт bytes в data/чанки —
   указывать это в докстринге; UI-слой сам решает вопрос отображения
   (base64 и т.п.) уже вне протокола.

## 11. Вне рамок этого ТЗ

- Контент-транспорт файлов (манифесты, offset/resume, каталог shares) —
  следующее ТЗ после приёмки.
- Сжатие кадров, мультиплексирование нескольких потоков в одном соединении.
- Изменения формата манифестов CERT_SYNC (только перенос кодирования).
- Автообновление узлов (updater) — им и воспользуемся для раскатки этапа 3.
