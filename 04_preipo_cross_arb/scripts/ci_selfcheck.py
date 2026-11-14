# -*- coding: utf-8 -*-
"""执行 CI yaml 里 lint + self-test 两段断言 (与 ci.yml 内容一致)。"""
import re, sys, random, math, time
from collections import deque

SRC = '/opt/nautilus_cluster/ant_arb/ant_cross_arb_service.py'
src = open(SRC, encoding="utf-8").read()

bad = []
expect = {
    "OKX_CONTRACT_SHARES": 1.005,
    "TOTAL_SHARES_B": 13.07,
    "ENTRY_SPREAD_BPS_FLOOR": 15.0,
    "ENTRY_GATE_PERCENTILE": 90.0,
    "ENTRY_GATE_WINDOW_SEC": 3600.0,
    "TARGET_NET_ROI_PCT": 1.5,
    "FRICTION_BPS": 18.0,
    "ASSUMED_SHARES_B": 13.07,
    "ROI_MIN_PCT": 1.0,
    "ROI_MAX_PCT": 5.0,
    "TIME_STOP_HOURS": 8.0,
}
for k, want in expect.items():
    m = re.search(rf"^\s*{k}\s*=\s*([\d.]+)", src, re.M)
    if not m:
        bad.append(f"{k} 缺失"); continue
    got = float(m.group(1))
    if abs(got - want) > 1e-9:
        bad.append(f"{k}={got} 期望 {want}")
for pat in ("okx_px * 10.0", "10.0 / s_now", "10.0 / _anchor"):
    if pat in src:
        bad.append(f"旧口径残留: {pat}")
if "TIME_STOP_HOURS = 8.0" not in src:
    bad.append("TIME_STOP_HOURS 未设为 8.0")
if "> self.cfg.TIME_STOP_HOURS * 3600" not in src:
    bad.append("时间止损未使用 TIME_STOP_HOURS 配置")
if "sorted(v for _, v in self._s_hist)" not in src:
    bad.append("_anchor_s 量纲 bug 复发(应取 _s_hist 中位数)")
_code = chr(10).join(l.split("#")[0] for l in src.split(chr(10)))
if "ROI_MIN_BPS" in _code or "dynamic_roi_target_pct" in _code:
    bad.append("sigma 地板已删除, 不应再出现")
if "self._hold_since = 0.0" not in src:
    bad.append("_hold_since 未在 __init__ 初始化")
print("LINT:", "PASS" if not bad else "FAIL")
for b in bad:
    print("  -", b)
if bad:
    sys.exit(1)

import importlib.util
spec = importlib.util.spec_from_file_location("ant", SRC)
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
E = object.__new__(m.AtomicDualLegEngineV4)
E.cfg = m.EngineConfigV4
E._sp_hist = []; E._spread_hist = deque(maxlen=20000); E._s_hist = deque(maxlen=20000)
E._anchor_s = E.cfg.ASSUMED_SHARES_B; E._anchor_dev_pct = 0.0
E._entry_dev_bps = 0.0; E._entry_gate_bps = 0.0; E._hold_since = 0.0
fails = []
tok = E.okx_per_share(102.36)
if abs(tok - 101.85) > 0.2: fails.append(f"每股等价 {tok}")
imp = E.compute_implied_s(101.96, 102.38)
if abs(imp/13.07 - 1) > 0.02: fails.append(f"隐含股本 {imp}")
ratio = 1.0/E.cfg.OKX_CONTRACT_SHARES
n_hl = 0.15*101.97; n_okx = 0.15*ratio*102.36
if abs(n_hl-n_okx)/n_hl > E.cfg.MAX_USD_DELTA_PCT: fails.append("配平偏差超限")
now = time.time()
for i in range(200):
    E.update_anchor(13.06 + random.uniform(-0.01, 0.01), -7.3, now-200+i)
if not (10.0 < E._anchor_s < 14.0):
    fails.append(f"_anchor_s 量纲错: {E._anchor_s}")
if abs(E._anchor_dev_pct) > 3.0:
    fails.append(f"_anchor_dev_pct 越界: {E._anchor_dev_pct}")
E._sp_hist = [(now-4000+i, random.gauss(0, 8)) for i in range(4000)]
g_lo = E.entry_gate_bps()
E._sp_hist = [(now-4000+i, random.gauss(0, 60)) for i in range(4000)]
g_hi = E.entry_gate_bps()
if g_lo > E.cfg.ENTRY_SPREAD_BPS_FLOOR + 0.01: fails.append(f"低波动门槛未落地 {g_lo}")
if g_hi <= g_lo + 20: fails.append(f"高波动门槛未抬升 {g_lo}->{g_hi}")
for d, want in ((10, 1.0), (38, 1.0), (60, 2.1), (118, 5.0), (500, 5.0)):
    E._entry_dev_bps = d
    tgt = E.entry_roi_target_pct()
    reach = max(0.0, (d - E.cfg.FRICTION_BPS)*0.05)
    exp = max(E.cfg.ROI_MIN_PCT, min(E.cfg.ROI_MAX_PCT, reach))
    if abs(tgt - exp) > 1e-9:
        fails.append(f"ROI 目标夹取错 d={d} tgt={tgt} expect={exp}")
MO = E.cfg.MIN_ORDER_USD
for hn, on, w in [(15.60, 15.79, False), (9.0, 15.0, True), (15.0, 9.0, True),
                  (5.0, 5.0, False), (40.0, 40.0, False)]:
    if (min(hn, on) < MO and max(hn, on) >= MO) != w:
        fails.append(f"残量判定错 {hn}/{on}")
msz = E._hl_min_sz(104.0)
if msz*104 < E.cfg.MIN_ORDER_USD - 1e-9: fails.append(f"最小手数 {msz}")
print("SELF-TEST:", "PASS" if not fails else "FAIL")
for f in fails:
    print("  -", f)
sys.exit(1 if fails else 0)
