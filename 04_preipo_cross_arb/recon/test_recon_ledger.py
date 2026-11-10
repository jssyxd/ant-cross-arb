#!/usr/bin/env python3
"""test_recon_ledger.py — 用 R5/R6 的真实时序做回归测试。

运行: python test_recon_ledger.py
"""

import sys
from recon_ledger import (
    DUST_SHARES, NAKED_HALT_S, Action, Fill, HedgeLedger, LegReading, OrphanWatchdog,
    ReadStatus, ReconState, build_ledger, confirm_zero, contracts_per_share,
    cross_check_position, decide, external_drift_detected, fill_implied_position,
    liquidation_or_adl_detected, shares_per_contract, verify_fill_chain,
)

FAILS = []


def check(name, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else f"  -> {detail}"))
    if not cond:
        FAILS.append(name)


def ok(q):
    return LegReading(qty=q, status=ReadStatus.OK, ts=0.0)


def zero_reads(n=3, explained=True, open_orders=0, code_ok=True, structure_ok=True):
    return [LegReading(0.0, ReadStatus.OK, float(i), open_orders=open_orders,
                       explained_by_fills=explained, code_ok=code_ok,
                       structure_ok=structure_ok) for i in range(n)]


print("=== 1. 股等价裸敞口（L1）===")
spc = shares_per_contract(12.7)
cps = contracts_per_share(12.7)
print(f"    1 张 OKX = {spc:.6f} 股 / 1 股 HL 需 {cps:.6f} 张 OKX")
check("S=12.7 时 1 张 = 1.27 股", abs(spc - 1.27) < 1e-9, spc)
check("S=12.7 时 HEDGE_RATIO = 0.7874", abs(cps - 10.0 / 12.7) < 1e-12, cps)

led = HedgeLedger(q_hl=1.90, q_okx=-1.90 / 1.27)   # 理论完美对冲（用精确值，勿用 4 位近似）
check("完美对冲 naked ≈ 0", abs(led.naked_shares) < 1e-6, led.naked_shares)
check("完美对冲 residual ≈ 0", abs(led.residual_okx) < 1e-6, led.residual_okx)
check("完美对冲分类为 HEDGED", led.classify() is ReconState.HEDGED, led.classify())
check("不能用张数相加近似（1.90 vs 1.4961 差 0.4）", abs(1.90 - 1.4961) > 0.4)

print("\n=== 2. R6 现场：HL +1.90 股 / OKX -1.858 张（含 R5 遗留 0.362）===")
r6 = HedgeLedger(q_hl=1.90, q_okx=-1.858)
print(f"    residual={r6.residual_okx:+.4f} ct  naked={r6.naked_shares:+.4f} 股")
check("residual = +0.3619 张（买入，减少空头）", abs(r6.residual_okx - 0.3619) < 1e-3,
      f"{r6.residual_okx:.4f}")
check("naked = -0.4597 股（OKX 空过头 0.46 股）", abs(r6.naked_shares + 0.4597) < 1e-3,
      f"{r6.naked_shares:.4f}")
check("分类为 OVERHEDGED", r6.classify() is ReconState.OVERHEDGED, r6.classify())

d = decide(r6)
check("两腿都有仓 -> TRADE_RESIDUAL（不猜该留哪条腿）", d.action is Action.TRADE_RESIDUAL, d)
check("residual 数量 = +0.3619", abs(d.size - 0.3619) < 1e-3, d.size)
check("交易腿是 OKX", d.leg == "OKX", d.leg)

print("\n=== 3. 零持仓正向确认（L3）——R6 的致命点 ===")
check("3 次稳定 + fills 可解释 -> 确认", confirm_zero(zero_reads(3))[0],
      confirm_zero(zero_reads(3))[1])
for name, reads in [
    ("只读 1 次", zero_reads(1)),
    ("消失无法被 fills 解释", zero_reads(3, explained=False)),
    ("仍有在途挂单", zero_reads(3, open_orders=1)),
    ("code != 0", zero_reads(3, code_ok=False)),
    ("返回结构异常", zero_reads(3, structure_ok=False)),
    ("中间一次读不到", [LegReading(0.0, ReadStatus.OK, 0.0, explained_by_fills=True),
                        LegReading(None, ReadStatus.UNKNOWN, 1.0),
                        LegReading(0.0, ReadStatus.OK, 2.0, explained_by_fills=True)]),
    ("窗口内出现非零", [LegReading(0.0, ReadStatus.OK, 0.0, explained_by_fills=True),
                        LegReading(0.0, ReadStatus.OK, 1.0, explained_by_fills=True),
                        LegReading(-1.858, ReadStatus.OK, 2.0, explained_by_fills=True)]),
]:
    c, why = confirm_zero(reads)
    check(f"{name} -> 拒绝", not c, why)

print("\n=== 4. 01:39 那一刻：OKX 读成空、但未确认 ===")
fake_zero = LegReading(0.0, ReadStatus.OK, 0.0, explained_by_fills=False)
confirmed, why = confirm_zero([fake_zero])
print(f"    confirm_zero -> {confirmed} ({why})")
check("假零不被确认", not confirmed)

led_or_none, err = build_ledger(ok(1.90), fake_zero,
                                hl_zero_confirmed=False, okx_zero_confirmed=False)
print(f"    build_ledger -> {led_or_none} ({err})")
check("未确认的零不能让账本成立", led_or_none is None, err)
d = decide(led_or_none, err)
check("-> MUST_CONFIRM（绝不平 HL）", d.action is Action.MUST_CONFIRM, d)

