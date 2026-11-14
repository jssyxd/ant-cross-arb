# -*- coding: utf-8 -*-
"""修复后自检: 关键参数 + 两个 bug 的修复验证 + 决策链。"""
import importlib.util, math, random, statistics as st, sys, time
from collections import deque

SRC = sys.argv[1] if len(sys.argv) > 1 else '/opt/nautilus_cluster/ant_arb/ant_cross_arb_service.py'
spec = importlib.util.spec_from_file_location("ant", SRC)
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)

fails = []
def chk(name, cond, detail=""):
    if cond:
        print(f"  OK   {name}  {detail}")
    else:
        print(f"  FAIL {name}  {detail}")
        fails.append(name)

C = m.EngineConfigV4
print("=" * 80)
print("1) 参数")
print("=" * 80)
chk("OKX_CONTRACT_SHARES=1.005", C.OKX_CONTRACT_SHARES == 1.005, f"{C.OKX_CONTRACT_SHARES}")
chk("TOTAL_SHARES_B=13.07", C.TOTAL_SHARES_B == 13.07, f"{C.TOTAL_SHARES_B}")
chk("ASSUMED_SHARES_B=13.07 (rebase后)", C.ASSUMED_SHARES_B == 13.07, f"{C.ASSUMED_SHARES_B}")
chk("ENTRY_SPREAD_BPS_FLOOR=15", C.ENTRY_SPREAD_BPS_FLOOR == 15.0, f"{C.ENTRY_SPREAD_BPS_FLOOR}")
chk("ENTRY_GATE_PERCENTILE=90", C.ENTRY_GATE_PERCENTILE == 90.0)
chk("ENTRY_GATE_WINDOW_SEC=3600", C.ENTRY_GATE_WINDOW_SEC == 3600.0)
chk("FRICTION_BPS=18", C.FRICTION_BPS == 18.0, f"{C.FRICTION_BPS}")
chk("ROI_MIN_PCT=1.0", C.ROI_MIN_PCT == 1.0)
chk("ROI_MAX_PCT=5.0", C.ROI_MAX_PCT == 5.0, f"{C.ROI_MAX_PCT}")
chk("ROI_MIN_BPS 已删除", not hasattr(C, 'ROI_MIN_BPS'))
chk("ROI_SIGMA_K 已删除", not hasattr(C, 'ROI_SIGMA_K'))
chk("TARGET_LEVERAGE=10", C.TARGET_LEVERAGE == 10)
chk("ANCHOR_MAX_DEV_PCT=3", C.ANCHOR_MAX_DEV_PCT == 3.0)

print()
print("=" * 80)
print("2) BUG 1: _anchor_s 量纲 (必须是股本B, 不能是价差bps)")
print("=" * 80)
E = object.__new__(m.AtomicDualLegEngineV4)
E.cfg = C
E._sp_hist = deque(maxlen=20000); E._spread_hist = deque(maxlen=20000)
E._s_hist = deque(maxlen=20000); E._anchor_s = C.ASSUMED_SHARES_B; E._anchor_dev_pct = 0.0
E._entry_dev_bps = 0.0; E._entry_gate_bps = 0.0; E._hold_since = 0.0
now = time.time()
for i in range(300):
    E.update_anchor(13.06 + random.uniform(-0.01, 0.01), -7.3 + random.uniform(-2, 2), now - 300 + i)
chk("_anchor_s 是股本量纲 (10~14B)", 10.0 < E._anchor_s < 14.0, f"_anchor_s={E._anchor_s:.4f}")
chk("_anchor_dev_pct 在 ±3% 内", abs(E._anchor_dev_pct) <= 3.0, f"{E._anchor_dev_pct:+.3f}%")
chk("anchor_ready()", E.anchor_ready())

print()
print("=" * 80)
print("3) BUG 2: 中枢与偏离同口径")
print("=" * 80)
src = open(SRC, encoding='utf-8').read()
chk("update_anchor 取 _s_hist 中位数", "sorted(v for _, v in self._s_hist)" in src)
chk("中枢样本用执行口径(bid/ask-6)", "self.okx_per_share(okx_bid) - hl_ask" in src)
chk("plan_dual_leg_position edge_bps 含 -6", "* 10000.0 - 6.0" in src.split("def plan_dual_leg_position")[1][:2000])
chk("旧 mid 口径中枢已移除",
    "_spread_abs_bps = self.compute_edge_bps(_hl_mid0, _okx_mid0)" not in src)

print()
print("=" * 80)
print("4) 决策链")
print("=" * 80)
chk("okx_per_share(102.36)=101.85", abs(E.okx_per_share(102.36) - 101.85) < 0.2, f"{E.okx_per_share(102.36):.4f}")
imp = E.compute_implied_s(101.96, 102.38)
chk("隐含股本 13.06B", abs(imp - 13.06) < 0.05, f"{imp:.4f}B")
ratio = 1.0 / C.OKX_CONTRACT_SHARES
chk("配平比率 0.995025", abs(ratio - 0.995025) < 1e-6, f"{ratio:.6f}")
E._entry_dev_bps = 60.0
chk("ROI目标 D=60 -> 2.10%", abs(E.entry_roi_target_pct() - (60-18)*0.05) < 1e-9, f"{E.entry_roi_target_pct():.3f}%")
E._entry_dev_bps = 10.0
chk("ROI目标 D=10 -> 夹到下限1.0%", abs(E.entry_roi_target_pct() - 1.0) < 1e-9, f"{E.entry_roi_target_pct():.3f}%")
E._entry_dev_bps = 200.0
chk("ROI目标 D=200 -> 夹到上限5.0%", abs(E.entry_roi_target_pct() - 5.0) < 1e-9, f"{E.entry_roi_target_pct():.3f}%")
chk("dynamic_roi_target_pct 已移除", not hasattr(E, 'dynamic_roi_target_pct'))
chk("时间止损 8h (TIME_STOP_HOURS=8.0)", C.TIME_STOP_HOURS == 8.0 and "> self.cfg.TIME_STOP_HOURS * 3600" in src)
chk("_hold_since 初始化", "self._hold_since = 0.0" in src)
chk("旧 ×10 口径无残留", "okx_px * 10" not in src and "10.0 / _anchor" not in src)

print()
print("=" * 80)
print("5) 门槛自适应")
print("=" * 80)
E._sp_hist = deque((time.time() - 4000 + i, random.gauss(0, 8)) for i in range(4000))
g_lo = E.entry_gate_bps()
E._sp_hist = deque((time.time() - 4000 + i, random.gauss(0, 60)) for i in range(4000))
g_hi = E.entry_gate_bps()
chk("低波动 -> 落地15bps", abs(g_lo - 15.0) < 0.01, f"{g_lo:.2f}")
chk("高波动 -> 抬升", g_hi > g_lo + 20, f"{g_lo:.2f} -> {g_hi:.2f}")

print()
print("=" * 80)
print("FAIL" if fails else "ALL CHECKS PASSED")
print("=" * 80)
sys.exit(1 if fails else 0)
