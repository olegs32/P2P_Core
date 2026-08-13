# debug_client.py — интерактивный клиент с ASCII-панелью сети и сервисами
# Кроссплатформенная версия (без curses, через msvcrt/win32 console)
import asyncio
import json
import time
import uuid
import sys
import os
import threading
import websockets
from src.networking.protocol import MsgPack, PackType
from src.networking.neighbor_table import PROTOCOL_VERSION

# ------------------------------------------------------------------
# Windows console setup — enable ANSI escape sequences
# ------------------------------------------------------------------
if sys.platform == "win32":
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
    except Exception:
        pass  # fallback: ANSI may still work on modern Windows

# ------------------------------------------------------------------
# Configuration
# ------------------------------------------------------------------
URI = "ws://localhost:9000/ws/DebugClient"
OWN_NODE = "DebugClient"
DST_NODE = "Node0"

# ------------------------------------------------------------------
# Terminal helpers
# ------------------------------------------------------------------
# ANSI escape codes
CLEAR = "\033[2J"
HOME = "\033[H"
SHOW_CURSOR = "\033[?25h"
HIDE_CURSOR = "\033[?25l"
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
REVERSE = "\033[7m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
CYAN = "\033[36m"
WHITE_ON_BLUE = "\033[37;44m"

# Box-drawing characters
TL = "┌"  # top-left
TR = "┐"  # top-right
BL = "└"  # bottom-left
BR = "┘"  # bottom-right
TM = "┬"  # top-middle
BM = "┴"  # bottom-middle
LM = "├"  # left-middle
RM = "┤"  # right-middle
H = "─"   # horizontal
V = "│"   # vertical
MM = "┼"  # middle-middle


def get_terminal_size():
    """Get terminal size cross-platform."""
    try:
        cols, rows = os.get_terminal_size()
        return cols, rows
    except Exception:
        return 80, 24


def goto(x, y):
    """Move cursor to (x, y) — 1-based."""
    return f"\033[{y};{x}H"


def box_title(title, width):
    """Center a title within a box top-border."""
    if len(title) >= width - 4:
        return TL + H * (width - 2) + TR
    padding = width - 4 - len(title)
    left = padding // 2
    right = padding - left
    return TL + H * left + f" {title} " + H * right + TR


def draw_box(top, left, height, width, title=""):
    """Return string that draws a box at (left, top) with given size."""
    lines = []
    # Top border with title
    top_line = box_title(title, width)
    lines.append(goto(left + 1, top + 1) + top_line)
    # Side borders
    for r in range(1, height - 1):
        lines.append(goto(left + 1, top + 1 + r) + V + " " * (width - 2) + V)
    if height > 1:
        # Bottom border
        lines.append(goto(left + 1, top + height) + BL + H * (width - 2) + BR)
    return "".join(lines)


def put_text(x, y, text, style="", max_width=0):
    """Return string to place text at (x, y)."""
    if max_width > 0 and len(text) > max_width:
        text = text[: max_width - 1]
    return goto(x + 1, y + 1) + style + text + RESET


def clear_line(y, width):
    """Clear a line and return the escape string."""
    return goto(1, y + 1) + " " * width + goto(1, y + 1)


def format_neighbor(n: dict, max_width: int = 0) -> str:
    # FIX: Removed trailing spaces in .get() keys
    node_id = n.get("node_id", "?")[:12]
    host = n.get("host", "?")[:15]
    port = str(n.get("port", "?"))
    status = n.get("status", "?")[:12]
    via = n.get("via", "")[:12]
    color = GREEN if status == "CONNECTED" else YELLOW
    text = f"  {node_id:<12} {host:>15}:{port:<6} {status:<12} via={via}"
    if max_width > 0:
        text = text[:max_width]
    return color + text + RESET


