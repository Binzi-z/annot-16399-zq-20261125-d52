"""主求解器：最小可验证支持证据 与 最短否决边界。

证书 = 证据 id 的集合；其成本 = 不同原始控制来源（root）的个数（独立席位数）。
同成本顺序固定为：(席位数, 证据条数, 排序后的证据 id 元组) —— 全程序唯一比较键。

每个声明维护一个"前沿"：root 集合 -> 该 root 集合下最优证书。
支配规则：若两个证书的 root 集合存在包含关系，则被包含者支配包含者（席位不更多且
来源更集中），被支配者剪除。root 集合相同者按固定顺序取最优。

支持（support）与否决（refutation）是同一递归的对偶：
  叶子：支持前沿 = {{e}} 若 e 在 τ 可用，否则 {}；否决前沿相反。
  AND 声明：支持 = 子项前沿的合取组合(join)；否决 = 子项前沿的择一合并(merge)。
  OR  声明：支持 = merge；否决 = join。
互相引用的声明构成 SCC，在 SCC 内做单调不动点迭代。
"""
from __future__ import annotations

from .model import Case

# 前沿大小安全上限（合成数据保证远低于此；超过即视为数据异常而非静默截断）
FRONTIER_CAP = 4096


def _prune(frontier: dict[frozenset, frozenset]) -> dict[frozenset, frozenset]:
    """按 root 集合包含关系剪枝；相同 root 集合保留固定顺序下最优证书。"""
    items = sorted(frontier.items(), key=lambda kv: (len(kv[0]), sorted(kv[0])))
    kept: list[tuple[frozenset, frozenset]] = []
    for rset, cert in items:
        if any(kr <= rset for kr, _ in kept):
            continue
        kept.append((rset, cert))
    return dict(kept)


def _better(cert_a: frozenset, cert_b: frozenset) -> frozenset:
    """同一 root 集合下的固定同成本顺序：证据条数少者优先，再按 id 字典序。"""
    if len(cert_a) != len(cert_b):
        return cert_a if len(cert_a) < len(cert_b) else cert_b
    return cert_a if sorted(cert_a) <= sorted(cert_b) else cert_b


def merge(fa: dict, fb: dict) -> dict:
    """择一：两前沿并集后剪枝。"""
    out = dict(fa)
    for rset, cert in fb.items():
        out[rset] = _better(out[rset], cert) if rset in out else cert
    return _prune(out)


def join(fa: dict, fb: dict) -> dict:
    """合取：笛卡尔并（root 集合取并）后剪枝。"""
    if not fa or not fb:
        return {}
    out: dict[frozenset, frozenset] = {}
    for ra, ca in fa.items():
        for rb, cb in fb.items():
            r = ra | rb
            c = ca | cb
            out[r] = _better(out[r], c) if r in out else c
            if len(out) > FRONTIER_CAP:
                raise RuntimeError("frontier cap exceeded")
    return _prune(out)


def cert_key(roots_of, cert: frozenset):
    """全程序唯一的同成本比较键。"""
    return (len({roots_of[e] for e in cert}), len(cert), tuple(sorted(cert)))


