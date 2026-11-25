"""封存报告与迟到更正的重现。

原则：报告是 (案件结构, 证据事件流, 封存时刻 τ) 的纯函数。
迟到更正（在 τ2 撤销某证据）只改变以 τ >= τ2 求值的当下报告；
以 τ1 < τ2 重现旧封存报告时必须得到与封存时逐字节一致的文本，
不允许用后来已知的事实覆盖过去。
"""
from __future__ import annotations

import hashlib
import json

from .model import Case
from .solver import Engine


def render_report(case: Case, tau: int, engine: Engine | None = None) -> str:
    """生成 τ 时刻的结案报告（确定性文本，同输入必同输出）。"""
    eng = engine or Engine(case, tau)
    if engine is None:
        eng.compute_all()
    body = {
        "tau": tau,
        "claims": {cid: eng.result(cid) for cid in sorted(case.claims)},
    }
    return json.dumps(body, ensure_ascii=False, sort_keys=True, indent=1)


def seal(case: Case, tau: int) -> dict:
    """封存：生成报告文本与其摘要，作为不可回写的历史快照。"""
    text = render_report(case, tau)
    return {
        "tau": tau,
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "text": text,
    }


def reproduce(case: Case, sealed: dict) -> bool:
    """用包含迟到更正的完整事件流重现旧封存报告，核验未被后来事实覆盖。"""
    text = render_report(case, sealed["tau"])
    return (
        hashlib.sha256(text.encode("utf-8")).hexdigest() == sealed["sha256"]
        and text == sealed["text"]
    )


def shared_sources(case: Case, eng: Engine) -> dict[str, list[str]]:
    """隐含共享来源：root -> 最小支持证书中用到该来源的声明列表。

    用于揭露"看似独立、实则转引同一份传感证据"的声明簇。
    """
    sharing: dict[str, list[str]] = {}
    for cid in sorted(case.claims):
        sup = eng.best(cid, "sup")
        if sup is None:
            continue
        for root in sorted({eng.roots_of[e] for e in sup}):
            sharing.setdefault(root, []).append(cid)
    return {r: cids for r, cids in sorted(sharing.items())}