print("\n    对照 fail-open（R6 实际发生的事）:")
led_bad = HedgeLedger(q_hl=1.90, q_okx=0.0)
print(f"      把空列表当 0 -> naked={led_bad.naked_shares:+.4f} 股 -> 误判 ORPHAN")
check("fail-open 下 naked 被误算为 +1.90 股", abs(led_bad.naked_shares - 1.90) < 1e-6)
d_bad = decide(led_bad, okx_inflight_close=False)
check("错误路径确实 CLOSE_SURVIVOR（这就是 R6）",
      d_bad.action is Action.CLOSE_SURVIVOR and d_bad.leg == "HL", d_bad)

print("\n=== 5. 17:04 重启对账：只减多余腿（当时动作是对的）===")
at_1704 = HedgeLedger(q_hl=0.91, q_okx=-2.575)
check("residual = +1.8585（买回，留 -0.7165）",
      abs(at_1704.residual_okx - 1.8585) < 1e-3, f"{at_1704.residual_okx:.4f}")
d = decide(at_1704)
check("-> TRADE_RESIDUAL 买 +1.8585", d.action is Action.TRADE_RESIDUAL
      and abs(d.size - 1.8585) < 1e-3, d)
after = HedgeLedger(q_hl=0.91, q_okx=-2.575 + at_1704.residual_okx)
check("修正后 HEDGED", after.classify() is ReconState.HEDGED,
      f"{after.classify()} naked={after.naked_shares:+.6f}")

print("\n=== 6. 时间维度裸持止损（L6: 60/180/600s）===")
w = OrphanWatchdog()
t0 = 1000.0
check("刚暴露 -> 不报警", w.observe(t0, 1.90, False) is None)
check("59s -> 不报警", w.observe(t0 + 59, 1.90, False) is None)
m = w.observe(t0 + 61, 1.90, False)
check("61s -> WARN", bool(m) and m.startswith("WARN"), m)
check("179s 仍只是 WARN", (w.observe(t0 + 179, 1.90, False) or "").startswith("WARN"))
m = w.observe(t0 + 181, 1.90, False)
check("181s -> FORCE", bool(m) and m.startswith("FORCE"), m)
m = w.observe(t0 + 601, 1.90, False)
check("601s -> HALT", bool(m) and m.startswith("HALT"), m)

w2 = OrphanWatchdog()
w2.observe(t0, 1.90, False)
check("在途对冲单 -> 计时不计", w2.observe(t0 + 400, 1.90, True) is None)
check("对冲单消失后重新起算", w2.observe(t0 + 401, 1.90, False) is None)

print("\n=== 7. R6 时序回放：15.5 小时的裸持必被拦 ===")
w3 = OrphanWatchdog()
tripped = None
for sec in range(0, int(15.5 * 3600), 5):
    m = w3.observe(float(sec), r6.naked_shares, inflight_hedge=False)
    if m and m.startswith("HALT"):
        tripped = sec
        break
print(f"    裸持 {tripped}s 后 HALT（{tripped/60:.1f} 分钟）")
check(f"在 {NAKED_HALT_S:.0f}s 内被拦住", tripped is not None and tripped <= NAKED_HALT_S, tripped)

print("\n=== 8. 成交链完整性（HL startPosition 不变式）===")
chain_ok = [
    Fill("HL", +1.90, start_position=0.0, trade_id="a"),
    Fill("HL", -0.99, start_position=1.90, trade_id="b"),
]
check("连续成交链 -> ok", verify_fill_chain(chain_ok)[0], verify_fill_chain(chain_ok)[1])

chain_broken = [
    Fill("HL", +1.90, start_position=0.0, trade_id="a"),
    Fill("HL", -0.99, start_position=2.50, trade_id="c"),   # 中间漏了一条
]
okc, why = verify_fill_chain(chain_broken)
check("链断 -> 判为不可信", not okc, why)

check("成交隐含持仓 = start + sz",
      abs((fill_implied_position(chain_ok, "HL") or 0) - 0.91) < 1e-9,
      fill_implied_position(chain_ok, "HL"))
check("无成交时返回 None（不是 0）", fill_implied_position([], "OKX") is None)

print("\n=== 9. 快照 vs 成交链交叉校验（R6 的根治点）===")
# R5 遗留 -0.362，01:32 再卖 1.496 -> -1.858
r6_fills = [Fill("OKX", -1.496, start_position=-0.362, trade_id="x")]
imp = fill_implied_position(r6_fills, "OKX")
print(f"    成交隐含 OKX 持仓 = {imp:+.4f} 张")
check("成交链说 -1.858（不是 0）", abs(imp + 1.858) < 1e-9, imp)
okc, why = cross_check_position(0.0, r6_fills, "OKX")
check("快照=0 但成交链=-1.858 -> 冲突（必须 UNKNOWN，不得当零）", not okc, why)
okc, why = cross_check_position(imp, r6_fills, "OKX")
check("快照与成交链一致 -> ok", okc, why)

print("\n=== 10. 强平 / ADL 识别 ===")
check("bills 命中 105（强平）-> True", liquidation_or_adl_detected(bill_subtypes=["105"]))
check("positions-history type=5 -> True", liquidation_or_adl_detected(position_types=["5"]))
check("普通成交 subType -> False", not liquidation_or_adl_detected(bill_subtypes=["1"]))

print("\n=== 11. 外部干预（手动平/ADL）-> 隔离并收敛，不是忽略 ===")
drift, why = external_drift_detected(snapshot_qty=-1.758, ledger_qty=-1.858, unmatched_fill_count=0)
check("无对应成交的差异 -> EXTERNAL_DRIFT", drift, why)
drift, why = external_drift_detected(snapshot_qty=-1.758, ledger_qty=-1.858, unmatched_fill_count=1)
check("有对应成交 -> 不算漂移", not drift, why)

print()
if FAILS:
    print(f"FAILED {len(FAILS)}: {FAILS}")
    sys.exit(1)
print("ALL TESTS PASSED")