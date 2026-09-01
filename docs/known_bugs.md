- config:
  - auto restart on config update - как при update
  - сделать подсветку синтаксиса в UI
- network map: map goes move to border canvas without centered UI node, i had see nothing


- mesh
  - при указании локального узла: 2026-08-27 11:04:20 [Router] ERROR: [mesh] no route to sysadmin-pc label=05dca1f6
  - так же с удаленного: no route to sysadmin-pc
    - возможно нода ищется по IP вместо имени, но file_transport работает при этом
- webpanel:
  - в главной странице при выборе другой ноды без UI Ошибка получения данных: Method not found: webpanel.node_status
- eye_sauron: 
  - [eyesauron] INFO: отключён (config.yaml → eyesauron.enabled: true), RPC отвечают отказом
  - в архив кадров нужно добавить возможность просмотра по отфильтрованным элементам в выводимой таблице в виде галереи, текущий просмотр очень неудобен

- lex reverse dial: [РЕШЕНО 2026-09-01]
  - keep inbound + parallel reverse реализован (NetworkModule.websocket_endpoint -> accept inbound, parallel dial по host/port из HELLO; при успехе outbound закрываем inbound, при ошибке оставляем inbound). NodeConnector всегда dial. Все успешные outbound сохраняются в config даже против lex. NAT: mesh может быть в серой сети — проверка по факту попыткой достучаться (упор на маршрутизацию), keep inbound покрывает. См. src/networking/network.py: _lex_reverse_keep_inbound.
