"""总验证：小图对拍、封存重现、迟到更正、6 万声明增量核对。

用法：python verify.py
"""
from __future__ import annotations

import dataclasses
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from audit.enumerator import Enumerator
from audit.gen import TAU_NOW, TAU_OLD, large_case, small_case, weak_components
from audit.report import render_report, reproduce, seal, shared_sources
from audit.solver import Engine

PASS = []


def check(name: str, ok: bool, detail: str = "") -> None:
    PASS.append(ok)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def verify_small_graph() -> None:
    print("== 小图：主求解器 vs 独立枚举工具（含互相引用与共享来源） ==")
    case = small_case()
    for tau, label in ((TAU_OLD, "旧封存时刻"), (TAU_NOW, "当下时刻")):
        eng = Engine(case, tau)
        eng.compute_all()
        enum = Enumerator(case, tau)
        enum_res = enum.run_all()
        ok_sup = ok_ref = True
        for cid in sorted(case.claims):
            s_sup = eng.best(cid, "sup")
            e_sup = enum_res[cid]["support"]
            if (s_sup or None) != (e_sup or None):
                ok_sup = False
                print(f"  支持不一致 {label} {cid}: solver={s_sup} enum={e_sup}")
            if s_sup is None:
                s_ref = eng.best(cid, "ref")
                e_ref = enum_res[cid]["refutation"]
                if (s_ref or None) != (e_ref or None):
                    ok_ref = False
                    print(f"  否决不一致 {label} {cid}: solver={s_ref} enum={e_ref}")
        check(f"小图最小支持证书一致（{label}）", ok_sup)
        check(f"小图最短否决边界一致（{label}）", ok_ref)
        share_solver = shared_sources(case, eng)
        share_enum = enum.shared_sources(enum_res)
        check(f"隐含共享来源一致（{label}）", share_solver == share_enum)
    # 展示共享来源：c1/c2/c3 看似独立实则同引 sensorA
    eng = Engine(case, TAU_OLD)
    eng.compute_all()
    sharing = shared_sources(case, eng)
    print("  隐含共享来源(root -> 声明):", sharing)
    check("c1 与 c2 的最小支持共享 sensorA",
          "c1" in sharing.get("sensorA", []) and "c2" in sharing.get("sensorA", []))


def verify_sealing() -> None:
    print("== 迟到更正：旧封存报告可重现，当下报告反映更正 ==")
    case = small_case()
    sealed = seal(case, TAU_OLD)
    # 迟到更正已在事件流中（e6 于 TAU_NOW 撤销、 e7 于 TAU_NOW 才到），
    # 用包含全部后来知识的同一 Case 重现旧报告，必须逐字节一致。
    check("旧封存报告可逐字节重现（未被后来已知覆盖）", reproduce(case, sealed))
    old = render_report(case, TAU_OLD)
    now = render_report(case, TAU_NOW)
    check("当下报告与旧封存报告不同（更正生效）", old != now)
    eng_old, eng_now = Engine(case, TAU_OLD), Engine(case, TAU_NOW)
    eng_old.compute_all()
    eng_now.compute_all()
    r7_old, r7_now = eng_old.result("c7"), eng_now.result("c7")
    check("c7 旧时刻受支持且证书含 e6",
          r7_old["status"] == "supported" and "e6" in r7_old["evidence"])
    check("c7 当下转为不受支持并给出否决边界",
          r7_now["status"] == "unsupported" and r7_now["refutation"] == ["e6"])
    r8_old, r8_now = eng_old.result("c8"), eng_now.result("c8")
    check("c8 旧时刻用 e6、当下改用迟到证据 e7",
          r8_old["evidence"] == ["e6"] and r8_now["evidence"] == ["e7"])


def verify_large_incremental() -> None:
    print("== 大图：60,000 条声明中改一条证据的增量核对 ==")
    t0 = time.perf_counter()
    case1, changed_ev = large_case()
    # 迟到更正：同一条证据在 TAU_NOW 被撤销
    case2 = dataclasses.replace(case1)
    case2.evidence = dict(case1.evidence)
    ev = case2.evidence[changed_ev]
    case2.evidence[changed_ev] = dataclasses.replace(ev, revoke_t=TAU_NOW)

    eng = Engine(case2, TAU_OLD)
    eng.compute_all()
    affected = eng.affected_by({changed_ev})
    recomputed = eng.compute_incremental({changed_ev}, TAU_NOW)

    check("实际重算节点 == 反向可达受影响集",
          recomputed == affected,
          f"重算 {len(recomputed)} / 共 {len(case2.claims)} 节点")

    fresh = Engine(case2, TAU_NOW)
    fresh.compute_all()
    check("增量后全量结果 == 从零全量重算",
          render_report(case2, TAU_NOW, engine=eng)
          == render_report(case2, TAU_NOW, engine=fresh))

    comp = weak_components(case2)
    allowed = {comp[c] for c in affected}
    touched_outside = [c for c in eng.accessed if comp[c] not in allowed]
    n_comp = len(set(comp.values()))
    check("无关组件零访问",
          not touched_outside and len(allowed) < n_comp,
          f"受影响组件 {len(allowed)} 个 / 共 {n_comp} 个，越界访问 {len(touched_outside)} 处")

    baseline = Engine(case2, TAU_OLD)
    baseline.compute_all()
    changed_results = [
        cid for cid in case2.claims
        if eng.result(cid) != baseline.result(cid)
    ]
    # 注意：上面仅为统计，结果变化集必须落在受影响集内
    check("结果发生变化的声明均在受影响集内",
          set(changed_results) <= affected,
          f"结果变化 {len(changed_results)} 条")
    print(f"  用时 {time.perf_counter() - t0:.2f}s")


def main() -> int:
    verify_small_graph()
    verify_sealing()
    verify_large_incremental()
    print(f"\n{'全部通过' if all(PASS) else '存在失败'}：{sum(PASS)}/{len(PASS)}")
    return 0 if all(PASS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
