# -*- coding: utf-8 -*-
"""bt8: 4h 时间止损的逐笔归因 + 采样密度敏感性 + 结论汇总。"""
import re, math, statistics as st, time
from collections import deque
from datetime import datetime, timezone, timedelta

CST = timezone(timedelta(hours=8))
LOG = '/opt/nautilus_cluster/ant_arb/ant_arb.err'
BRK = 1794477840000 / 1000.0
C_POST, C_PRE = 1.005, 1.3245
SHARES = 13.07
LEV, FLOOR, PCTL = 10.0, 15.0, 90.0
GATE_WIN, ANCHOR_WIN, ANCHOR_MIN, GATE_MIN = 3600.0, 7200.0, 120, 200
Q_HL = 0.15
HL_FEE, OKX_FEE = 0.90, 5.00
FR_REAL, FR_PROD = 15.7, 18.0

HB = re.compile(r"HL: \$([\d.]+)/\$([\d.]+) \| OKX: \$([\d.]+)/\$([\d.]+)")
TS = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
rows = []
for ln in open(LOG, errors='replace'):
    if 'WS Heartbeat' not in ln: continue
    m, h = TS.match(ln), HB.search(ln)
    if not m or not h: continue
    a, b, c, d = map(float, h.groups())
    rows.append({'t': time.mktime(time.strptime(m.group(1), '%Y-%m-%d %H:%M:%S')),
                 'hl_b': a, 'hl_a': b, 'ok_b': c, 'ok_a': d})
rows.sort(key=lambda r: r['t'])
ded = []
for r in rows:
    if not ded or ded[-1]['t'] != r['t']: ded.append(r)
rows = ded
post = [r for r in rows if r['t'] >= BRK]

def cal(r): return C_POST if r['t'] >= BRK else C_PRE
def exec_sprd(r): return ((r['ok_b']/cal(r)-r['hl_a'])/r['hl_a'])*1e4-6.0