# ------------------------------------------------------------------
# Networking helpers
# ------------------------------------------------------------------
async def do_handshake(websocket) -> bool:
    hello = MsgPack(
        type=PackType.HELLO,
        source=OWN_NODE,
        dst=DST_NODE,
        data={
            "node_id": OWN_NODE,
            "host": "localhost",
            "port": 0,
            "version": PROTOCOL_VERSION,
            "session_id": str(uuid.uuid4()),
            "services": [],
        },
    )
    await websocket.send(hello.model_dump_json())
    try:
        raw = await asyncio.wait_for(websocket.recv(), timeout=5)
        pack = MsgPack(**json.loads(raw))
        if pack.type == PackType.HELLO_ACK:
            return True
        elif pack.type == PackType.HELLO_REJECT:
            reason = (pack.data or {}).get("reason", "unknown")

            return False
    except asyncio.TimeoutError:
        pass
    return False


async def receive_loop(websocket, state: dict, pipe: asyncio.Queue):
    """Background receive loop — fills state dict."""
    try:
        async for raw in websocket:
            data = json.loads(raw)
            pack = MsgPack(**data)
            match pack.type:
                case PackType.RESPONSE:
                    state["last_response"] = pack.data
                    state["response_label"] = pack.label[:8]
                    state["response_ready"] = True
                case PackType.GOSSIP:
                    state["gossip_data"] = (pack.data or {}).get("neighbors", [])
                    state["gossip_from"] = (pack.data or {}).get("from", pack.source)
                case PackType.ANNOUNCE:
                    svc = (pack.data or {}).get("services", [])
                    from_node = (pack.data or {}).get("from", pack.source)
                    state["announces"].append((from_node, svc))
                    state["announces"] = state["announces"][-5:]
                case PackType.PING:
                    pong = MsgPack(
                        type=PackType.PONG,
                        source=OWN_NODE,
                        dst=pack.source,
                        label=pack.label,
                    )
                    await websocket.send(pong.model_dump_json())
                case PackType.STREAM_OPEN:
                    state["stream_label"] = pack.label
                    state["stream_eof"] = False
                    state["stream_service"] = pack.service
                    state["stream_method"] = pack.method
                    await websocket.send(
                        MsgPack(
                            type=PackType.STREAM_READY,
                            source=OWN_NODE,
                            dst=pack.source,
                            label=pack.label,
                            data="ready",
                        ).model_dump_json()
                    )
                case PackType.STREAM_CHUNK:
                    await pipe.put(("chunk", pack.data))
                case PackType.STREAM_EOF:
                    state["stream_eof"] = True
                    await pipe.put(("eof", None))
                case PackType.STREAM_READY:
                    state["stream_ready"] = True
                case PackType.ERROR:
                    state["last_response"] = {"error": pack.error}
                    state["response_ready"] = True
                case _:
                    pass
    except (
        websockets.exceptions.ConnectionClosedOK,
        websockets.exceptions.ConnectionClosedError,
    ):
        state["connection_closed"] = True
    except Exception:
        state["connection_closed"] = True


async def rpc(websocket, state, service, method, data=None, dst=DST_NODE):
    """One-shot RPC — returns response data or None on timeout."""
    label = str(uuid.uuid4())
    state["response_ready"] = False
    state["last_response"] = None
    state["_rpc_label"] = label
    pack = MsgPack(
        source=OWN_NODE,
        dst=dst,
        service=service,
        method=method,
        data=data,
        label=label,
    )
    await websocket.send(pack.model_dump_json())
    for _ in range(50):  # 5 sec total
        if state.get("response_ready") and state.get("_rpc_label") == label:
            return state.get("last_response")
        await asyncio.sleep(0.1)
    return {"error": "timeout waiting for response"}


# ------------------------------------------------------------------
# Known service methods (from docs)
# ------------------------------------------------------------------
KNOWN_METHODS = {
    "netinfo": ["neighbors", "nodes", "services", "find_service"],
    "compute_full": [
        "start_stream",
        "compute_ranges",
        "compute_squares",
        "run_range",
    ],
    "generator": ["start_stream"],
    "test": ["echo", "echo_stream"],
}

