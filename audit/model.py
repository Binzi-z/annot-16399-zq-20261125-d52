"""数据模型：证据（带原始控制来源与时间事件）与声明（合取/择一）。

时间模型（迟到更正）：
- 每条证据有事件流：("add", t) 与可选的 ("revoke", t)。
- 在时刻 τ 求值时，证据可用 当且仅当 add_t <= τ 且 (revoke_t 为空或 τ < revoke_t)。
- 旧封存报告用旧的 τ 重现；迟到的撤销只影响 τ 更大的当下报告，绝不回写过去。

独立席位：按证据的原始控制来源 root 计数；多条转引同一来源的证据只占一席。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Evidence:
    eid: str
    root: str            # 原始控制来源（独立席位计数单位）
    add_t: int = 0
    revoke_t: int | None = None  # 迟到更正：撤销时刻

    def usable(self, tau: int) -> bool:
        return self.add_t <= tau and (self.revoke_t is None or tau < self.revoke_t)


# 声明的子项引用：("e", eid) 引用证据，("c", cid) 引用另一声明
Ref = tuple[str, str]


@dataclass(frozen=True)
class Claim:
    cid: str
    op: str              # "and"（合取，需全部支持）或 "or"（择一，任一支持）
    refs: tuple[Ref, ...]


@dataclass
class Case:
    evidence: dict[str, Evidence] = field(default_factory=dict)
    claims: dict[str, Claim] = field(default_factory=dict)

    # ---- 序列化（用于合成数据落盘与复现） ----
    def to_json(self) -> dict:
        return {
            "evidence": [
                {"eid": e.eid, "root": e.root, "add_t": e.add_t, "revoke_t": e.revoke_t}
                for e in sorted(self.evidence.values(), key=lambda x: x.eid)
            ],
            "claims": [
                {"cid": c.cid, "op": c.op, "refs": [list(r) for r in c.refs]}
                for c in sorted(self.claims.values(), key=lambda x: x.cid)
            ],
        }

    @staticmethod
    def from_json(d: dict) -> "Case":
        case = Case()
        for e in d["evidence"]:
            case.evidence[e["eid"]] = Evidence(e["eid"], e["root"], e["add_t"], e["revoke_t"])
        for c in d["claims"]:
            case.claims[c["cid"]] = Claim(c["cid"], c["op"], tuple(tuple(r) for r in c.refs))
        return case
