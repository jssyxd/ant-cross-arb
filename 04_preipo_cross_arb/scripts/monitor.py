#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ANT 交易机器人 监控任务 (Hermes cron 派发)。

检查项:
  1. 进程/服务健康 (systemd 状态, 重启次数, 磁盘, 内存)
  2. 配置版本校验 (md5 是否与部署基线一致, 关键参数是否被改动)
  3. 日志异常 (ERROR/CRITICAL, 新出现的 bug 模式)
  4. 交易状态 (当前持仓, 门禁状态, 价差)
  5. 交易所真实成交 (24h / 参数调整以来累计)
  6. 数据一致性 (隐含股本 vs 真实 13.07B, 价差中枢, 自适应门槛)
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from collections import Counter
from datetime import datetime, timezone, timedelta

CST = timezone(timedelta(hours=8))
ANT = "/opt/nautilus_cluster/ant_arb"
ERR = os.path.join(ANT, "ant_arb.err")
HL_ADDR = "0xAF3E93d42b56d517F055dDF64bcB4531c0B1E815"
OKX_INST = "ANTHROPIC-USDT-SWAP"
ENVF = "/etc/ant-arb.env"
H = {"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"}

# 部署基线 (改动配置后同步更新)
BASELINE_MD5 = "5e23ae16f856abfb33697f1b50933cb5"
EXPECT = {
    "OKX_CONTRACT_SHARES": 1.005,
    "TOTAL_SHARES_B": 13.07,
    "ENTRY_SPREAD_BPS_FLOOR": 15.0,
    "ENTRY_GATE_PERCENTILE": 90.0,
    "TARGET_NET_ROI_PCT": 1.5,
    "FRICTION_BPS": 18.0,
    "ASSUMED_SHARES_B": 13.07,
    "ROI_MIN_PCT": 1.0,
    "ROI_MAX_PCT": 5.0,
}

# 已知的历史 bug 模式 (修复后不应再出现)
KNOWN_BUGS = [
    ("'_hold_since' object has no attribute", "_hold_since 未初始化"),
    ("'_anchor' object has no attribute", "self._anchor 不存在 (应为 _anchor_s)"),
    ("'held' is not defined", "plan 里 held 未定义"),
    ("'max_notional' is not defined", "plan 里 max_notional 未定义"),
    ("偏离自适应锚 -2", "锚量纲 bug 复发 (_anchor_s 被赋成价差bps)"),
    ("偏离自适应锚 -3", "锚量纲 bug 复发 (_anchor_s 被赋成价差bps)"),
]


def sh(cmd, timeout=25):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip()
    except Exception:
        return ""


def fnum(x):
    try:
        return float(x)
    except Exception:
        return 0.0


def get_env(path):
    env = {}
    try:
        for line in open(path, errors="replace"):
            line = line.strip().replace("export ", "")
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env[k] = v.strip().strip('"').strip("'")
    except Exception:
        pass
    return env


def post_hl(b):
    r = urllib.request.Request("https://api.hyperliquid.xyz/info",
                              data=json.dumps(b).encode(), headers=H)
    return json.loads(urllib.request.urlopen(r, timeout=25).read())


def okx_get(path, params):
    q = "&".join(f"{k}={v}" for k, v in params.items())
    return json.loads(urllib.request.urlopen(
        urllib.request.Request(f"https://www.okx.com/api/v5/{path}?{q}", headers=H),
        timeout=25).read())


L = []
WARN = []


def out(s=""):
    L.append(s)


def warn(s):
    WARN.append(s)
    out("  !! " + s)


n = datetime.now(CST)
out("=" * 46)
out("ANT 交易机器人 · 监控报告")
out(f"{n.strftime('%Y-%m-%d %H:%M:%S')} (UTC+8)")
out("=" * 46)

# ---------- 1. 服务健康 ----------
out("\n【1】服务与主机")
act = sh("systemctl is-active ant-arb")
out(f"  ant-arb: {act}   重启次数 {sh('systemctl show ant-arb -p NRestarts --value')}   "
    f"启动于 {sh('systemctl show ant-arb -p ExecMainStartTimestamp --value')}")
if act != "active":
    warn(f"ant-arb 未运行: {act}")
out(f"  hermes-gateway: {sh('systemctl is-active hermes-gateway')}")
disk = sh("df -h / | tail -1 | awk '{print $4\" 可用 (\"$5\")\"}'")
out(f"  磁盘: {disk}")
mem = sh("free -m | awk 'NR==2{print $7\" MB 可用 / \"$2\" MB\"}'")
out(f"  内存: {mem}")
_df = sh("df -h / | tail -1")
_pct = 0
try:
    _pct = int(_df.split()[-2].rstrip("%"))