# ------------------------------------------------------------------
# Windows key input — msvcrt-based non-blocking keyboard
# ------------------------------------------------------------------
if sys.platform == "win32":
    import msvcrt

    def get_key_nonblocking():
        """Returns a key character or None if no key pressed."""
        if msvcrt.kbhit():
            ch = msvcrt.getch()
            if ch == b"\xe0" or ch == b"\x00":
                ch2 = msvcrt.getch()
                arrow_map = {
                    b"H": "UP",
                    b"P": "DOWN",
                    b"K": "LEFT",
                    b"M": "RIGHT",
                    b"G": "HOME",
                    b"O": "END",
                }
                return arrow_map.get(ch2, None)
            elif ch == b"\r":
                return "ENTER"
            elif ch == b"\x1b":
                return "ESC"
            elif ch == b"\x08" or ch == b"\x7f":
                return "BACKSPACE"
            elif ch == b"\x03":
                return "CTRL_C"
            else:
                try:
                    return ch.decode("utf-8", errors="replace")
                except Exception:
                    return None
        return None

else:
    import select
    import tty
    import termios

    def get_key_nonblocking():
        if select.select([sys.stdin], [], [], 0)[0]:
            ch = sys.stdin.read(1)
            if ch == "\r":
                return "ENTER"
            elif ch == "\x1b":
                if select.select([sys.stdin], [], [], 0.01)[0]:
                    seq = sys.stdin.read(2)
                    if seq == "[A":
                        return "UP"
                    elif seq == "[B":
                        return "DOWN"
                return "ESC"
            elif ch in ("\x08", "\x7f"):
                return "BACKSPACE"
            elif ch == "\x03":
                return "CTRL_C"
            return ch
        return None


