from collections import deque
from pathlib import Path
import json
import os

import yaml


def _adjacency(snapshot: dict) -> dict[str, set[str]]:
    adj: dict[str, set[str]] = {}
    for e in snapshot.get('edges', []) or []:
        s, d = e.get('src'), e.get('dst')
        if not s or not d:
            continue
        adj.setdefault(s, set()).add(d)
        adj.setdefault(d, set()).add(s)  # неориентированный BFS: важна достижимость, не направление WS
    return adj


def plan_waves(snapshot: dict, root: str) -> tuple[list[list[str]], list[list[str]]]:
    adj = _adjacency(snapshot)
    dist: dict[str, int] = {root: 0}
    q: deque[str] = deque([root])
    while q:
        cur = q.popleft()
        for nb in sorted(adj.get(cur, ())):
            if nb not in dist:
                dist[nb] = dist[cur] + 1
                q.append(nb)
    by_hop: dict[int, list[str]] = {}
    for node, h in dist.items():
        if node == root or h < 1:
            continue
        by_hop.setdefault(h, []).append(node)
    seed = [sorted(by_hop[h]) for h in sorted(by_hop)]
    return seed, seed[::-1]


def _canon(s) -> str:
    return s.strip().lower() if isinstance(s, str) else s


def ensure_update_source(text: str, admin: str, share: str = 'dist') -> str | None:
    data = yaml.safe_load(text) or {}
    admin = _canon(admin)
    sources = ((data.get('update') or {}).get('sources')) or []
    if any(_canon(s.get('node')) == admin for s in sources if isinstance(s, dict)):
        return None
    data.setdefault('update', {}).setdefault('sources', []).append({'node': admin, 'share': share})
    return yaml.dump(data, allow_unicode=True, sort_keys=False)


def ensure_cdn_share(text: str, download_dir: str) -> str | None:
    data = yaml.safe_load(text) or {}
    shares = ((data.get('files') or {}).get('shares')) or []
    if any(s.get('name') == 'cdn_update' for s in shares if isinstance(s, dict)):
        return None
    data.setdefault('files', {}).setdefault('shares', []).append(
        {'name': 'cdn_update', 'path': str(download_dir), 'allow': [], 'chunk_size': 262144})
    return yaml.dump(data, allow_unicode=True, sort_keys=False)


class RolloutJournal:
    def __init__(self, work_dir: Path, version: str):
        safe = ''.join(c for c in version if c.isalnum() or c in '.-_')
        self.path = Path(work_dir) / f'rollout_{safe}.json'

    def write(self, state: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + '.tmp')
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(tmp, self.path)

    def read(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return {}
