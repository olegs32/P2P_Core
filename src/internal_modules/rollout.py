from collections import deque


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