class Engine:
    """对同一 Case 在时刻 τ 求值；支持全量与增量重算，并记录访问集。"""

    def __init__(self, case: Case, tau: int):
        self.case = case
        self.tau = tau
        self.roots_of = {eid: e.root for eid, e in case.evidence.items()}
        self.usable = {eid: e.usable(tau) for eid, e in case.evidence.items()}
        # sup[cid] / ref[cid]：前沿（root 集合 -> 证书）
        self.sup: dict[str, dict] = {cid: {} for cid in case.claims}
        self.ref: dict[str, dict] = {cid: {} for cid in case.claims}
        self.accessed: set[str] = set()          # 被读取或重算的声明
        self.recomputed: set[str] = set()        # 实际执行重算的声明
        # 依赖结构
        self.claim_deps: dict[str, list[str]] = {
            cid: [r[1] for r in c.refs if r[0] == "c"] for cid, c in case.claims.items()
        }
        self.ev_users: dict[str, list[str]] = {eid: [] for eid in case.evidence}
        self.rev_deps: dict[str, list[str]] = {cid: [] for cid in case.claims}
        for cid, c in case.claims.items():
            for kind, rid in c.refs:
                if kind == "e":
                    self.ev_users[rid].append(cid)
                else:
                    self.rev_deps[rid].append(cid)

    # ---- 单声明一步求值（mode ∈ {"sup", "ref"}） ----
    def _eval_claim(self, cid: str, mode: str) -> dict:
        c = self.case.claims[cid]
        frontiers = self.sup if mode == "sup" else self.ref
        if not c.refs:
            # 空合取视为 vacuously 成立；空择一视为不成立（否决侧对偶）。
            and_like = (c.op == "and") == (mode == "sup")
            return {frozenset(): frozenset()} if and_like else {}
        acc: dict | None = None
        for kind, rid in c.refs:
            if kind == "e":
                ok = self.usable[rid] if mode == "sup" else not self.usable[rid]
                f = {frozenset({self.roots_of[rid]}): frozenset({rid})} if ok else {}
            else:
                self.accessed.add(rid)
                f = frontiers[rid]
            if acc is None:
                acc = dict(f)
            else:
                and_like = (c.op == "and") == (mode == "sup")
                acc = join(acc, f) if and_like else merge(acc, f)
        return _prune(acc) if acc else {}

    def _eval_both(self, cid: str) -> bool:
        new_sup = self._eval_claim(cid, "sup")
        new_ref = self._eval_claim(cid, "ref")
        changed = new_sup != self.sup[cid] or new_ref != self.ref[cid]
        self.sup[cid], self.ref[cid] = new_sup, new_ref
        self.recomputed.add(cid)
        self.accessed.add(cid)
        return changed

    # ---- SCC（Tarjan，迭代实现） ----
    def _sccs(self, nodes: set[str] | None = None) -> list[list[str]]:
        nodes = self.case.claims.keys() if nodes is None else nodes
        index: dict[str, int] = {}
        low: dict[str, int] = {}
        on_stack: set[str] = set()
        stack: list[str] = []
        out: list[list[str]] = []
        counter = 0
        for root in nodes:
            if root in index:
                continue
            work = [(root, 0)]
            while work:
                v, i = work[-1]
                if i == 0:
                    index[v] = low[v] = counter
                    counter += 1
                    stack.append(v)
                    on_stack.add(v)
                recursed = False
                deps = [d for d in self.claim_deps[v] if d in nodes]
                while i < len(deps):
                    w = deps[i]
                    i += 1
                    if w not in index:
                        work[-1] = (v, i)
                        work.append((w, 0))
                        recursed = True
                        break
                    elif w in on_stack:
                        low[v] = min(low[v], index[w])
                if recursed:
                    continue
                work.pop()
                if work:
                    pv = work[-1][0]
                    low[pv] = min(low[pv], low[v])
                if low[v] == index[v]:
                    scc = []
                    while True:
                        w = stack.pop()
                        on_stack.discard(w)
                        scc.append(w)
                        if w == v:
                            break
                    out.append(scc)
        return out

    def _compute_scope(self, scope: set[str]) -> None:
        """在 scope 内按依赖序重算；scope 外的依赖读取已存前沿。"""
        sccs = self._sccs(scope)
        cid2scc = {}
        for i, s in enumerate(sccs):
            for cid in s:
                cid2scc[cid] = i
        # SCC 间拓扑序（依赖先行）
        order = self._topo_scc(sccs, cid2scc, scope)
        for si in order:
            scc = sccs[si]
            if len(scc) == 1 and scc[0] not in self.claim_deps[scc[0]]:
                self._eval_both(scc[0])
            else:
                for _ in range(10000):
                    changed = [self._eval_both(cid) for cid in scc]
                    if not any(changed):
                        break
                else:
                    raise RuntimeError("fixpoint did not converge")

    def _topo_scc(self, sccs, cid2scc, scope) -> list[int]:
        indeg = {i: 0 for i in range(len(sccs))}
        adj: dict[int, set[int]] = {i: set() for i in range(len(sccs))}
        for cid in scope:
            for d in self.claim_deps[cid]:
                if d in scope:
                    a, b = cid2scc[d], cid2scc[cid]
                    if a != b and b not in adj[a]:
                        adj[a].add(b)
                        indeg[b] += 1
        from collections import deque

        ready = deque(sorted([i for i, dg in indeg.items() if dg == 0]))
        order = []
        while ready:
            x = ready.popleft()
            order.append(x)
            for y in sorted(adj[x]):
                indeg[y] -= 1
                if indeg[y] == 0:
                    ready.append(y)
        return order

    # ---- 全量与增量 ----
    def compute_all(self) -> None:
        self._compute_scope(set(self.case.claims))

    def affected_by(self, changed_eids: set[str]) -> set[str]:
        """变更证据的反向可达闭包（理论受影响集，供独立核对）。"""
        seen: set[str] = set()
        stack = [cid for e in changed_eids for cid in self.ev_users.get(e, [])]
        while stack:
            cid = stack.pop()
            if cid in seen:
                continue
            seen.add(cid)
            stack.extend(self.rev_deps[cid])
        return seen

    def compute_incremental(self, changed_eids: set[str], new_tau: int) -> set[str]:
        """迟到更正后的增量重算：仅重算反向可达节点，返回实际重算集。"""
        self.tau = new_tau
        for eid in changed_eids:
            self.usable[eid] = self.case.evidence[eid].usable(new_tau)
        affected = self.affected_by(changed_eids)
        before_recomputed = set(self.recomputed)
        self._compute_scope(affected)
        return self.recomputed - before_recomputed

    # ---- 结果提取 ----
    def best(self, cid: str, mode: str) -> frozenset | None:
        frontier = (self.sup if mode == "sup" else self.ref)[cid]
        if not frontier:
            return None
        return min(frontier.values(), key=lambda c: cert_key(self.roots_of, c))

    def result(self, cid: str) -> dict:
        sup = self.best(cid, "sup")
        if sup is not None:
            return {
                "status": "supported",
                "seats": len({self.roots_of[e] for e in sup}),
                "evidence": sorted(sup),
                "roots": sorted({self.roots_of[e] for e in sup}),
            }
        ref = self.best(cid, "ref")
        return {
            "status": "unsupported",
            "refutation": sorted(ref) if ref is not None else None,
            "refutation_seats": (
                len({self.roots_of[e] for e in ref}) if ref is not None else None
            ),
        }
