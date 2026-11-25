"""第二个独立复核工具：小图上的暴力枚举。

与主求解器（audit/solver.py）不共享任何求值代码：
- 支持性用"全体声明的最小不动点真值"直接定义；
- 最小支持证书：按固定成本顺序枚举可用证据子集 S，仅以 S 中证据为真时
  声明为真的第一个 S；
- 最短否决边界：按同一顺序枚举当前不可用证据的子集 B，把除 B 外的不可用
  证据全部"恢复"后声明仍为假的第一个 B；
- 隐含共享来源：由枚举得到的最小证书独立推导 root -> 声明 映射。

固定成本顺序（与主求解器一致）：(席位数, 证据条数, 排序后证据 id 元组)。
"""
from __future__ import annotations

from itertools import combinations

from .model import Case


def _truth_fixpoint(case: Case, active: set[str]) -> dict[str, bool]:
    """仅以 active 中的证据为真，求全部声明的最小不动点真值。"""
    val = {cid: False for cid in case.claims}
    for _ in range(len(case.claims) + 1):
        changed = False
        for cid, c in case.claims.items():
            parts = [
                (rid in active) if kind == "e" else val[rid]
                for kind, rid in c.refs
            ]
            v = all(parts) if c.op == "and" else any(parts)
            if not c.refs:
                v = c.op == "and"
            if v != val[cid]:
                val[cid] = v
                changed = True
        if not changed:
            return val
    raise RuntimeError("fixpoint did not converge")


def _ordered_subsets(eids: list[str], roots_of: dict[str, str]):
    """按固定成本顺序产出全部子集。"""
    subs = []
    for k in range(len(eids) + 1):
        for combo in combinations(sorted(eids), k):
            s = frozenset(combo)
            subs.append(s)
    subs.sort(key=lambda s: (len({roots_of[e] for e in s}), len(s), tuple(sorted(s))))
    return subs


class Enumerator:
    def __init__(self, case: Case, tau: int):
        self.case = case
        self.tau = tau
        self.roots_of = {eid: e.root for eid, e in case.evidence.items()}
        self.usable = sorted(e for e, ev in case.evidence.items() if ev.usable(tau))
        self.unusable = sorted(e for e, ev in case.evidence.items() if not ev.usable(tau))

    def minimal_support(self, cid: str) -> frozenset | None:
        for s in _ordered_subsets(self.usable, self.roots_of):
            if _truth_fixpoint(self.case, set(s))[cid]:
                return s
        return None

    def minimal_refutation(self, cid: str) -> frozenset | None:
        base = set(self.usable)
        for b in _ordered_subsets(self.unusable, self.roots_of):
            active = base | (set(self.unusable) - set(b))
            if not _truth_fixpoint(self.case, active)[cid]:
                return b
        return None

    def run_all(self) -> dict[str, dict]:
        out = {}
        for cid in sorted(self.case.claims):
            sup = self.minimal_support(cid)
            ref = None if sup is not None else self.minimal_refutation(cid)
            out[cid] = {"support": sup, "refutation": ref}
        return out

    def shared_sources(self, results: dict[str, dict]) -> dict[str, list[str]]:
        sharing: dict[str, list[str]] = {}
        for cid in sorted(self.case.claims):
            sup = results[cid]["support"]
            if sup is None:
                continue
            for root in sorted({self.roots_of[e] for e in sup}):
                sharing.setdefault(root, []).append(cid)
        return {r: cids for r, cids in sorted(sharing.items())}
