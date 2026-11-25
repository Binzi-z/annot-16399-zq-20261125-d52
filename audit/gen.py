"""本地合成数据：小图（含互相引用、共享来源、迟到更正）与 6 万声明大图。"""
from __future__ import annotations

import random

from .model import Case, Claim, Evidence

TAU_OLD = 10   # 旧封存时刻
TAU_NOW = 20   # 当下时刻（迟到更正在此刻生效）


def small_case() -> Case:
    """小图：两条'看似独立'的事实 c1/c2 实际共享传感来源 sensorA。"""
    case = Case()
    for e in [
        Evidence("e1", "sensorA", add_t=0),
        Evidence("e2", "sensorA", add_t=0),          # 与 e1 同源的另一条转引
        Evidence("e3", "sensorB", add_t=0),
        Evidence("e4", "sensorC", add_t=0),
        Evidence("e5", "human", add_t=0),
        Evidence("e6", "sensorB", add_t=0, revoke_t=TAU_NOW),  # 迟到更正：τ=20 撤销
        Evidence("e7", "sensorD", add_t=TAU_NOW),              # 迟到补充：τ=20 才到
    ]:
        case.evidence[e.eid] = e
    for c in [
        Claim("c1", "and", (("e", "e1"), ("e", "e3"))),
        Claim("c2", "or", (("e", "e2"), ("e", "e4"))),
        Claim("c3", "and", (("c", "c1"), ("c", "c2"))),   # 隐含共享 sensorA
        Claim("c4", "or", (("c", "c3"), ("e", "e5"))),
        Claim("c5", "and", (("c", "c6"), ("e", "e3"))),   # 与 c6 互相引用
        Claim("c6", "or", (("c", "c5"), ("e", "e4"))),
        Claim("c7", "and", (("e", "e6"), ("e", "e1"))),   # 受迟到更正影响
        Claim("c8", "or", (("e", "e6"), ("e", "e7"))),    # 旧可用新撤销 / 迟到证据
    ]:
        case.claims[c.cid] = c
    return case


def large_case(n_claims: int = 60000, n_components: int = 12,
               seed: int = 16399) -> tuple[Case, str]:
    """大图：多个互不相连的组件；返回 (案件, 将被修改的证据 id)。

    结构约束保证前沿有界：两子项声明大多带一个证据子项；
    少量组件内回边构成互相引用的 SCC。
    """
    rng = random.Random(seed)
    case = Case()
    per = n_claims // n_components
    layers = 10
    per_layer = per // layers
    changed_ev = None
    for comp in range(n_components):
        p = f"k{comp:02d}"
        n_ev = per // 4
        evs = []
        for i in range(n_ev):
            eid = f"{p}e{i:05d}"
            root = f"{p}r{i % (n_ev // 4):04d}"
            case.evidence[eid] = Evidence(eid, root, add_t=0)
            evs.append(eid)
        # 将被修改的证据：组件 0 中引用较多的一条
        if comp == 0:
            changed_ev = evs[0]
        prev_layer: list[str] = []
        for layer in range(layers):
            cur_layer = []
            for i in range(per_layer):
                cid = f"{p}c{layer * per_layer + i:06d}"
                roll = rng.random()
                if layer == 0 or roll < 0.45:
                    refs = (("e", rng.choice(evs)),)
                elif roll < 0.90:
                    refs = (
                        ("e", rng.choice(evs)),
                        ("c", rng.choice(prev_layer)),
                    )
                else:
                    refs = (
                        ("c", rng.choice(prev_layer)),
                        ("c", rng.choice(prev_layer)),
                    )
                op = "and" if rng.random() < 0.4 else "or"
                case.claims[cid] = Claim(cid, op, refs)
                cur_layer.append(cid)
            # 组件内互相引用（同层回边，构成 SCC）
            if layer >= 2 and cur_layer:
                for _ in range(2):
                    a = rng.choice(cur_layer)
                    b = rng.choice(cur_layer)
                    if a != b:
                        ca = case.claims[a]
                        case.claims[a] = Claim(ca.cid, ca.op, ca.refs + (("c", b),))
            prev_layer = cur_layer
    assert changed_ev is not None
    return case, changed_ev


def weak_components(case: Case) -> dict[str, int]:
    """声明的弱连通分量（仅按声明间引用划分）。"""
    parent = {cid: cid for cid in case.claims}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for cid, c in case.claims.items():
        for kind, rid in c.refs:
            if kind == "c":
                parent[find(cid)] = find(rid)
    return {cid: find(cid) for cid in case.claims}
