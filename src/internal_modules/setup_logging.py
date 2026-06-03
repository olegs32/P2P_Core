import logging

from src.internal_modules.config import LoggingConfig

COLORS = {
    'DEBUG':    '\033[36m',   # cyan
    'INFO':     '\033[32m',   # green
    'WARNING':  '\033[33m',   # yellow
    'ERROR':    '\033[31m',   # red
    'CRITICAL': '\033[35m',   # magenta
}
RESET = '\033[0m'
BOLD  = '\033[1m'
DIM = '\033[2m'

class ColorFormatter(logging.Formatter):
    def formatTime(self, record, datefmt=None):
        t = super().formatTime(record, datefmt)
        return f"{DIM}{t}{RESET}"

    def format(self, record):
        color = COLORS.get(record.levelname, RESET)
        record.levelname = f"{color}{record.levelname}{RESET}"
        record.name      = f"{BOLD}{record.name}{RESET}"
        record.msg       = f"{color}{record.msg}{RESET}"
        return super().format(record)

def setup_logging(cfg: LoggingConfig = None):
    handler = logging.StreamHandler()
    handler.setFormatter(ColorFormatter(
        fmt="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    ))
    logging.root.setLevel(getattr(logging, cfg.level) if cfg else logging.DEBUG)
    logging.root.handlers = [handler]

    ws_level = getattr(logging, cfg.websockets_level) if cfg else logging.WARNING
    logging.getLogger("websockets").setLevel(ws_level)
    logging.getLogger("websockets.client").setLevel(ws_level)
    logging.getLogger("websockets.server").setLevel(ws_level)

    # приглушаем uvicorn
    logging.getLogger("uvicorn").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("fastapi").setLevel(logging.WARNING)