except Exception:
    pass
if _pct > 90:
    warn("磁盘占用 %d%% (>90%%)" % _pct)

# ---------- 2. 配置版本 ----------
out("\n【2】配置版本与关键参数")
md5 = sh(f"md5sum {ANT}/ant_cross_arb_service.py | awk '{{print $1}}'")
if md5 == BASELINE_MD5:
    out(f"  ✓ 配置 md5 与部署基线一致 ({md5[:12]}...)")
else:
    warn(f"配置已被改动: md5 {md5[:12]}... != 基线 {BASELINE_MD5[:12]}...")
src = open(os.path.join(ANT, "ant_cross_arb_service.py"), encoding="utf-8", errors="replace").read()
for k, want in EXPECT.items():
    m = re.search(rf"^\s*{k}\s*=\s*([\d.]+)", src, re.M)
    got = float(m.group(1)) if m else None
    if got is None:
        warn(f"参数 {k} 未找到")
    elif abs(got - want) > 1e-9:
        warn(f"参数 {k} = {got}, 期望 {want}")
    else:
        out(f"  ✓ {k} = {got}")

# ---------- 3. 日志异常 ----------
out("\n【3】日志异常")
hb = None
errs = []
new_bugs = []
lines = []
try:
    with open(ERR, errors="replace") as f:
        lines = f.readlines()[-30000:]
except Exception:
    pass
HB = re.compile(r"HL: \$([\d.]+)/\$([\d.]+) \| OKX: \$([\d.]+)/\$([\d.]+)")
for ln in lines:
    if "WS Heartbeat" in ln:
        hb = ln.strip()
    if "[ERROR]" in ln or "[CRITICAL]" in ln:
        errs.append(ln.strip())
recent = errs[-40:]
out(f"  最近 40000 行内 ERROR/CRITICAL: {len(errs)} 条 (显示最近 5 条)")
for e in recent[-5:]:
    out("   " + e[:170])
for pat, desc in KNOWN_BUGS:
    c = sum(1 for e in errs if pat in e)
    if c:
        # 只看最近的 200 条, 避免历史遗留误报
        c_recent = sum(1 for e in errs[-200:] if pat in e)
        if c_recent:
            new_bugs.append(f"{desc} ({pat}) 仍出现 {c_recent} 次")
for b in new_bugs:
    warn(f"已知 bug 复发: {b}")

# ---------- 4. 交易状态 ----------
out("\n【4】交易状态")
if hb:
    m = re.search(r"跨所价差: ([+-][\d.]+)bps", hb)
    m2 = re.search(r"偏离中枢: ([+-][\d.]+)bps", hb)
    m3 = re.search(r"自适应门槛 (\d+)", hb)
    m4 = re.search(r"方向: ([^|]+)\|", hb)
    m5 = re.search(r"隐含S: ([\d.]+)B", hb)
    m6 = re.search(r"价差中枢: ([+-][\d.]+)bps", hb)
    m7 = re.search(r"HL: \$([\d.]+)/\$([\d.]+)", hb)
    m8 = re.search(r"OKX: \$([\d.]+)/\$([\d.]+)", hb)
    out(f"  HL {m7.group(1) if m7 else '?'}/{m7.group(2) if m7 else '?'}   "
        f"OKX {m8.group(1) if m8 else '?'}/{m8.group(2) if m8 else '?'}")
    out(f"  跨所价差 {m.group(1) if m else '?'}bps   价差中枢 {m6.group(1) if m6 else '?'}bps   "
        f"偏离中枢 {m2.group(1) if m2 else '?'}bps   自适应门槛 {m3.group(1) if m3 else '?'}bps")
    out(f"  方向: {m4.group(1).strip() if m4 else '?'}   隐含S {m5.group(1) if m5 else '?'}B (真实 13.07B)")
    if m5 and abs(float(m5.group(1)) / 13.07 - 1) > 0.05:
        warn(f"隐含股本 {m5.group(1)}B 偏离真实 13.07B 超 5% → 口径可能被交易所再次 rebase")
else:
    warn("无心跳 → 服务可能已停")