# ------------------------------------------------------------------
# Screen rendering
# ------------------------------------------------------------------
def render_screen(state, selected_idx, scroll_offset, input_buf, input_mode, status_msg):
    """Build the entire screen as a string."""
    cols, rows = get_terminal_size()
    if rows < 15 or cols < 40:
        return goto(1, 1) + RED + "Terminal too small! Need at least 40x15" + RESET

    sep_row = max(5, int(rows * 0.40))
    services = state.get("services", [])
    parts = []

    parts.append(CLEAR + HOME)

    # ============ NETWORK PANEL ============
    net_h = sep_row - 1
    parts.append(draw_box(0, 0, net_h, cols, " Network Status "))

    header = f" Own Node: {OWN_NODE} | Target: {DST_NODE} | Protocol v{PROTOCOL_VERSION}"
    parts.append(put_text(2, 1, header, BOLD, max_width=cols - 4))

    neighbors = state.get("neighbors", [])
    connected = [n for n in neighbors if n.get("status") == "CONNECTED"]
    known = [n for n in neighbors if n.get("status") == "KNOWN"]
    parts.append(
        put_text(
            2, 2,
            f" Connected: {len(connected)}  |  Known: {len(known)}",
            BOLD,
            max_width=cols - 4,
        )
    )

    row = 3
    if row < net_h - 1:
        parts.append(
            put_text(2, row, "  Node ID        Host:Port        Status       Via", DIM, max_width=cols - 4)
        )
        row += 1

    for n in connected:
        if row >= net_h - 1:
            break
        # FIX: Added max_width= keyword
        parts.append(put_text(2, row, format_neighbor(n), max_width=cols - 4))
        row += 1

    for n in known:
        if row >= net_h - 1:
            break
        # FIX: Added max_width= keyword
        parts.append(put_text(2, row, format_neighbor(n), max_width=cols - 4))
        row += 1

    gossip_from = state.get("gossip_from", "")
    if gossip_from and row < net_h - 1:
        gossip_n = state.get("gossip_data", [])
        parts.append(
            put_text(2, row, f" Gossip from {gossip_from}: {len(gossip_n)} neighbors", CYAN, max_width=cols - 4)
        )
        row += 1

    announces = state.get("announces", [])[-3:]
    for from_node, svcs in announces:
        if row >= net_h - 1:
            break
        svc_str = ", ".join(svcs)
        parts.append(
            put_text(2, row, f" Announce from {from_node}: {svc_str}", CYAN, max_width=cols - 4)
        )
        row += 1

    conn_status = "DISCONNECTED" if state.get("connection_closed") else "CONNECTED"
    conn_color = RED if state.get("connection_closed") else GREEN
    badge_x = max(cols - 18, 2)
    parts.append(put_text(badge_x, net_h - 2, f" [{conn_status}] ", conn_color + BOLD))

    parts.append(goto(1, net_h + 1) + LM + H * (cols - 2) + RM)

    # ============ SERVICE PANEL ============
    svc_top = net_h + 1
    svc_h = rows - svc_top - 1
    parts.append(draw_box(svc_top, 0, svc_h, cols, " Services "))

    svc_inner_top = svc_top + 1
    parts.append(put_text(2, svc_inner_top, " #  Service", BOLD, max_width=cols - 4))

    list_rows = max(1, (svc_h - 4) // 2)
    visible = services[scroll_offset : scroll_offset + list_rows]
    for i, svc in enumerate(visible):
        r = svc_inner_top + 1 + i
        idx = scroll_offset + i
        if idx >= svc_inner_top + list_rows:
            break
        if r >= svc_top + svc_h - 1:
            break
        if idx == selected_idx:
            parts.append(
                put_text(2, r, f">>> [{idx}] {svc}", REVERSE + BOLD, max_width=cols - 4)
            )
        else:
            # FIX: Added max_width= keyword (THIS WAS THE CRASH)
            parts.append(put_text(2, r, f"    [{idx}] {svc}", max_width=cols - 4))

    sep_in_svc = svc_inner_top + 1 + list_rows
    if sep_in_svc < svc_top + svc_h - 2:
        parts.append(put_text(2, sep_in_svc, H * (cols - 6), DIM, max_width=cols - 4))

    detail_row = sep_in_svc + 1
    if services and selected_idx < len(services) and detail_row < svc_top + svc_h - 2:
        sel_svc = services[selected_idx]
        parts.append(put_text(2, detail_row, f" Service: {sel_svc}", BOLD, max_width=cols - 4))
        methods = state.get("service_methods", {}).get(sel_svc, [])
        if methods and detail_row + 1 < svc_top + svc_h - 2:
            method_str = "  ".join(f"[{i}] {m}" for i, m in enumerate(methods))
            parts.append(
                put_text(4, detail_row + 1, f"Methods: {method_str}", DIM, max_width=cols - 6)
            )

    resp_start = detail_row + 3
    if state.get("last_response") is not None:
        resp_h = svc_top + svc_h - 2 - resp_start
        if resp_h > 2:
            parts.append(draw_box(resp_start, 2, resp_h, cols - 4, " Response "))
            resp_text = json.dumps(state.get("last_response"), indent=2, default=str)
            lines = resp_text.split("\n")
            for i, line in enumerate(lines[: resp_h - 2]):
                rr = resp_start + 1 + i
                if rr >= svc_top + svc_h - 2:
                    break
                # FIX: Added max_width= keyword
                parts.append(put_text(4, rr, line[: cols - 8], max_width=cols - 8))

    if state.get("stream_label"):
        slabel = state["stream_label"][:8]
        ssvc = state.get("stream_service", "")
        smeth = state.get("stream_method", "")
        eof = " [EOF]" if state.get("stream_eof") else ""
        sx = max(cols - 42, 2)
        sy = svc_top + svc_h - 2
        parts.append(
            put_text(sx, sy, f" stream:{slabel} {ssvc}:{smeth}{eof}", CYAN)
        )

    # ============ INPUT BAR ============
    input_row = rows - 1
    parts.append(goto(1, input_row) + " " * cols)
    if input_mode:
        parts.append(
            goto(1, input_row)
            + BOLD
            + WHITE_ON_BLUE
            + f" ARGS> {input_buf}"
            + " " * max(0, cols - len(input_buf) - 8)
            + RESET
        )
    else:
        if status_msg:
            parts.append(goto(1, input_row) + DIM + status_msg[: cols - 1] + RESET)
        else:
            services = state.get("services", [])
            sel_svc = services[selected_idx] if selected_idx < len(services) else ""
            methods = state.get("service_methods", {}).get(sel_svc, [])
            hint = "Enter=invoke"
            if methods:
                hint += f" {sel_svc}.{methods[0]}"
            hint += " | type args or method name | q=quit | r=refresh"
            parts.append(goto(1, input_row) + DIM + hint[: cols - 1] + RESET)

    return "".join(parts)


# ------------------------------------------------------------------
# Main interactive loop
# ------------------------------------------------------------------
async def async_main():
    # FIX: Removed trailing spaces in state keys
    state = {
        "neighbors": [],
        "services": [],
        "service_methods": {},
        "last_response": None,
        "response_ready": False,
        "response_label": "",
        "gossip_data": [],
        "gossip_from": "",
        "announces": [],
        "stream_label": None,
        "stream_eof": False,
        "stream_ready": False,
        "stream_service": "",
        "stream_method": "",
        "connection_closed": False,
    }
    selected_idx = 0
    scroll_offset = 0
    input_buf = ""
    input_mode = False
    status_msg = "Connecting..."
    refresh_timer = 0

    sys.stdout.write(HIDE_CURSOR + CLEAR + HOME)
    sys.stdout.flush()

    try:
        async with websockets.connect(URI) as websocket:
            accepted = await do_handshake(websocket)
            if not accepted:
                status_msg = RED + "Handshake REJECTED" + RESET
                sys.stdout.write(goto(1, 1) + status_msg + goto(1, 3) + SHOW_CURSOR)
                sys.stdout.flush()
                await asyncio.sleep(3)
                return

            status_msg = (
                f"Connected as {OWN_NODE} — "
                f"↑↓ navigate, Enter invoke, 'q' quit"
            )

            pipe = asyncio.Queue()
            recv_task = asyncio.create_task(receive_loop(websocket, state, pipe))

            neighbors_data = await rpc(websocket, state, "netinfo", "neighbors")
            if neighbors_data:
                state["neighbors"] = neighbors_data.get("connected", []) + \
                                     neighbors_data.get("known", [])

            svc_data = await rpc(websocket, state, "netinfo", "services")
            if svc_data:
                state["services"] = svc_data if isinstance(svc_data, list) else []
                state["service_methods"] = {
                    svc: KNOWN_METHODS.get(svc, ["?"])
                    for svc in state["services"]
                }

            while True:
                cols, rows = get_terminal_size()
                key = get_key_nonblocking()

                if key == "CTRL_C" or (key == "q" and not input_mode):
                    break

                if input_mode:
                    if key == "ESC":
                        input_mode = False
                        input_buf = ""
                    elif key == "ENTER":
                        input_mode = False
                        services = state.get("services", [])
                        if services and selected_idx < len(services):
                            sel_svc = services[selected_idx]
                            methods = state.get("service_methods", {}).get(sel_svc, [])
                            raw_input = input_buf.strip()
                            method_name = methods[0] if methods else ""
                            call_data = {}
                            if raw_input:
                                try:
                                    parsed = json.loads(raw_input)
                                    if isinstance(parsed, dict):
                                        call_data = dict(parsed)
                                        if "method" in call_data:
                                            method_name = call_data.pop("method")
                                    else:
                                        call_data = {"value": parsed}
                                except json.JSONDecodeError:
                                    parts = raw_input.split(None, 1)
                                    if parts[0] in methods:
                                        method_name = parts[0]
                                        rest = parts[1] if len(parts) > 1 else ""
                                    else:
                                        method_name = parts[0]
                                        rest = ""
                                    if rest:
                                        try:
                                            for pair in rest.split(","):
                                                k, v = pair.strip().split("=", 1)
                                                try:
                                                    v = int(v)
                                                except ValueError:
                                                    try:
                                                        v = float(v)
                                                    except ValueError:
                                                        pass
                                                call_data[k.strip()] = v
                                        except Exception:
                                            call_data = {"_raw": rest}
                            if not method_name:
                                status_msg = f"No method for {sel_svc}"
                            else:
                                status_msg = f"Calling {sel_svc}.{method_name}..."
                                result = await rpc(
                                    websocket, state, sel_svc, method_name, call_data
                                )
                                if result:
                                    status_msg = f"OK: {sel_svc}.{method_name}"
                                else:
                                    status_msg = f"Timeout: {sel_svc}.{method_name}"
                        input_buf = ""
                    elif key == "BACKSPACE":
                        input_buf = input_buf[:-1]
                    elif key and len(key) == 1 and ord(key) >= 32:
                        input_buf += key
                else:
                    services = state.get("services", [])
                    max_idx = len(services) - 1 if services else 0
                    if key == "UP":
                        selected_idx = max(0, selected_idx - 1)
                    elif key == "DOWN":
                        selected_idx = min(max_idx, selected_idx + 1)
                    elif key == "HOME":
                        selected_idx = 0
                    elif key == "END":
                        selected_idx = max_idx
                    elif key == "ENTER":
                        input_mode = True
                        input_buf = ""
                    elif key == "r":
                        status_msg = "Refreshing..."
                        nd = await rpc(websocket, state, "netinfo", "neighbors")
                        if nd:
                            state["neighbors"] = nd.get("connected", []) + \
                                                 nd.get("known", [])
                        sd = await rpc(websocket, state, "netinfo", "services")
                        if sd:
                            state["services"] = sd if isinstance(sd, list) else []
                            state["service_methods"] = {
                                svc: KNOWN_METHODS.get(svc, ["?"])
                                for svc in state["services"]
                            }
                        status_msg = "Refreshed"

                list_rows = max(1, (rows - int(rows * 0.40) - 5) // 2)
                if selected_idx < scroll_offset:
                    scroll_offset = selected_idx
                elif selected_idx >= scroll_offset + list_rows:
                    scroll_offset = selected_idx - list_rows + 1

                refresh_timer += 1
                if refresh_timer % 50 == 0:
                    nd = await rpc(websocket, state, "netinfo", "neighbors")
                    if nd:
                        state["neighbors"] = nd.get("connected", []) + \
                                             nd.get("known", [])
                    refresh_timer = 0

                while not pipe.empty():
                    msg_type, msg_data = await pipe.get()
                    if msg_type == "chunk":
                        state["last_response"] = msg_data
                        state["response_ready"] = True
                    elif msg_type == "eof":
                        state["stream_eof"] = True

                screen = render_screen(
                    state, selected_idx, scroll_offset,
                    input_buf, input_mode, status_msg
                )
                sys.stdout.write(screen)
                sys.stdout.flush()
                await asyncio.sleep(0.05)

            recv_task.cancel()
            try:
                await recv_task
            except asyncio.CancelledError:
                pass

    except ConnectionRefusedError:
        sys.stdout.write(
            goto(1, 1) + RED + f"Connection refused — server not running on {URI}" + RESET
        )
        sys.stdout.flush()
        await asyncio.sleep(3)
    finally:
        sys.stdout.write(SHOW_CURSOR + goto(1, rows) + RESET)
        sys.stdout.flush()


def main():
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write(SHOW_CURSOR + RESET + "\n")
        sys.stdout.flush()


# FIX: Was `if name == "main"` — must be `__name__` and `"__main__"`
if __name__ == "__main__":
    main()