def run(data, cap_h, friction=FR_REAL, roi_lo=1.0, roi_hi=5.0, min_gap_sec=0):
    """min_gap_sec>0: 模拟采样更稀 (生产 1s, 日志 ~11-20s)"""
    sh = deque(); sph = deque(); tr = []; pos = None; last_t = None
    for r in data:
        t = r['t']
        if min_gap_sec and last_t and t - last_t < min_gap_sec:
            continue
        last_t = t
        es = exec_sprd(r)
        sh.append((t, es))
        while sh and sh[0][0] < t-ANCHOR_WIN: sh.popleft()
        anchor = None
        if len(sh) >= ANCHOR_MIN:
            v = sorted(x for _, x in sh); anchor = v[len(v)//2]
        dev = (es-anchor) if anchor is not None else 0.0
        sph.append((t, dev))
        while sph and sph[0][0] < t-GATE_WIN: sph.popleft()
        if pos is not None:
            hm = (r['hl_b']+r['hl_a'])/2; om = (r['ok_b']+r['ok_a'])/2
            pnl = pos['qh']*(hm-pos['he']) + pos['qo']*(pos['oe']-om)
            mg = (pos['qh']*pos['he']/LEV + pos['qo']*pos['oe']/LEV)
            roi = pnl/mg*100.0
            held = (t-pos['t0'])/3600.0
            forced = cap_h is not None and held >= cap_h
            if roi >= pos['tg'] or forced:
                px = pos['qh']*(r['hl_b']-pos['he']) + pos['qo']*(pos['oe']-r['ok_a'])
                fee = (pos['qh']*pos['he']*HL_FEE*2 + pos['qo']*pos['oe']*OKX_FEE*2)/1e4
                tr.append({'t0': pos['t0'], 't1': t, 'dev': pos['dev'], 'tg': pos['tg'],
                           'roi_mid': roi, 'roi': (px-fee)/mg*100.0, 'fee_drag': roi-(px-fee)/mg*100.0,
                           'held': held, 'forced': forced, 'usd': px-fee})
                pos = None
            continue
        if anchor is None: continue
        vals = sorted(v for _, v in sph)
        gate = FLOOR if len(vals) < GATE_MIN else max(
            FLOOR, vals[min(len(vals)-1, int(len(vals)*PCTL/100.0))])
        if dev < gate: continue
        reach = max(0.0, (dev-friction)*0.05)
        tg = min(roi_hi, max(roi_lo, reach))
        qh = Q_HL; qo = qh/cal(r)
        pos = {'t0': t, 'qh': qh, 'qo': qo, 'he': r['hl_a'], 'oe': r['ok_b'],
               'dev': dev, 'tg': tg}
    return tr

print("=" * 100)
print("1) 时间止损逐笔归因: 4h 强平 vs 不加止损")
print("=" * 100)
a4 = run(post, 4.0)
an = run(post, None)
key = lambda x: x['t0']
m4 = {x['t0']: x for x in a4}
mn = {x['t0']: x for x in an}
print(f"  {'进场':>12} {'D':>7} {'目标':>6} | {'4h:持/ROI/费损':>24} | {'不限:持/ROI/费损':>24}")
for x in sorted(an, key=key):
    y = m4.get(x['t0'])
    s4 = (f"{y['held']:.2f}h {y['roi']:+.2f}%{'⏱' if y['forced'] else ''}"
          if y else "—")
    sn = f"{x['held']:.2f}h {x['roi']:+.2f}%"
    print(f"  {datetime.fromtimestamp(x['t0'], CST):%m-%d %H:%M} {x['dev']:+6.1f} {x['tg']:>5.2f}% | "
          f"{s4:>24} | {sn:>24}")
only4 = [x for x in a4 if x['forced']]
print()
print(f"  4h 强平 {len(only4)} 轮:")
for x in only4:
    print(f"    {datetime.fromtimestamp(x['t0'], CST):%m-%d %H:%M} D={x['dev']:+.1f}bps 目标{x['tg']:.2f}% "
          f"4h强平ROI {x['roi']:+.2f}%")
    if x['t0'] in mn:
        y = mn[x['t0']]
        print(f"      -> 不加止损: 持 {y['held']:.2f}h 最终 ROI {y['roi']:+.2f}% "
              f"(差 {y['roi']-x['roi']:+.2f}%)")

print()
print("=" * 100)
print("2) 汇总表: 修正口径 + 用户公式 (D-18)*0.05 夹[1%,5%] + 真实摩擦 15.7")
print("=" * 100)
print(f"  {'上限':>6} {'轮':>3} {'胜率':>5} {'ROI(mid口径)':>13} {'ROI(扣费)':>11} {'PnL$':>9} {'强平':>5} {'费损/笔':>8}")
for cap in (None, 2.0, 3.0, 4.0, 6.0, 8.0, 12.0, 24.0):
    tr = run(post, cap)
    if not tr:
        print(f"  {'无' if cap is None else f'{cap:g}h':>6}: 无交易"); continue
    n = len(tr)
    print(f"  {'无' if cap is None else f'{cap:g}h':>6} {n:>3} "
          f"{sum(1 for x in tr if x['roi']>0)/n*100:>4.0f}% "
          f"{sum(x['roi_mid'] for x in tr):>+12.2f}% {sum(x['roi'] for x in tr):>+10.2f}% "
          f"{sum(x['usd'] for x in tr):>+8.4f} {sum(1 for x in tr if x['forced']):>5} "
          f"{st.median([x['fee_drag'] for x in tr]):>7.2f}%")

print()
print("=" * 100)
print("3) 采样密度敏感性 (生产 _sp_hist 是 1s/点, 日志心跳 ~11-20s/点)")
print("=" * 100)
print(f"  {'抽样间隔':>8} {'轮':>3} {'胜率':>5} {'ROI(扣费)':>11} {'强平':>5}")
for gap in (0, 30, 60, 120, 300):
    tr = run(post, 4.0, min_gap_sec=gap)
    if not tr:
        print(f"  {'全量' if gap==0 else f'{gap}s':>8}: 无交易"); continue
    n = len(tr)
    tag = '全量(~11s)' if gap == 0 else f'{gap}s'
    print(f"  {tag:>8} {n:>3} {sum(1 for x in tr if x['roi']>0)/n*100:>4.0f}% "
          f"{sum(x['roi'] for x in tr):>+10.2f}% {sum(1 for x in tr if x['forced']):>5}")

print()
print("=" * 100)
print("4) 门槛地板敏感性 (修正口径 + 用户公式 + 4h)")
print("=" * 100)
for fl in (10.0, 12.0, 15.0, 18.0, 20.0):
    FLOOR_S = FLOOR; globals()['FLOOR'] = fl
    tr = run(post, 4.0); globals()['FLOOR'] = FLOOR_S
    if not tr:
        print(f"  地板 {fl:>4.0f}bps: 无交易"); continue
    n = len(tr)
    print(f"  地板 {fl:>4.0f}bps: {n:>2}轮 胜率{sum(1 for x in tr if x['roi']>0)/n*100:>4.0f}% "
          f"ROI(扣费){sum(x['roi'] for x in tr):>+7.2f}% PnL${sum(x['usd'] for x in tr):>+8.4f} "
          f"强平{sum(1 for x in tr if x['forced'])}")