pos = sh(f"grep '全量健康巡检' {ERR} | tail -1")
if pos:
    m = re.search(r"HL仓位: ([+-][\d.]+)股.*?OKX仓位: ([+-][\d.]+)张", pos)
    if m:
        qh, qo = float(m.group(1)), float(m.group(2))
        out(f"  持仓: HL {qh:+.2f} 股 / OKX {qo:+.3f} 张")
        if abs(qh) > 0.05 or abs(qo) > 0.005:
            out(f"    (持仓中 → 4h 时间止损在跑, 剩余 {max(0, 4 - (time.time()-0)/3600):.1f}h 量级)")
    else:
        out("  " + pos[24:150])

# ---------- 5. 交易所真实成交 ----------
out("\n【5】交易所真实成交")


def fills_since(since_ms):
    hl_f = []
    try:
        d = post_hl({"type": "userFillsByTime", "user": HL_ADDR,
                     "startTime": int(since_ms), "endTime": int(time.time() * 1000)})
        hl_f = [x for x in d if x.get("coin") == "xyz:ANT"]
    except Exception:
        pass
    ok_f = []
    try:
        env = get_env(ENVF)
        sys.path.insert(0, "/opt/nautilus_cluster/shared")
        from gateway_okx_client import OKXGatewayClient
        c = OKXGatewayClient(env["OKX_API_KEY"], env["OKX_API_SECRET"], env["OKX_PASSPHRASE"])
        cur = {"instType": "SWAP", "instId": OKX_INST, "begin": str(int(since_ms)),
               "end": str(int(time.time() * 1000)), "limit": "100"}
        for _ in range(20):
            r = c._sign_and_request("GET", "/api/v5/trade/fills-history", cur)
            d = r.get("data") or []
            if not d:
                break
            ok_f += d
            if len(d) < 100:
                break
            cur = dict(cur); cur["after"] = d[-1]["billId"]; time.sleep(0.15)
    except Exception:
        pass
    return hl_f, ok_f


def summarize(hl_f, ok_f):
    hn = sum(fnum(x.get("closedPnl")) - fnum(x.get("fee")) for x in hl_f)
    on = sum(fnum(x.get("fillPnl")) + fnum(x.get("fee")) for x in ok_f)
    return len(hl_f), len(ok_f), hn, on


for lbl, secs in (("过去 24 小时", 86400), ("参数调整以来 (11-14 16:48)", 0)):
    if secs == 0:
        continue
    hf, of = fills_since((time.time() - secs) * 1000)
    hn, on, hnet, onet = summarize(hf, of)
    out(f"  {lbl}: HL {hn} 笔 净 {hnet:+.4f} USDC | OKX {on} 笔 净 {onet:+.4f} USDT | 合计 {hnet + onet:+.4f}")
    if hn == 0 and on == 0:
        out("    (无成交)")

# ---------- 6. 数据一致性 ----------
out("\n【6】数据一致性")
try:
    tk = okx_get("market/ticker", {"instId": OKX_INST})["data"][0]
    idx = okx_get("market/index-tickers", {"instId": "ANTHROPIC-USDT"})["data"][0]
    b = post_hl({"type": "l2Book", "coin": "xyz:ANT", "dex": "xyz"})
    hl_bid, hl_ask = float(b["levels"][0][0]["px"]), float(b["levels"][1][0]["px"])
    okx_bid, okx_ask = float(tk["bidPx"]), float(tk["askPx"])
    C = EXPECT["OKX_CONTRACT_SHARES"]
    tok_bid = okx_bid / C
    sprd = (tok_bid - hl_ask) / hl_ask * 1e4
    out(f"  OKX 合约 {okx_bid}/{okx_ask}  指数 {idx['idxPx']}  HL {hl_bid}/{hl_ask}")
    out(f"  OKX 每股等价(bid) {tok_bid:.3f}  跨所价差(买HL卖OKX) {sprd:+.1f}bps")
    implied = tok_bid / hl_bid * EXPECT["TOTAL_SHARES_B"]
    out(f"  隐含总股本 {implied:.3f}B (真实 13.07B, 偏差 {(implied/13.07-1)*100:+.2f}%)")
    if abs(implied / 13.07 - 1) > 0.03:
        warn(f"隐含股本偏差 {(implied/13.07-1)*100:+.2f}% → OKX 疑似再次 rebase, "
             f"需更新 OKX_CONTRACT_SHARES (当前 {C})")
except Exception as e:
    warn(f"交易所数据拉取失败: {e}")

out("\n" + "=" * 46)
if WARN:
    out(f"⚠️ 发现 {len(WARN)} 项异常:")
    for w in WARN:
        out("  - " + w)
else:
    out("✅ 未发现异常")
out("=" * 46)

print("\n".join(L))