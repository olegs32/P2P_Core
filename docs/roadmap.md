# TODO

- mesh 
  - autobuilding network
  - what is: [Router] WARNING: Swept 1 stale ws_pending entries (no RESPONSE within 180s)

- create network control panel via politics as main page
  - design control panel
    - firewall control UI
      - show all connections: local + ECDH 
  - design arch politics
    - design execution service control on endpoints - использовать rpc настроек
      - centralize configurator

- safe deployments
  - no drop certs
    - make initial temp crypto storage, push storage + binary to remote
    - initial decrypted by hardcode password, store in main storage
    - remove temp deployment stor
  - base64 built in initial py conf file vs unlinked stor?
  - deploy agent - to get required data
    - request pre-config for node
    - download node, run, check
    - self-terminating
- distribution compute
  - ui
  - backend
- hoster projects/games
  - notificator service
    - via VK_bot
- RPC client secure 
  - store cert to connect
  - firewall controlled calls
- cloud log storage
  - speedtest persistent log
  - cloud database?)
  - base for messenger?!)
- certstool
  - not only direct connected certs, join from gossip adv

- ### exotik:
  - emulate SMB share from files