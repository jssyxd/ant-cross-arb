# -*- coding: utf-8 -*-
"""
ant_cross_arb_v4_adaptive.py - 工业级 ANT 跨所双腿套利自适应复利与高阶风控引擎
=====================================================================================================
核心架构与四大系统级升级：
1. 开平仓闭环与波段收割飞轮 (Mean-Reverting Basis Swing & Spread Exit):
   - 开仓门禁：Net Spread >= 750 bps (当前盘口约 830 bps)；
   - 波段止盈平仓：当反向利差回落至 <= 500 bps 时，立即并发双平，吃下 250~300 bps 差价波动收益；
   - 终极收敛平仓：当 SEC S-1 披露或上市使得基差归零 (<= 50 bps) 时执行清仓交割。
2. 双腿合计净利润率止盈守卫 (Combined PnL Take-Profit Gate):
   - 实时监控两腿综合未实现盈亏与已占用保证金；
   - 当两腿合计净收益率 >= 3.0% (例如 HL 亏 6% 而 OKX 盈 9.8%)，立即市价双平锁定确定性利润并释放资金！
3. 极端非对称强平硬断路器 (Single-Leg 8% Price Deviation Breaker):
   - 监控任一腿价格相对开仓加权均价的变动率 |P_cur - P_entry| / P_entry；
   - 任一腿变动 >= 8.0% (在 10x 杠杆下对应 80% 权益波动，逼近交易所强平红线) 时，立即并发市价双平，规避单边穿仓。
4. 孤儿单自动对冲与同步清理器 (Orphan Leg Auto-Reconciler):
   - 后台每 5 秒校验底层真实持仓：若检测到一侧仓位归零 (因外力强平、止损或人工平仓)，而另一侧仍有裸持仓位；
   - 立即向存留侧发送市价单强平，将净敞口瞬间归零，杜绝单腿裸奔，随后重置状态重新进入监控。
5. 木桶短板自适应复利算法 (Bottleneck Adaptive Sizing):
   - 动态读取双边可用现金，取木桶短板 W_bottleneck = min(W_HL, W_OKX)；
   - 设定总持仓名义价值 <= 2.5 * W_bottleneck (保留 75% 游隙现金抵御波动)；
   - 单笔切片受 11.5 USD 保底与 1,500 USD 盘口前三档深度封顶保护，实现利润自动滚存复利。
"""

import os
import sys
import time
import json
import math
import logging
import asyncio
import threading
from typing import Dict, Any, Tuple, Optional
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s"
)
logger = logging.getLogger("ANT-Arb-V4-Adaptive")
ENGINE_HL_SYMBOL = "xyz:ANT"

sys.path.insert(0, "/opt/nautilus_cluster/shared")
try:
    from gateway_okx_client import OKXGatewayClient
except ImportError:
    OKXGatewayClient = None

try:
    import websockets
except ImportError:
    websockets = None

try:
    from hyperliquid.info import Info
    from hyperliquid.exchange import Exchange
    from hyperliquid.utils import constants
    import eth_account
except ImportError:
    Info, Exchange, constants, eth_account = None, None, None, None


class EngineConfigV4:
    # 标的定义
    HL_SYMBOL = "xyz:ANT"
    OKX_INST_ID = "ANTHROPIC-USDT-SWAP"
    REFILL_SYMBOL = "BTC"
    
    # 股本假设与配平系数
    ASSUMED_SHARES_B = 12.7       # 仅作无实时 S 时的兜底
    HEDGE_RATIO = 10.0 / ASSUMED_SHARES_B  # 兜底比率 ~0.7874
    DYNAMIC_HEDGE_RATIO = True    # 开仓按隐含 S 实时配平, 消除 S 漂移的方向性敞口
    HEDGE_RATIO_S_OVERRIDE = 0.0  # 人工锁定配平股本(十亿股), 0=不覆盖
    HEDGE_RATIO_BOUNDS = (0.55, 0.90)  # 动态比率硬区间 (S 约 11.1B~18.2B), 越界则放弃开仓
    REF_SHARES_B = 12.70          # [2026-11-08] 统一到 12.7，与 ASSUMED_SHARES_B/HEDGE_RATIO 同锚
    
    # 核心利差门禁
    ENTRY_SPREAD_BPS = 750.0            # 开仓利差门槛 (当前约为 830 bps)
    # [2026-11-11] EXIT_SWING_PROFIT_BPS 已删除: 7天 S 最低 13.22B=520bps, 从未触发
    # [2026-11-11] EXIT_TERMINAL_CONVERGE_BPS 已删除: 统一由 ROI 管理出场
    
    # 收益与风控红线
    TARGET_NET_ROI_PCT = 8.0            # 双腿占用保证金净收益率 >= 8.0% 立即止盈双平 (4h 价差 sigma 约 7.6% of margin, 门槛需高于 1 sigma)
    SINGLE_LEG_MAX_PRICE_DRIFT = 0.16   # 单腿价格偏离开仓均价 16%，强制双平止损 (账户保证金使用率上限 50%, 可承受)
    MAX_EFFECTIVE_LEVERAGE = 5.0        # 总持仓有效杠杆上限提升至 5.0x，保留 50% 现金游隙抵御极端波动
    
    # 滑点与费率
    MAX_SLIPPAGE_BPS = 5.0              # 单腿最大滑点容忍 (开仓, 5bps)
    # ===== [2026-11-09] 风控硬约束 =====
    MAX_USD_DELTA_PCT = 0.005            # 开仓计划两腿美元净敞口 / 单腿名义 < 0.5%
    MAX_NAKED_USD_PCT_LEG = 0.05         # 未对冲敞口 / 单腿名义 上限
    MAX_NAKED_USD_PCT_EQ = 0.10          # 未对冲敞口 / 单腿权益 上限
    MAX_LEG_MARGIN_PCT = 0.50            # 单腿保证金占用上限（名义 / (权益*杠杆)）
    QUOTA_HALT_SEC = 1800                # 命中 HL 配额错误后禁开仓时长

    # ===== [2026-11-09] 补偿腿（top-up）=====
    # 裸敞口超限时不做"完全不开仓"，而是在价差达标时只把「缺的那条腿」补齐到美元中性；
    # 本路径只允许减小 |net_delta|，永不新增净敞口。
    TOPOUT_TRIGGER_USD = 5.0             # 裸敞口超过该金额才补腿
    TOPOUT_COOLDOWN_SEC = 15.0           # 每次补腿后的冷却
    TOPOUT_SLIPPAGE_BPS = 5.0            # 补腿用的主动滑点（只求成交，不求价格）

    # ===== 平仓滑点与拆单控制 =====
    CLOSE_SLIPPAGE_BPS = 0.0            # [2026-11-08] 平仓不接受滑点：只按盘口价成交
    # [2026-11-09] P0：平仓滑点改为「时间预算阶梯」(bps, 累计截止秒)。
    # 0 档先白等 200ms；超时逐级放宽；1.0s 用 50bps 必成。依据：HL 写入延迟 ~380ms，
    # 超过 1s 未成交说明盘口真走，此时裸腿风险远大于 10bps 成本。
    CLOSE_SLIPPAGE_LADDER = ((0.0, 0.20), (5.0, 0.50), (20.0, 0.80), (50.0, 1.00))
    CLOSE_RETRY_SLIPPAGE_BPS = (0.0, 5.0, 20.0, 50.0)     # 兼容旧引用
    CLOSE_LIMIT_ONLY_MAX_BPS = 80.0     # 非强制平仓 (止盈/波段) 最高只用到该档, 超出即放弃不市价
    CLOSE_DEPTH_PCT = 0.30              # 单笔拆单量 <= 买一档深度 * 该比例
    CLOSE_CHUNK_INTERVAL_SEC = 0.25     # 拆单间隔(秒)
    CLOSE_BATCH_PCT = 0.50              # 同步平仓每批占「两腿较小可平量」的比例
    CLOSE_MAX_ROUNDS = 12               # 同步平仓最大轮数
    CLOSE_TAIL_MIN_HL = 0.05            # 收尾批允许平掉的 HL 下限(股), 防止无限细分
    INSTANT_DRIFT_BPS = 8.5             # 瞬时漂移防狙击
    MAX_CARRY_LOSS_APR = 10.0           # 资金费率净支出最大容忍 10% APR
    TARGET_LEVERAGE = 10                # 双边统一打满 10x 杠杆，1:1 保证金占用对称
    
    # 自适应单笔切片约束
    MIN_ORDER_USD = 11.5                # HL 最小 10 USD 名义价值保底
    # ===== 双边名义统一约束 (取代单边 HL 名义口径) =====
    MAX_MARGIN_USAGE_PCT = 0.50         # 单腿各自保证金上限: 每腿名义 <= 本账户权益 * 该值 * 杠杆
    CLIP_FRACTION = 0.08                # 每笔切片占剩余可用名义的比例
    # [2026-11-09] PRICE_EDGE_MIN_BPS 已删除（与 ENTRY_SPREAD_BPS 同锚后完全冗余）
    MAX_LOB_DEPTH_CLIP_USD = 1500.0     # 单笔切片名义上限
    OPEN_DEPTH_TIERS = 3                # 开仓深度预检取卖盘前几档
    OPEN_DEPTH_PCT = 0.30               # 单笔开仓最多吃掉前 N 档厚度的该比例
    
    # 冷却周期
    COOLDOWN_AFTER_FILL_SEC = 15.0      # 成交后冷却期
    SAFETY_FAILURE_COOLDOWN_SEC = 12.0  # 未撮合时 12 秒安全冷却 (遵守 10s 写入约束)
    EMERGENCY_STOP_COOLDOWN_SEC = 60.0  # 触发 16% 单腿止损后的防震荡冷静期
    HL_WS_MAX_SILENCE_SEC = 20.0        # HL WS 静默超时阈值，触发强制重订阅

    # 隐含股本 S 监控 (S = OKX 价 * 10 / HL 价，单位十亿股)
    IMPLIED_S_ALERT_B = 14.00           # 超过此值仅告警，市场在调高股本预期
    # [已停用 2026-11-08] MAX_ENTRY_IMPLIED_S_B = 15.20  # 开仓 S 上限与自身硬熔断冲突，见下方门禁注释
    IMPLIED_S_DELEVER_B = 99.00         # [已停用 2026-11-08] 原 14.50：无一次性开关，S>=14.5 时每轮减半→75 秒清仓→立刻重开，7 天回测 490 笔往返。现只保留硬熔断 16.50 与 ROI/价差出场。
    IMPLIED_S_HARD_LIMIT_B = 16.50      # [2026-11-08] 15.20 -> 16.50：避开与开仓门禁同值造成的'开了就砍'


class SharedOrderbookState:
    """双所内存共享盘口与状态缓存 (由 WebSocket 纯异步更新)"""
    def __init__(self):
        self.hl_bid_px = 0.0
        self.hl_bid_sz = 0.0
        self.hl_ask_px = 0.0
        self.hl_ask_sz = 0.0
        self.hl_last_ts = 0.0
        self.hl_updates = 0
        
        self.okx_bid_px = 0.0
        self.okx_bid_sz = 0.0
        self.okx_ask_px = 0.0
        self.okx_ask_sz = 0.0
        self.okx_last_ts = 0.0
        self.okx_updates = 0
        
        self.running = True


class DualExchangeWebSocketManager:
    """双所 WebSocket 监听中继器"""
    def __init__(self, state: SharedOrderbookState, hl_info: Any, okx_inst_id: str):
        self.state = state
        self.hl_info = hl_info
        self.okx_inst_id = okx_inst_id
        self.hl_max_silence_sec = EngineConfigV4.HL_WS_MAX_SILENCE_SEC
        self._threads = []

    def start(self):
        t_hl = threading.Thread(target=self._run_hl_ws, daemon=True, name="HL-WS-Thread")
        t_hl.start()
        self._threads.append(t_hl)

        t_okx = threading.Thread(target=self._run_okx_ws_thread, daemon=True, name="OKX-WS-Thread")
        t_okx.start()
        self._threads.append(t_okx)

        logger.info("双所纯 WebSocket 监听中继器已成功启动！")

    def _run_hl_ws(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(self._hl_ws_coro())

    async def _hl_ws_coro(self):
        url = "wss://api.hyperliquid.xyz/ws"
        while self.state.running:
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=10) as ws:
                    await ws.send(json.dumps({
                        "method": "subscribe",
                        "subscription": {"type": "l2Book", "coin": ENGINE_HL_SYMBOL}
                    }))
                    self.state.hl_last_ts = time.time()
                    logger.info("Hyperliquid " + ENGINE_HL_SYMBOL + " L2Book 订阅成功 (原生 WS)")
                    while self.state.running:
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=30.0)
                        except asyncio.TimeoutError:
                            await ws.send(json.dumps({"method": "ping"}))
                            continue
                        if raw in ("pong", "{}"):
                            continue
                        msg = json.loads(raw)
                        if msg.get("channel") != "l2Book":
                            continue
                        data = msg.get("data") or {}
                        if not isinstance(data, dict):
                            continue
                        levels = data.get("levels") or [[], []]
                        if len(levels) < 2 or not levels[0] or not levels[1]:
                            continue
                        self.state.hl_bid_px = float(levels[0][0]["px"])
                        self.state.hl_bid_sz = float(levels[0][0]["sz"])
                        self.state.hl_ask_px = float(levels[1][0]["px"])
                        self.state.hl_ask_sz = float(levels[1][0]["sz"])
                        self.state.hl_last_ts = time.time()
                        self.state.hl_updates += 1
            except Exception as e:
                logger.warning(f"HL WS 断开，2 秒后重连: {e}")
                await asyncio.sleep(2.0)

    def _run_okx_ws_thread(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(self._okx_ws_coro())

    async def _okx_ws_coro(self):
        url = "wss://ws.okx.com:443/ws/v5/public"
        while self.state.running:
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=10) as ws:
                    sub_payload = {
                        "op": "subscribe",
                        "args": [{"channel": "bbo-tbt", "instId": self.okx_inst_id}]
                    }
                    await ws.send(json.dumps(sub_payload))
                    logger.info("OKX ANTHROPIC-USDT-SWAP bbo-tbt 订阅请求已发送")

                    while self.state.running:
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=25.0)
                            if raw == "pong":
                                continue
                            msg = json.loads(raw)
                            if "data" in msg and msg["data"]:
                                item = msg["data"][0]
                                bids = item.get("bids", [])
                                asks = item.get("asks", [])
                                if bids and asks:
                                    self.state.okx_bid_px = float(bids[0][0])
                                    self.state.okx_bid_sz = float(bids[0][1])
                                    self.state.okx_ask_px = float(asks[0][0])
                                    self.state.okx_ask_sz = float(asks[0][1])
                                    self.state.okx_last_ts = time.time()
                                    self.state.okx_updates += 1
                        except asyncio.TimeoutError:
                            await ws.send("ping")
            except Exception as e:
                logger.warning(f"OKX WS 连接断开，3 秒后重连: {e}")
                await asyncio.sleep(3.0)


class AtomicDualLegEngineV4:
    # [2026-11-09] 三态/硬约束状态（类级默认值，实例可覆盖）
    pos_state_known = True
    pos_state_reason = ""
    naked_halt = False
    margin_halt = False
    quota_until = 0.0
    def __init__(
        self,
        hl_account: str,
        hl_agent_key: str,
        okx_api_key: str,
        okx_secret: str,
        okx_passphrase: str,
        cfg: EngineConfigV4 = EngineConfigV4()
    ):
        self.cfg = cfg
        self.hl_account = hl_account
        self.hl_agent_key = hl_agent_key
        
        # 客户端初始化
        self.okx = OKXGatewayClient(okx_api_key, okx_secret, okx_passphrase)
        wallet = eth_account.Account.from_key(hl_agent_key)
        self.hl_exchange = Exchange(wallet, constants.MAINNET_API_URL, account_address=hl_account, perp_dexs=["xyz"])
        self.hl_info = Info(constants.MAINNET_API_URL, skip_ws=False, perp_dexs=["xyz"])
        
        # WebSocket 行情中继器
        self.state = SharedOrderbookState()
        self.ws_mgr = DualExchangeWebSocketManager(self.state, self.hl_info, self.cfg.OKX_INST_ID)
        
        # 实时动态仓位与均价追踪 (内存状态机)
        self.current_q_hl = 0.0
        self.current_q_okx = 0.0
        self._depth_capped_q = None
        self.hl_entry_px = 0.0
        self.okx_entry_px = 0.0
        
        logger.info(f"V4 自适应复利与全风控闭环引擎初始化完成 | HL 地址: {hl_account} | Agent: {wallet.address}")

    def align_account_leverage(self):
        """启动时强制通过 API 同步 OKX 为 10x 交叉杠杆，保持 1:1 保证金占用对称"""
        for attempt in range(1, 4):
            try:
                res = self.okx.set_leverage(self.cfg.OKX_INST_ID, str(self.cfg.TARGET_LEVERAGE), mgn_mode="cross")
                if str(res.get("code", "-1")) == "0":
                    logger.info(f"✅ [OKX 杠杆强制初始化成功] (10x 交叉保证金模式已确认): {res.get('data')}")
                    return
                else:
                    logger.warning(f"⚠️ OKX 杠杆设置第 {attempt} 次失败: {res}，1秒后重试...")
                    time.sleep(1.0)
            except Exception as e:
                logger.error(f"配置 OKX 杠杆异常 (尝试 {attempt}): {e}")
                time.sleep(1.0)

    def get_real_positions(self) -> Tuple[float, float, float]:
        """获取底层真实持仓并计算 Net Delta"""
        q_hl = 0.0
        q_okx = 0.0
        hl_px = 0.0
        okx_px = 0.0
        ok_hl = False
        ok_okx = False
        try:
            # 1. Hyperliquid 真实仓位
            state = self.hl_info.post("/info", {"type": "clearinghouseState", "user": self.hl_account, "dex": "xyz"})
            ok_hl = isinstance(state, dict) and "assetPositions" in state
            for pos in (state.get("assetPositions", []) if ok_hl else []):
                p = pos.get("position", {})
                if p.get("coin") == self.cfg.HL_SYMBOL:
                    q_hl = float(p.get("szi", 0.0))
                    hl_px = float(p.get("entryPx", 0.0))
                    break

            # 2. OKX 真实仓位
            okx_positions = self.okx.get_positions(inst_type="SWAP", inst_id=self.cfg.OKX_INST_ID)
            ok_okx = okx_positions is not None
            for p in (okx_positions or []):
                if p.get("instId") == self.cfg.OKX_INST_ID:
                    pos_str = p.get("pos", "0.0")
                    q_okx = float(pos_str) if pos_str else 0.0
                    avg_px_str = p.get("avgPx", "0.0")
                    okx_px = float(avg_px_str) if avg_px_str else 0.0
        except Exception as e:
            logger.error(f"获取持仓异常: {e}")

        self.current_q_hl = q_hl
        self.current_q_okx = q_okx
        if q_hl > 0.0 and hl_px > 0.0:
            self.hl_entry_px = hl_px
        if abs(q_okx) > 0.0 and okx_px > 0.0:
            self.okx_entry_px = okx_px

        # [2026-11-09] P0 三态：读不到 != 平仓。任何一个场所读取失败即标记 unknown，
        # 上层必须跳过孤儿清理/平仓动作，禁止把"未知"当成"已平"。
        self.pos_state_known = bool(ok_hl and ok_okx)
        self.pos_state_reason = "" if self.pos_state_known else f"hl_ok={ok_hl} okx_ok={ok_okx}"
        # [2026-11-09] 净敞口改美元口径
        _hl_ref = hl_px if hl_px > 0 else self.hl_entry_px
        _okx_ref = okx_px if okx_px > 0 else self.okx_entry_px
        usd_hl = q_hl * _hl_ref
        usd_okx = abs(q_okx) * _okx_ref
        net_delta = usd_hl - usd_okx
        self._last_usd_leg = max(min(abs(usd_hl), abs(usd_okx)), 1e-9)
        return q_hl, q_okx, net_delta

    def get_account_balances(self) -> Tuple[float, float, float, float]:
        """
        获取双边可用资金与总权益
        返回: (w_hl_free, w_okx_free, w_hl_equity, w_okx_equity)
        """
        w_hl_free = 0.0
        w_hl_equity = 0.0
        w_okx_free = 0.0
        w_okx_equity = 0.0

        try:
            # HL 余额
            spot = self.hl_info.spot_user_state(self.hl_account)
            for b in spot.get("balances", []):
                if b.get("coin") == "USDC":
                    w_hl_equity = float(b.get("total", 0.0))
                    break
            # 考虑维持保证金后可用
            avail_pairs = spot.get("tokenToAvailableAfterMaintenance", [])
            if avail_pairs:
                w_hl_free = float(avail_pairs[0][1])
            else:
                w_hl_free = w_hl_equity

            # OKX 余额
            okx_bal = self.okx.get_balance("USDT")
            w_okx_free = float(okx_bal.get("availBal", 0.0))
            w_okx_equity = float(okx_bal.get("eq", 0.0))

        except Exception as e:
            logger.error(f"获取账户资金异常: {e}")

        return w_hl_free, w_okx_free, w_hl_equity, w_okx_equity

    def _check_open_depth(self, q_hl: float, okx_px: float) -> Tuple[bool, str]:
        """
        开仓侧盘口深度预检。返回 (是否可开, 说明)。
        若本笔 HL 买入量超出卖盘前 N 档可吃量的 OPEN_DEPTH_PCT, 则把可开量压缩到深度内;
        同时校验 OKX 卖盘深度是否足以承接对冲量的 OPEN_DEPTH_PCT。
        """
        self._depth_capped_q = None
        tiers = self.cfg.OPEN_DEPTH_TIERS
        pct = self.cfg.OPEN_DEPTH_PCT

        ask_book = self._get_hl_book(side="ask")
        if not ask_book:
            return False, "HL 卖盘不可读, 放弃开仓"
        hl_depth = sum(sz for _, sz in ask_book[:tiers])
        if hl_depth <= 0:
            return False, "HL 卖盘前几档无量, 放弃开仓"

        hl_cap = hl_depth * pct
        if hl_cap < q_hl:
            self._depth_capped_q = round(max(hl_cap, 0.01), 2)

        # OKX 对冲腿深度: 卖盘可吃量需覆盖（q_hl * ratio * OPEN_DEPTH_PCT 折算）
        try:
            books = self.okx.get_books(self.cfg.OKX_INST_ID, sz=max(tiers, 5))
            asks = books.get("asks") or []
            okx_depth = sum(float(a[1]) for a in asks[:tiers]) if asks else 0.0
        except Exception as e:
            logger.warning(f"⚠️ [开仓深度预检] OKX 盘口读取失败: {e}")
            return True, "OKX 深度不可读, 仅校验 HL 侧"

        s_now = self.compute_implied_s(self.state.hl_ask_px or 1.0, okx_px or 1.0)
        ratio = 10.0 / s_now if s_now > 0 else self.cfg.HEDGE_RATIO
        need_okx = (self._depth_capped_q or q_hl) * ratio
        okx_cap = okx_depth * pct
        if okx_cap < need_okx:
            capped_hl = round(okx_cap / ratio, 2) if ratio > 0 else 0.0
            self._depth_capped_q = capped_hl if capped_hl > 0 else self._depth_capped_q
            note = (
                f"OKX 前{tiers}档厚度 {okx_depth:.3f} 张, 可承载 {okx_cap:.3f} 张; "
                f"需 {need_okx:.3f} 张, 压缩至 HL {self._depth_capped_q:.2f} 股"
            )
            if self._depth_capped_q is None or self._depth_capped_q < 0.01:
                return False, note + " (不足最小单)"
            return True, note

        return True, (
            f"HL 前{tiers}档 {hl_depth:.2f} 股 (可承载 {hl_cap:.2f}), "
            f"OKX 前{tiers}档 {okx_depth:.3f} 张 (可承载 {okx_cap:.3f})"
        )

    def plan_dual_leg_position(self, hl_px: float, okx_px: float) -> Dict[str, Any]:
        """
        开仓前的双边配平规划。
        要点:
          1. 隐含股本 S 由两所实时价格推得 (S = OKX价*10 / HL价), 不用常数假设;
          2. 股数配平: 1 股 HL 对应 10/S 张 OKX, 上市后两腿代表同一数量股票;
          3. 单腿各自满足: 该腿名义 <= 本账户权益 * MAX_MARGIN_USAGE_PCT * TARGET_LEVERAGE;
          4. 先判断哪一侧贵, 决定多空方向。
        """
        if hl_px <= 0 or okx_px <= 0:
            return {"ok": False, "reason": "价格无效"}

        s_now = self.compute_implied_s(hl_px, okx_px)
        if s_now <= 0:
            return {"ok": False, "reason": "隐含 S 不可用"}

        # 两个 S 各司其职, 不可混用:
        #   s_ref   = 契约性参照股本, 用来判断「现在贵不贵」, 与开仓阈值比较
        #   s_now   = 市场实时隐含股本, 用来定配平比率, 保证上市后两腿股数中性
        if self.cfg.HEDGE_RATIO_S_OVERRIDE > 0:
            s_ref = self.cfg.HEDGE_RATIO_S_OVERRIDE
            s_hedge = self.cfg.HEDGE_RATIO_S_OVERRIDE
            s_src = "人工覆盖"
        else:
            s_ref = self.cfg.REF_SHARES_B
            s_hedge = s_now
            s_src = "实时 S"

        okx_eq_ref = okx_px * 10.0 / s_ref
        edge_bps = (okx_eq_ref - hl_px) / hl_px * 10000.0
        # [2026-11-09] 冗余门已删除

        long_hl = edge_bps > 0
        ratio = 10.0 / s_hedge
        lo, hi = self.cfg.HEDGE_RATIO_BOUNDS
        if ratio < lo or ratio > hi:
            return {"ok": False, "s": s_now,
                    "reason": f"S={s_now:.2f}B 对应比率 {ratio:.4f} 越界 [{lo},{hi}]"}

        w_hl_free, w_okx_free, w_hl_eq, w_okx_eq = self.get_account_balances()
        if w_hl_eq <= 0 or w_okx_eq <= 0:
            return {"ok": False, "reason": "权益读取失败"}

        # ===== 单腿各自保证金上限 =====
        # 每腿名义 <= 本账户权益 * MAX_MARGIN_USAGE_PCT * TARGET_LEVERAGE
        # 当前 50% * 10x = 单腿 5 倍权益; 单腿价格逆向 20% 即吃掉全部权益, 与 16% 断路器留 4pp 缓冲。
        hl_max_notional = w_hl_eq * self.cfg.MAX_MARGIN_USAGE_PCT * self.cfg.TARGET_LEVERAGE
        okx_max_notional = w_okx_eq * self.cfg.MAX_MARGIN_USAGE_PCT * self.cfg.TARGET_LEVERAGE

        hl_held = abs(self.current_q_hl) * hl_px
        okx_held = abs(self.current_q_okx) * okx_px
        hl_room = hl_max_notional - hl_held
        okx_room = okx_max_notional - okx_held

        # 每 1 股 HL 对应的双边名义 = HL 股价 + OKX 折合每股
        per_share_notional = hl_px + okx_eq_ref
        q_min = self.cfg.MIN_ORDER_USD / hl_px
        q_min = round(q_min + 0.005, 2)                 # 向上取到分, 确保越过 10 USD

        # 单腿剩余空间能承载的股数 (两腿都要满足)
        q_room_hl = hl_room / hl_px if hl_px > 0 else 0.0
        q_room_okx = (okx_room / (ratio * okx_px)) if (ratio > 0 and okx_px > 0) else 0.0
        q_room = min(q_room_hl, q_room_okx)

        if q_room < q_min:
            lagger = "HL" if q_room_hl <= q_room_okx else "OKX"
            return {"ok": False, "s": s_now,
                    "reason": (
                        f"单腿空间不足 (短板 {lagger}): 可承载 {q_room:.2f} 股 < 最小 {q_min:.2f} 股 | "
                        f"HL ${hl_held:.2f}/${hl_max_notional:.2f} (权益 {w_hl_eq:.2f}), "
                        f"OKX ${okx_held:.2f}/${okx_max_notional:.2f} (权益 {w_okx_eq:.2f})"
                    )}

        room = q_room * per_share_notional               # 以最紧的一腿折算的双边可用名义

        # 单笔切片: 剩余空间的 CLIP_FRACTION, 但不得超过剩余空间本身
        clip_room = min(room * self.cfg.CLIP_FRACTION, room, self.cfg.MAX_LOB_DEPTH_CLIP_USD)
        q_clip = round(clip_room / per_share_notional, 2)
        if q_clip < q_min:
            q_clip = round(q_min + 0.005, 2)          # 向上取到分, 越过门槛
        # 抬到最小股数后若超出剩余空间, 说明账户空间不足, 拒绝
        if q_clip * per_share_notional > room:
            return {"ok": False, "s": s_now, "held": held, "max_notional": max_notional,
                    "reason": (
                        f"剩余双边名义 ${room:.2f} 不足以开出最小合规单 "
                        f"(需 ${q_clip * per_share_notional:.2f}, HL {q_clip:.2f} 股)"
                    )}

        # ===== 开仓侧盘口深度预检 (与平仓侧对齐) =====
        # 门禁只判断「价差够不够」, 不判断「深度够不够」。若本笔切片超出前 N 档可吃量,
        # 会击穿盘口产生远超预期的滑点, 且对冲腿可能打不进去导致裸持。
        depth_ok, depth_note = self._check_open_depth(q_clip, okx_px)
        if not depth_ok:
            return {"ok": False, "s": s_now, "held": held, "max_notional": max_notional,
                    "reason": depth_note}
        if self._depth_capped_q is not None and self._depth_capped_q < q_clip:
            logger.info(
                f"📐 [开仓深度压缩] 切片 {q_clip:.2f} -> {self._depth_capped_q:.2f} 股 ({depth_note})"
            )
            q_clip = self._depth_capped_q
            if q_clip < q_min:
                return {"ok": False, "s": s_now, "held": held, "max_notional": max_notional,
                        "reason": f"盘口深度不足: 压缩后 {q_clip:.2f} 股低于最小合规单 {q_min:.2f} 股"}

        self._depth_capped_q = None

        # 注意: 开仓不受 MAX_ROUND_HL_SHARES 约束。
        # 该参数的本意是限制「单笔平仓」的冲击成本, 由 _close_hl_depth_aware 的拆单逻辑承担;
        # 若在此处限制开仓, 小账户会被压到 0.02 股 (1.94 USD), 永远过不了 10 USD 门槛。

        q_okx = round(q_clip * ratio, 3)
        n_hl = q_clip * hl_px
        n_okx = q_okx * okx_px
        _chk = abs(n_hl - n_okx) / max(min(n_hl, n_okx), 1e-9)
        if _chk > self.cfg.MAX_USD_DELTA_PCT:
            return {"ok": False, "s": s_now, "s_ref": s_ref, "edge_bps": edge_bps,
                    "reason": (f"美元配平断言失败: HL ${n_hl:.2f} vs OKX ${n_okx:.2f} "
                               f"(净 {n_hl - n_okx:+.2f}, {_chk*100:.2f}%)")}
        return {
            "ok": True, "s": s_now, "s_ref": s_ref, "s_hedge": s_hedge, "s_src": s_src,
            "edge_bps": edge_bps, "long_hl": long_hl, "ratio": ratio,
            "q_hl": q_clip, "q_okx": q_okx,
            "notional_hl": n_hl, "notional_okx": n_okx,
            "hl_held": hl_held, "okx_held": okx_held,
            "hl_max": hl_max_notional, "okx_max": okx_max_notional,
            "w_hl": w_hl_eq, "w_okx": w_okx_eq,
            "hl_usage_pct": (hl_held + n_hl) / (w_hl_eq * self.cfg.TARGET_LEVERAGE) * 100.0,
            "okx_usage_pct": (okx_held + n_okx) / (w_okx_eq * self.cfg.TARGET_LEVERAGE) * 100.0,
        }

    def execute_atomic_trade(self, hl_ask: float, okx_bid: float, target_hl_sz: float = 0.1) -> bool:
        """
        严格单向串联原子开仓:
        HL 买入 IOC -> 确认成交 -> 60ms 避开 STP -> OKX 市价对冲卖单
        """
        # ===== 双边配平规划: 实时 S + 方向 + 双边名义约束 =====
        plan = self.plan_dual_leg_position(hl_ask, okx_bid)
        if not plan.get("ok"):
            logger.warning(f"⚖️ [配平拒绝开仓] {plan.get('reason')}")
            return False
        if not plan["long_hl"]:
            logger.warning(
                f"⚖️ [方向不符] S={plan['s']:.2f}B 价差 {plan['edge_bps']:+.1f} bps, "
                f"HL 折合每股高于 OKX; 当前引擎仅支持多 HL 空 OKX, 放弃本笔"
            )
            return False
        target_hl_sz = plan["q_hl"]
        target_okx_sz = plan["q_okx"]
        slider = plan["edge_bps"] / 10000.0
        logger.info(
            f"⚖️ [配平] S={plan['s']:.2f}B 折价 {plan['edge_bps']:+.1f}bps ratio {plan['ratio']:.4f} | "
            f"开 HL {target_hl_sz:.2f}股(${plan['notional_hl']:.1f}) / OKX {target_okx_sz:.3f}张(${plan['notional_okx']:.1f}) | "
            f"HL占用 ${plan['hl_held'] + plan['notional_hl']:.1f}/${plan['hl_max']:.1f} ({plan['hl_usage_pct']:.0f}%) | "
            f"OKX占用 ${plan['okx_held'] + plan['notional_okx']:.1f}/${plan['okx_max']:.1f} ({plan['okx_usage_pct']:.0f}%)"
        )

        # 1. 预检 OKX 可用资金
        notional_okx = target_okx_sz * okx_bid
        req_margin_okx = (notional_okx / self.cfg.TARGET_LEVERAGE) * 1.25
        w_hl_free, w_okx_free, _, _ = self.get_account_balances()
        if w_okx_free < req_margin_okx:
            logger.warning(f"⚠️ OKX 保证金不足放弃触发！可用: ${w_okx_free:.2f} < 需求: ${req_margin_okx:.2f}")
            return False

        # 2. 执行 Leg 1: Hyperliquid IOC 买单
        limit_buy_px = round(hl_ask * (1.0 + self.cfg.MAX_SLIPPAGE_BPS / 10000.0), 2)
        logger.info(f"⚡ [Leg 1] 发送 HL 买入: {target_hl_sz:.2f} 股 @ 限价 ${limit_buy_px:.2f} (IOC)...")

        t0 = time.perf_counter()
        hl_res = self.hl_exchange.order(
            self.cfg.HL_SYMBOL,
            True,
            target_hl_sz,
            limit_buy_px,
            {"limit": {"tif": "Ioc"}}
        )
        elapsed_hl_ms = (time.perf_counter() - t0) * 1000.0

        hl_filled_sz, hl_avg_px = self._parse_hl_fill(hl_res)
        if hl_filled_sz <= 0.0:
            logger.warning(f"❌ [Leg 1 失败/未撮合] HL 返回: {hl_res} (耗时 {elapsed_hl_ms:.1f}ms)，自动阻断，坚决不发 Leg 2！")
            return False

        logger.info(f"✅ [Leg 1 成交] HL 成功买入 {hl_filled_sz} 股 @ ${hl_avg_px} (耗时 {elapsed_hl_ms:.1f}ms)")

        # 3. 60ms 避开 STP 规则，随后发送 Leg 2
        time.sleep(0.06)
        actual_okx_sz = round(hl_filled_sz * self.cfg.HEDGE_RATIO, 3)
        logger.info(f"⚡ [Leg 2] 发送 OKX 对冲卖单: {actual_okx_sz} 张...")

        t1 = time.perf_counter()
        okx_res = self.okx.place_order(
            inst_id=self.cfg.OKX_INST_ID,
            td_mode="cross",
            side="sell",
            ord_type="market",
            sz=str(actual_okx_sz)
        )
        elapsed_okx_ms = (time.perf_counter() - t1) * 1000.0

        if str(okx_res.get("code", "-1")) == "0":
            logger.info(f"🎉 [Leg 2 成交] OKX 对冲成功！双腿闭环完成 (耗时 {elapsed_okx_ms:.1f}ms)")
            # 更新内部状态
            self.get_real_positions()
            return True
        else:
            logger.critical(f"🚨 [Leg 2 失败] OKX 报错: {okx_res}！触发原子断路器，启动 50ms 紧急平仓 HL！")
            self._emergency_flatten_hl(hl_filled_sz, hl_ask)
            return False

    # ==================== 平仓滑点控制 ====================
    def _get_hl_book(self, side: str = "bid"):
        """
        取 HL xyz:ANT 盘口深度 [(px, sz), ...]，失败返回 []。
        side="bid" 取买盘 (平多时用), side="ask" 取卖盘 (平空时用)。
        """
        try:
            book = self.hl_info.l2_snapshot(self.cfg.HL_SYMBOL)
            lv = book.get("levels") or []
            if len(lv) < 2:
                return []
            idx = 0 if side == "bid" else 1
            return [(float(x["px"]), float(x["sz"])) for x in lv[idx]]
        except Exception as e:
            logger.warning(f"⚠️ [盘口预检] HL L2 获取失败: {e}")
            return []

    def _get_okx_ask_px(self):
        """取 OKX 卖一价，失败返回 None"""
        try:
            books = self.okx.get_books(self.cfg.OKX_INST_ID, sz=5)
            asks = books.get("asks") or []
            if asks:
                return float(asks[0][0])
        except Exception as e:
            logger.warning(f"⚠️ [盘口预检] OKX 卖一获取失败: {e}")
        return None

    def _close_hl_market_force(self, q_hl: float) -> Tuple[bool, float, float]:
        hl_mid = (self.state.hl_ask_px + self.state.hl_bid_px) / 2.0 if self.state.hl_ask_px > 0 else 0
        if hl_mid <= 0: return False, 0.0, 0.0
        notional = q_hl * hl_mid
        if notional > self.cfg.MIN_ORDER_USD * 1.5:
            return False, 0.0, 0.0
        logger.warning(f"⚡ [residue detect] HL ${notional:.2f} < ${self.cfg.MIN_ORDER_USD*1.5:.2f} -> market flush")
        try:
            res = self.hl_exchange.market_close(self.cfg.HL_SYMBOL, sz=round(q_hl,2), slippage=0.01)
            filled, px = self._parse_hl_fill(res)
            logger.warning(f"⚡ [residue clear] HL market close {filled:.2f}/{q_hl:.2f} @ ${px:.4f}")
            return True, filled, px
        except Exception as e:
            logger.error(f"residue clear fail: {e}")
            return False, 0.0, 0.0

    def _close_hl_depth_aware(self, q_hl: float, tag: str = "平仓", allow_market: bool = True) -> Tuple[bool, float, float]:
        """
        盘口预检 + 动态拆单平掉 HL 仓位 (多头或空头)。
        q_hl 带符号: 正数 = 多头待平, 负数 = 空头待平。
        返回 (全部成交, 累计成交股数, 加权均价)
        """
        sz_total = abs(q_hl)
        if sz_total <= 0.005:
            return True, 0.0, 0.0

        is_long = q_hl > 0
        side = "bid" if is_long else "ask"      # 平多打买盘, 平空打卖盘
        side_label = "买一" if is_long else "卖一"

        book = self._get_hl_book(side=side)
        if book:
            best_px, best_sz = book[0]
            max_chunk = max(0.01, round(best_sz * self.cfg.CLOSE_DEPTH_PCT, 2))
            depth_ok = remaining_depth = 0.0
            for px, sz in book:
                remaining_depth += sz
                if remaining_depth >= sz_total:
                    depth_ok = px
                    break
            logger.info(
                f"📖 [{tag}] HL 盘口预检({side_label}): ${best_px:.3f}×{best_sz:.2f}股 | "
                f"待平 {sz_total:.2f}股 ({'多' if is_long else '空'}) | 单笔上限 {max_chunk:.2f}股 | "
                + (f"吃满所需价位 ${depth_ok:.3f}" if depth_ok else "深度不足，将逐档吃")
            )
        else:
            max_chunk = sz_total
            logger.warning(f"⚠️ [{tag}] 无盘口数据，退化为单笔平仓 (滑点仍限 {self.cfg.CLOSE_SLIPPAGE_BPS:.0f}bps)")

        remaining = round(sz_total, 2)
        filled_total = 0.0
        cost = 0.0
        chunks = 0
        while remaining > 0.005 and chunks < 20:
            chunk = round(min(remaining, max_chunk), 2)
            if chunk < 0.01:
                break
            filled = px = 0.0
            _t0 = time.time()
            for attempt, (bps, _deadline) in enumerate(self.cfg.CLOSE_SLIPPAGE_LADDER):
                if time.time() - _t0 > _deadline:
                    continue
                if bps > self.cfg.CLOSE_LIMIT_ONLY_MAX_BPS and not allow_market:
                    logger.warning(f"🛑 [{tag}] 非强制路径不使用 {bps:.0f}bps 档，停止尝试，保留剩余 {remaining:.2f} 股")
                    break
                try:
                    _touch = self.state.hl_bid_px if self.state.hl_bid_px > 0 else hl_mid
                    _aggressive = round(_touch * (1.0 - bps / 10000.0), 2)
                    res = self.hl_exchange.order(self.cfg.HL_SYMBOL, False, chunk, _aggressive, {"limit": {"tif": "Ioc"}})
                    filled, px = self._parse_hl_fill(res)
                except Exception as e:
                    logger.error(f"❌ [{tag}] HL 拆单异常(尝试{attempt+1}): {e}")
                    filled = 0.0
                if filled > 0:
                    if attempt > 0:
                        logger.warning(f"⚠️ [{tag}] 放宽至 {bps:.0f}bps 后成交 {filled:.2f}股 @ ${px:.4f}")
                    break
                time.sleep(0.4)
            if filled <= 0:
                logger.error(f"❌ [{tag}] HL 拆单最终未成交，剩余 {remaining:.2f} 股未平！")
                break
            filled_total += filled
            cost += filled * px
            remaining = round(remaining - filled, 2)
            chunks += 1
            if remaining > 0.005:
                time.sleep(self.cfg.CLOSE_CHUNK_INTERVAL_SEC)
        avg = cost / filled_total if filled_total > 0 else 0.0
        ok = remaining <= 0.01
        logger.info(f"{'✅' if ok else '⚠️'} [{tag}] HL 拆单平仓: {filled_total:.2f}/{sz_total:.2f} 股, "
                    f"均价 ${avg:.4f}, 共 {chunks} 笔, {'已清空' if ok else f'残留 {remaining:.2f} 股'}")
        return ok, filled_total, avg

    def _close_okx_limit_ioc(self, q_okx: float, tag: str = "平仓", allow_market: bool = True) -> bool:
        """
        限价 IOC 平 OKX 仓位。
        当前实现仅支持平空 (side=buy, 价 = 卖一 × (1+bps))；若传入正数 (多头待平) 会显式拒绝,
        避免像 HL 侧那样静默返回成功却不发单。
        """
        sz = abs(q_okx)
        if sz <= 0.001:
            return True
        if q_okx > 0:
            logger.error(f"❌ [{tag}] OKX 平仓仅支持平空, 收到多头 {q_okx:+.3f} 张, 拒绝执行以免静默漏平")
            return False
        ask = self._get_okx_ask_px()
        if ask is None:
            logger.warning(f"⚠️ [{tag}] 无 OKX 盘口，退化为市价单")
            try:
                res = self.okx.place_order(inst_id=self.cfg.OKX_INST_ID, td_mode="cross",
                                           side="buy", ord_type="market", sz=str(round(sz, 3)))
                ok = str(res.get("code", "-1")) == "0"
                logger.info(f"{'✅' if ok else '❌'} [{tag}] OKX 市价平仓(兜底): {res.get('code')}")
                return ok
            except Exception as e:
                logger.error(f"❌ [{tag}] OKX 市价兜底异常: {e}")
                return False

        _t0 = time.time()
        for bps, _deadline in self.cfg.CLOSE_SLIPPAGE_LADDER:
            if time.time() - _t0 > _deadline:
                continue
            if bps > self.cfg.CLOSE_LIMIT_ONLY_MAX_BPS and not allow_market:
                logger.warning(f"🛑 [{tag}] 非强制路径不使用 {bps:.0f}bps 档，停止尝试，保留 {sz:.3f} 张")
                return False
            px = round(ask * (1.0 + bps / 10000.0), 2)
            try:
                res = self.okx.place_order(
                    inst_id=self.cfg.OKX_INST_ID, td_mode="cross", side="buy",
                    ord_type="ioc", px=str(px), sz=str(round(sz, 3)))
                if str(res.get("code", "-1")) == "0":
                    logger.info(f"✅ [{tag}] OKX 限价IOC平仓 {sz:.3f} 张 @限价 ${px} (卖一 ${ask}, +{bps:.0f}bps)")
                    return True
                logger.warning(f"⚠️ [{tag}] OKX IOC 未成交(限价 ${px}): {res.get('code')} {res.get('msg','')}")
            except Exception as e:
                logger.error(f"❌ [{tag}] OKX IOC 异常: {e}")
            time.sleep(0.4)

        logger.error(f"❌ [{tag}] OKX 限价 IOC 全部失败，兜底市价平仓")
        try:
            res = self.okx.place_order(inst_id=self.cfg.OKX_INST_ID, td_mode="cross",
                                       side="buy", ord_type="market", sz=str(round(sz, 3)))
            ok = str(res.get("code", "-1")) == "0"
            logger.info(f"{'✅' if ok else '❌'} [{tag}] OKX 市价兜底结果: {res.get('code')}")
            return ok
        except Exception as e:
            logger.error(f"❌ [{tag}] OKX 市价兜底异常: {e}")
            return False

    def _topup_deficient_leg(self, hl_px: float, okx_px: float) -> bool:
        """[2026-11-09] 补偿腿：只补「缺的那条腿」，把两腿拉回美元中性。

        与「开仓」的本质区别：本方法只允许 **减小** |net_delta|，绝不新增净敞口。
        触发前置条件（调用方保证）：价差 >= ENTRY_SPREAD_BPS、非 quota 熔断。
        返回 True 表示本轮发出了一笔补腿单。
        """
        now = time.time()
        if now - getattr(self, "_topup_last_ts", 0.0) < self.cfg.TOPOUT_COOLDOWN_SEC:
            return False
        q_hl, q_okx, net = self.get_real_positions()
        if not getattr(self, "pos_state_known", True):
            logger.warning("⚠️ [补腿] 持仓状态未知，跳过")
            return False
        if abs(net) <= self.cfg.TOPOUT_TRIGGER_USD:
            return False

        # ===== [2026-11-09] 价差口径（用户定义）=====
        # 不是「两腿此刻市价之差」，而是「缺腿此刻市价 vs 另一腿的持仓均价（折算同量纲）」。
        # 买 HL：对比 OKX 持仓均价折算成每股；卖 OKX：对比 HL 持仓均价折算成每张。
        if net < 0:
            _other_basis = self.okx_entry_px * 10.0 / self.cfg.ASSUMED_SHARES_B   # OKX 均价 → 每股等价
            _edge_basis = ((_other_basis - hl_px) / hl_px * 10000.0) if hl_px > 0 else 0.0
            _act = f"买 HL(现 {hl_px:.2f}) vs OKX均价折每股 {_other_basis:.2f}"
        else:
            _other_basis = self.hl_entry_px * self.cfg.ASSUMED_SHARES_B / 10.0    # HL 均价 → 每张等价
            _edge_basis = ((okx_px - _other_basis) / _other_basis * 10000.0) if _other_basis > 0 else 0.0
            _act = f"卖 OKX(现 {okx_px:.2f}) vs HL均价折每张 {_other_basis:.2f}"
        if _edge_basis < self.cfg.ENTRY_SPREAD_BPS:
            logger.info(f"⚖️ [补腿-未达价差] {_act} → {_edge_basis:+.0f}bps < 门槛 {self.cfg.ENTRY_SPREAD_BPS:.0f}bps，不补")
            return False
        logger.info(f"⚖️ [补腿-价差达标] {_act} → {_edge_basis:+.0f}bps ≥ {self.cfg.ENTRY_SPREAD_BPS:.0f}bps，执行补腿")
        _wfh, _wfo, eq_hl, eq_okx = self.get_account_balances()
        if eq_hl <= 0 or eq_okx <= 0:
            return False
        _slip = self.cfg.TOPOUT_SLIPPAGE_BPS / 10000.0
        try:
            if net < 0:
                # 净空 → 缺多头 → 买 HL
                held = abs(q_hl) * hl_px
                cap = eq_hl * self.cfg.MAX_LEG_MARGIN_PCT * self.cfg.TARGET_LEVERAGE
                room = cap - held
                sz = round(min(-net, room) / hl_px, 2)
                if sz * hl_px < self.cfg.MIN_ORDER_USD:
                    logger.info(f"⚖️ [补腿] HL 缺口 ${-net:.2f} 但受保证金上限约束（余 ${room:.2f}）不足最小单，跳过")
                    return False
                px = round(hl_px * (1.0 + _slip), 2)
                res = self.hl_exchange.order(self.cfg.HL_SYMBOL, True, sz, px, {"limit": {"tif": "Ioc"}})
                filled, avg = self._parse_hl_fill(res)
                logger.warning(
                    f"⚖️ [补腿-买 HL] 裸敞口 ${net:+.2f} 价差 {_edge_basis:+.0f}bps → 买 {sz:.2f} 股 @ 限价 ${px} "
                    f"| 成交 {filled:.2f} @ ${avg:.2f} | 保证金 {held+sz*hl_px:.2f}/{cap:.2f}")
                self._topup_last_ts = now
                return filled > 0
            else:
                # 净多 → 缺空头 → 卖 OKX
                held = abs(q_okx) * okx_px
                cap = eq_okx * self.cfg.MAX_LEG_MARGIN_PCT * self.cfg.TARGET_LEVERAGE
                room = cap - held
                sz = round(min(net, room) / okx_px, 3)
                if sz < 0.001:
                    logger.info(f"⚖️ [补腿] OKX 缺口 ${net:.2f} 受保证金上限约束（余 ${room:.2f}）不足最小张，跳过")
                    return False
                px = round(okx_px * (1.0 - _slip), 2)
                res = self.okx.place_order(inst_id=self.cfg.OKX_INST_ID, td_mode="cross",
                                           side="sell", ord_type="ioc", px=str(px), sz=str(sz))
                ok = str(res.get("code", "-1")) == "0"
                logger.warning(
                    f"⚖️ [补腿-卖 OKX] 裸敞口 ${net:+.2f} 价差 {_edge_basis:+.0f}bps → 卖 {sz:.3f} 张 @ 限价 ${px} "
                    f"| {'成功' if ok else '失败 '+str(res)[:100]} | 保证金 {held+sz*okx_px:.2f}/{cap:.2f}")
                if ok:
                    self._topup_last_ts = now
                return ok
        except Exception as e:
            logger.error(f"❌ [补腿] 异常: {e}")
            return False

    def execute_atomic_close(self, reason: str = "波段止盈平仓", forced: bool = True) -> bool:
        """
        双腿同步平仓通道 (方案 B: 等量配对推进)。

        每轮取「两腿可平量的较小者」作为本批敞口, 两腿各平这么多, 循环直到清空。
        这样任意时刻两腿的名义敞口差不超过一个批次, 不会出现一边已归零、另一边裸持,
        也就不再依赖孤儿仓清理器的 5 秒巡检兜底。

        forced=True  强制路径 (单腿断路 / 孤儿仓 / S 硬熔断): 允许第四档 200bps 与市价兜底。
        forced=False 非强制路径 (8% ROI 止盈 / 波段止盈): 只用限价 IOC 阶梯, 绝不市价。
        """
        q_hl, q_okx, _ = self.get_real_positions()
        if abs(q_hl) <= 0.01 and abs(q_okx) <= 0.005:
            logger.info("当前无有效持仓，无需平仓。")
            return True

        S_now = self.compute_implied_s(self.state.hl_bid_px or 1.0, self.state.okx_bid_px or 1.0)
        ratio = 10.0 / S_now if S_now > 0 else self.cfg.HEDGE_RATIO

        logger.warning(
            f"🚨 [执行双腿同步平仓] 原因: {reason} | 目标: HL {q_hl:+.2f} 股, OKX {q_okx:+.3f} 张 | "
            f"S={S_now:.2f}B ratio={ratio:.4f} 批次上限 {self.cfg.CLOSE_BATCH_PCT:.0%}"
        )

        hl_ok = okx_ok = False
        for rnd in range(1, self.cfg.CLOSE_MAX_ROUNDS + 1):
            q_hl, q_okx, _ = self.get_real_positions()
            hl_left = abs(q_hl)
            okx_left = abs(q_okx)
            if hl_left <= 0.01 and okx_left <= 0.005:
                hl_ok = okx_ok = True
                break

            # 本批敞口 = 两腿较小者可平量, 再乘批次比例
            # 把 OKX 张数折算成等效股数, 与 HL 同量纲比较
            hl_equiv = hl_left
            okx_equiv = okx_left / ratio if ratio > 0 else okx_left
            smaller = min(hl_equiv, okx_equiv)
            batch_hl = max(smaller * self.cfg.CLOSE_BATCH_PCT, self.cfg.MIN_ORDER_USD / hl_mid if hl_mid > 0 else 0.01)
            batch_hl = round(min(batch_hl, smaller), 2)

            # 尾差处理: 若按比例切出的量低于最小下单单位, 直接把两腿剩余全部平掉
            batch_okx = round(batch_hl * ratio, 3)
            batch_hl = round(batch_hl, 2)
            if batch_hl < 0.01 or batch_okx < 0.001:
                batch_hl = round(min(hl_equiv, self.cfg.CLOSE_TAIL_MIN_HL), 2)
                batch_okx = round(batch_hl * ratio, 3)
                logger.info(
                    f"🔚 [同步平仓 收尾批] 尾差小于最小单位, 按剩余量全平: "
                    f"HL {batch_hl:.2f} 股 + OKX {batch_okx:.3f} 张"
                )
                if batch_hl < 0.01 or batch_okx < 0.001:
                    logger.warning(f"🔚 [同步平仓] 剩余 HL {hl_left:.3f} / OKX {okx_left:.3f} 低于可下单量, 停止")
                    break

            logger.info(
                f"🔄 [同步平仓 第{rnd}批] HL {batch_hl:.2f} 股 + OKX {batch_okx:.3f} 张 "
                f"(剩余 HL {hl_left:.2f} / OKX {okx_left:.3f})"
            )

            # ===== [2026-11-09] P1 影子跟单：先平薄腿 HL，拿到「实际成交量」，厚腿只跟随这个量 =====
            hl_filled = 0.0
            try:
                _, hl_filled, avg_px = self._close_hl_depth_aware(
                    batch_hl, tag=f"平仓-HL-{rnd}", allow_market=forced)
            except Exception as e:
                logger.error(f"HL 平仓异常: {e}")
                hl_filled = 0.0

            if hl_filled <= 0.005:
                logger.warning(f"⚠️ [同步平仓] 第{rnd}批 HL 实际成交 {hl_filled:.2f} 股 → 影子量=0，不平 OKX")
                break

            shadow_okx = round(hl_filled * ratio, 3)
            logger.info(f"👥 [影子跟单] HL 实成 {hl_filled:.2f} 股 → OKX 只跟随 {shadow_okx:.3f} 张")
            okx_batch_ok = False
            try:
                okx_batch_ok = self._close_okx_limit_ioc(
                    -shadow_okx, tag=f"平仓-OKX-{rnd}", allow_market=True)
            except Exception as e:
                logger.error(f"OKX 平仓异常: {e}")

            if not okx_batch_ok:
                # 影子腿没跟上 → 把已平的 HL 原量买回，恢复配对（按实际成交，不按计划）
                logger.critical(f"🚨 [影子跟单失败] OKX 未跟随 {shadow_okx:.3f} 张，买回 HL {hl_filled:.2f} 股恢复配对")
                try:
                    back_px = round(self.state.hl_ask_px * 1.002, 2) if self.state.hl_ask_px > 0 else None
                    if back_px:
                        self.hl_exchange.order(self.cfg.HL_SYMBOL, True, round(hl_filled, 2), back_px,
                                               {"limit": {"tif": "Ioc"}})
                        logger.warning(f"↩️ [回补] HL 买回 {hl_filled:.2f} 股 @ ${back_px:.2f}")
                except Exception as e:
                    logger.critical(f"FATAL: 回补失败, 存在单边敞口: {e}")
                break

            time.sleep(self.cfg.CLOSE_CHUNK_INTERVAL_SEC)

        q_hl, q_okx, _ = self.get_real_positions()
        hl_ok = abs(q_hl) <= 0.01
        okx_ok = abs(q_okx) <= 0.005

        if hl_ok and okx_ok:
            logger.info(f"🎉 [双腿平仓完成] 原因: {reason}，双边仓位已清空恢复中性。")
            self.hl_entry_px = 0.0
            self.okx_entry_px = 0.0
            self.current_q_hl = 0.0
            self.current_q_okx = 0.0
            return True

        logger.error(
            f"⚠️ [双腿平仓未完成] HL {q_hl:+.2f} 股 / OKX {q_okx:+.3f} 张 残留; "
            f"孤儿仓清理器将在 5 秒内接管"
        )
        return False

    def resolve_hedge_ratio(self, hl_px: float, okx_px: float) -> Tuple[float, str]:
        """
        开仓瞬间的配平比率。优先人工覆盖，其次按隐含 S 实时计算，最后兜底 10/12.7。
        返回 (ratio, 说明)。ratio 返回 0.0 表示越界，该笔开仓应放弃。
        """
        if not self.cfg.DYNAMIC_HEDGE_RATIO:
            return self.cfg.HEDGE_RATIO, f"固定比率 {self.cfg.HEDGE_RATIO:.4f}"

        if self.cfg.HEDGE_RATIO_S_OVERRIDE > 0:
            s_use = self.cfg.HEDGE_RATIO_S_OVERRIDE
            ratio = 10.0 / s_use
            return ratio, f"人工锁定 S={s_use:.2f}B -> ratio {ratio:.4f}"

        s_now = self.compute_implied_s(hl_px, okx_px)
        if s_now <= 0:
            return self.cfg.HEDGE_RATIO, "隐含 S 不可用，回落兜底比率"

        ratio = 10.0 / s_now
        lo, hi = self.cfg.HEDGE_RATIO_BOUNDS
        if ratio < lo or ratio > hi:
            return 0.0, (
                f"隐含 S={s_now:.2f}B 推出比率 {ratio:.4f} 越出安全区间 [{lo}, {hi}]，放弃本笔开仓"
            )
        return ratio, f"动态配平 S={s_now:.2f}B -> ratio {ratio:.4f}"

    def compute_edge_bps(self, hl_px: float, okx_px: float) -> float:
        """
        参照股本口径的跨所折价 (bps)。正数 = OKX 贵, 负数 = HL 贵。
        与门禁利差 (ASSUMED_SHARES_B 口径) 是两个不同量:
          门禁利差 = (OKX价*10/12.7 - HL价)/HL价, 回答「值不值得开」;
          参照折价 = (OKX价*10/13.70 - HL价)/HL价, 回答「往哪个方向开」。
        """
        if hl_px <= 0 or okx_px <= 0:
            return 0.0
        s_ref = self.cfg.HEDGE_RATIO_S_OVERRIDE if self.cfg.HEDGE_RATIO_S_OVERRIDE > 0 else self.cfg.REF_SHARES_B
        okx_eq = okx_px * 10.0 / s_ref
        return (okx_eq - hl_px) / hl_px * 10000.0

    def compute_implied_s(self, hl_px: float, okx_px: float) -> float:
        """市场隐含流通股本 S (十亿股)。S = OKX 价 * 10B / HL 价"""
        if hl_px <= 0 or okx_px <= 0:
            return 0.0
        return okx_px * 10.0 / hl_px

    def compute_breakeven_s(self) -> float:
        """当前持仓的盈亏平衡股本。S_be = OKX 开仓价 * 10B / HL 开仓价"""
        if self.hl_entry_px <= 0 or self.okx_entry_px <= 0:
            return 0.0
        return self.okx_entry_px * 10.0 / self.hl_entry_px

    def execute_atomic_deleverage(self, fraction: float, reason: str) -> bool:
        """按比例削减双腿仓位，保持对冲比率。fraction=0.5 表示各减一半"""
        q_hl, q_okx, _ = self.get_real_positions()
        hl_cut = math.floor(q_hl * fraction * 100) / 100.0
        okx_cut = math.floor(abs(q_okx) * fraction * 1000) / 1000.0
        if hl_cut <= 0 or okx_cut <= 0:
            logger.info(f"减仓量不足最小下单单位 (HL {hl_cut}, OKX {okx_cut})，跳过。")
            return False

        logger.warning(f"✂️ [执行减仓 {fraction:.0%}] 原因: {reason} | 削减 HL {hl_cut:.2f} 股, OKX {okx_cut:.3f} 张")

        hl_ok = False
        try:
            hl_ok, filled, avg_px = self._close_hl_depth_aware(hl_cut, tag="减仓-HL")
        except Exception as e:
            logger.error(f"HL 减仓异常: {e}")

        okx_ok = False
        try:
            okx_ok = self._close_okx_limit_ioc(okx_cut, tag="减仓-OKX")
        except Exception as e:
            logger.error(f"OKX 减仓异常: {e}")

        self.get_real_positions()
        logger.info(f"✂️ [减仓结果] HL {'成功' if hl_ok else '失败'}, OKX {'成功' if okx_ok else '失败'}")
        return hl_ok and okx_ok

    def reconcile_orphan_position(self, q_hl: float, q_okx: float) -> bool:
        """
        孤儿仓位监控与自动清理器:
        若某一腿被强平、止损或外部人工平仓导致归零，而另一腿仍有裸持仓位，立即市价平掉残存腿！
        """
        # 情况 1: OKX 已归零，但 HL 仍有裸持 (多头或空头)
        if abs(q_hl) > 0.05 and abs(q_okx) < 0.01:
            pos_label = "多头" if q_hl > 0 else "空头"
            logger.critical(f"🚨 [检测到单腿裸奔孤儿仓] OKX 已归零，HL 仍持有 {q_hl:+.2f} 股{pos_label}！立即强制平仓 HL！")
            try:
                ok, filled, avg_px = self._close_hl_depth_aware(q_hl, tag="孤儿仓-HL")
                logger.warning(f"HL 孤儿平仓结果: {'成功' if ok else '未完全平掉'} {filled:.2f}/{abs(q_hl):.2f} 股 @ ${avg_px:.4f}")
                if ok:
                    self.hl_entry_px = 0.0
                    self.current_q_hl = 0.0
                return ok
            except Exception as e:
                logger.error(f"HL 孤儿平仓异常: {e}")
                return False

        # 情况 2: HL 已归零，但 OKX 仍有裸持 (空头或多头)
        if abs(q_hl) < 0.01 and abs(q_okx) > 0.02:
            okx_label = "空头" if q_okx < 0 else "多头"
            logger.critical(f"🚨 [检测到单腿裸奔孤儿仓] HL 已归零，OKX 仍持有 {q_okx:+.3f} 张{okx_label}！立即强制平仓 OKX！")
            try:
                ok = self._close_okx_limit_ioc(q_okx, tag="孤儿仓-OKX")
                logger.warning(f"OKX 孤儿平仓结果: {'成功' if ok else '失败'}")
                if ok:
                    self.okx_entry_px = 0.0
                    self.current_q_okx = 0.0
                return ok
            except Exception as e:
                logger.error(f"OKX 孤儿平仓异常: {e}")
                return False

        return False

    def check_single_leg_price_drift(self, hl_mid: float, okx_mid: float) -> bool:
        """
        任一腿价格波动 >= 8% (对应 10x 杠杆下权益 80% 剧烈波动)，立即双平防爆仓
        """
        if self.current_q_hl > 0.05 and self.hl_entry_px > 0.0:
            drift_hl = abs(hl_mid - self.hl_entry_px) / self.hl_entry_px
            if drift_hl >= self.cfg.SINGLE_LEG_MAX_PRICE_DRIFT:
                logger.critical(f"🛑 [触发单腿 {self.cfg.SINGLE_LEG_MAX_PRICE_DRIFT:.0%} 价格偏离硬断路] HL 价格漂移 {drift_hl:.2%} (入场 ${self.hl_entry_px:.2f}, 现价 ${hl_mid:.2f})")
                self.execute_atomic_close(reason=f"HL 价格偏离 {drift_hl:.2%}", forced=True)
                return True

        if abs(self.current_q_okx) > 0.05 and self.okx_entry_px > 0.0:
            drift_okx = abs(okx_mid - self.okx_entry_px) / self.okx_entry_px
            if drift_okx >= self.cfg.SINGLE_LEG_MAX_PRICE_DRIFT:
                logger.critical(f"🛑 [触发单腿 {self.cfg.SINGLE_LEG_MAX_PRICE_DRIFT:.0%} 价格偏离硬断路] OKX 价格漂移 {drift_okx:.2%} (入场 ${self.okx_entry_px:.2f}, 现价 ${okx_mid:.2f})")
                self.execute_atomic_close(reason=f"OKX 价格偏离 {drift_okx:.2%}", forced=True)
                return True

        return False

    # [2026-11-11] 半价差成本: 两腿各自 (ask-bid)/2 / mid 的 bps
    def _compute_live_crossing(self, hl_mid: float, okx_mid: float) -> float:
        hls, okxs = 0.0, 0.0
        if self.state.hl_ask_px > 0 and self.state.hl_bid_px > 0 and hl_mid > 0:
            hls = (self.state.hl_ask_px - self.state.hl_bid_px) / hl_mid * 10000.0
        _okx_eq = okx_mid * 10.0 / self.cfg.ASSUMED_SHARES_B if okx_mid > 0 else 0.0
        if self.state.okx_ask_px > 0 and self.state.okx_bid_px > 0 and _okx_eq > 0:
            okxs = (self.state.okx_ask_px - self.state.okx_bid_px) / _okx_eq * 10000.0
        return hls / 2.0 + okxs / 2.0

    def check_combined_pnl_take_profit(self, hl_mid: float, okx_mid: float) -> bool:
        """
        双腿合计净利润率止盈守卫:
        当综合净收益率 >= 3.0% (on margin)，立即并发双平，锁定利润实现频繁交易飞轮
        """
        if self.current_q_hl <= 0.05 or abs(self.current_q_okx) <= 0.005:
            return False
        if self.hl_entry_px <= 0.0 or self.okx_entry_px <= 0.0:
            return False

        # 1. 计算 HL 浮动盈亏 (多头)
        pnl_hl = self.current_q_hl * (hl_mid - self.hl_entry_px)
        # 10x 杠杆占用保证金
        margin_hl = (self.current_q_hl * self.hl_entry_px) / self.cfg.TARGET_LEVERAGE

        # 2. 计算 OKX 浮动盈亏 (空头)
        pnl_okx = abs(self.current_q_okx) * (self.okx_entry_px - okx_mid)
        margin_okx = (abs(self.current_q_okx) * self.okx_entry_px) / self.cfg.TARGET_LEVERAGE

        total_pnl = pnl_hl + pnl_okx
        total_margin = margin_hl + margin_okx
        total_notional = (self.current_q_hl * hl_mid) + (abs(self.current_q_okx) * okx_mid)
        _hn = abs(self.current_q_hl) * hl_mid; _on = abs(self.current_q_okx) * okx_mid
        if min(_hn, _on) < self.cfg.MIN_ORDER_USD * 2.0 and max(_hn, _on) > self.cfg.MIN_ORDER_USD:
            logger.warning(f"⚡ [residue] HL ${_hn:.2f} / OKX ${_on:.2f} -> market double-flatten")
            self._close_hl_market_force(abs(self.current_q_hl))
            try:
                self.okx.place_order(inst_id=self.cfg.OKX_INST_ID, td_mode="cross", side="buy",
                                     ord_type="market", sz=str(round(abs(self.current_q_okx),3)))
            except Exception as e: logger.error(f"OKX residue flush: {e}")
            return True


        if total_margin > 0.0:
            roi_on_margin = (total_pnl / total_margin) * 100.0
            _cross = self._compute_live_crossing(hl_mid, okx_mid)
            _dyn = max(_cross * 2.5 / 10.0, 8.0)
            self._last_roi_comp = dict(cross_bps=_cross, target=_dyn, roi=roi_on_margin, pnl=total_pnl)
            if tick % 30 == 0:
                logger.info(f"📐 [动态ROI] crossing={_cross:.1f}bps threshold={_dyn:.1f}% now={roi_on_margin:.1f}% pnl=${total_pnl:.2f}")
            if roi_on_margin >= max(self.cfg.TARGET_NET_ROI_PCT, _dyn):
                reason = f"双腿保证金净收益率达标 (+{roi_on_margin:.2f}% >= {self.cfg.TARGET_NET_ROI_PCT}%, 净浮盈: +${total_pnl:.2f} USD)"
                logger.info(f"🎯 [触发综合净利润止盈] {reason}！立即双平落袋为安！")
                self.execute_atomic_close(reason=reason, forced=False)
                return True
        return False

    def _parse_hl_fill(self, hl_res: Dict[str, Any]) -> Tuple[float, float]:
        try:
            if hl_res.get("status") != "ok":
                # [2026-11-09] P0 配额熔断：限频/配额错误会让平仓静默失败 → 变成裸腿
                if "cumulative requests" in str(hl_res) or "Too Many" in str(hl_res):
                    self.quota_until = time.time() + self.cfg.QUOTA_HALT_SEC
                    logger.critical(f"🛑 [HL 配额熔断] 命中请求上限，禁止开仓 {self.cfg.QUOTA_HALT_SEC}s: {str(hl_res)[:120]}")
                return 0.0, 0.0
            resp_data = hl_res.get("response", {})
            statuses = resp_data.get("data", {}).get("statuses", []) if isinstance(resp_data, dict) else []
            if not statuses:
                return 0.0, 0.0
            st = statuses[0]
            if "filled" in st:
                sz = float(st["filled"].get("totalSz", 0.0))
                px = float(st["filled"].get("avgPx", 0.0))
                return sz, px
            return 0.0, 0.0
        except Exception:
            return 0.0, 0.0

    def _emergency_flatten_hl(self, sz: float, ref_px: float):
        try:
            sell_px = round(ref_px * 0.998, 2)
            res = self.hl_exchange.order(
                self.cfg.HL_SYMBOL,
                False,
                sz,
                sell_px,
                {"limit": {"tif": "Ioc"}},
                reduce_only=True
            )
            logger.warning(f"🛡️ [紧急回滚结果] HL 紧急平仓返回: {res}")
        except Exception as e:
            logger.critical(f"FATAL: HL 紧急平仓异常: {e}")

    def run(self):
        logger.info("启动 ANT 跨所套利 V4 自适应复利主循环...")
        self.align_account_leverage()
        self.ws_mgr.start()

        logger.info("等待 WebSocket 盘口首帧接收...")
        for _ in range(30):
            if self.state.hl_ask_px > 0 and self.state.okx_bid_px > 0:
                break
            time.sleep(0.5)

        logger.info(f"WebSocket 数据流就绪！初始盘口 -> HL: ${self.state.hl_bid_px:.2f}/${self.state.hl_ask_px:.2f} | OKX: ${self.state.okx_bid_px:.2f}/${self.state.okx_ask_px:.2f}")

        # 初始同步一次真实持仓与均价
        self.get_real_positions()

        tick = 0
        while self.state.running:
            try:
                tick += 1

                hl_ask = self.state.hl_ask_px
                hl_bid = self.state.hl_bid_px
                okx_bid = self.state.okx_bid_px
                okx_ask = self.state.okx_ask_px

                if hl_ask <= 0 or hl_bid <= 0 or okx_bid <= 0 or okx_ask <= 0:
                    time.sleep(1.0)
                    continue

                # 1. 行情陈旧性死区守卫 (任一边超 2.0s 判定数据无效)
                now_t = time.time()
                hl_age = now_t - self.state.hl_last_ts
                okx_age = now_t - self.state.okx_last_ts
                if hl_age > 6.0 or okx_age > 2.0:
                    if tick % 15 == 0:
                        logger.warning(f"⚠️ [行情陈旧性死区拦截] WS 推流延迟超限 (HL: {hl_age:.2f}s > 6.0s 或 OKX: {okx_age:.2f}s > 2.0s)")
                    time.sleep(0.5)
                    continue

                hl_mid = (hl_ask + hl_bid) / 2.0
                okx_mid = (okx_ask + okx_bid) / 2.0

                # 2. 定期全量持仓与风控巡检 (每 5 秒巡检一次)
                if tick % 5 == 1:
                    q_hl, q_okx, net_delta = self.get_real_positions()

                    # ===== [2026-11-09] P0 三态守门：读不到 != 平仓，未知即跳过全部动作 =====
                    if not getattr(self, "pos_state_known", True):
                        logger.critical(f"🛑 [三态守门] 持仓状态未知({getattr(self,'pos_state_reason','')}) → 本轮不判孤儿、不平仓")
                        time.sleep(1.0)
                        continue

                    # 2.1 孤儿单自动对冲清理器
                    if self.reconcile_orphan_position(q_hl, q_okx):
                        time.sleep(self.cfg.COOLDOWN_AFTER_FILL_SEC)
                        continue

                    # 2.2 单腿 16% 价格偏离硬断路止损
                    if self.check_single_leg_price_drift(hl_mid, okx_mid):
                        time.sleep(self.cfg.EMERGENCY_STOP_COOLDOWN_SEC)
                        continue

                    # 2.3 双腿综合净收益率 >= 3% 止盈
                    if self.check_combined_pnl_take_profit(hl_mid, okx_mid):
                        time.sleep(self.cfg.COOLDOWN_AFTER_FILL_SEC)
                        continue

                    _leg = getattr(self, "_last_usd_leg", 1e-9)
                    _pct = net_delta / _leg * 100.0
                    health = ("HEALTHY" if abs(_pct) < 0.5 else f"⚠️ UNBALANCED (USD {net_delta:+.2f} = {_pct:+.1f}% of leg)")

                    # ===== [2026-11-09] P1 硬约束：裸敞口上限 + 单腿保证金 <= 50% =====
                    try:
                        _wf = self.get_account_balances()
                        _eq_hl, _eq_okx = _wf[2], _wf[3]
                        _n_hl = abs(q_hl) * hl_mid
                        _n_okx = abs(q_okx) * okx_mid
                        _cap = min(min(_n_hl, _n_okx) * self.cfg.MAX_NAKED_USD_PCT_LEG,
                                   min(_eq_hl, _eq_okx) * self.cfg.MAX_NAKED_USD_PCT_EQ)
                        _tot_n = _n_hl + _n_okx
                        _tot_eq = _eq_hl + _eq_okx
                        _u_total = _tot_n / (_tot_eq * self.cfg.TARGET_LEVERAGE) if _tot_eq > 0 else 0.0
                        self.naked_halt = (_cap > 0 and abs(net_delta) > _cap)
                        self.margin_halt = (_u_total > 1.0)
                        if self.naked_halt and tick % 5 == 1:
                            logger.critical(
                                f"🛑 [裸敞口超限] |{net_delta:.2f}| > 上限 {_cap:.2f} → 禁止开仓")
                        if self.margin_halt and tick % 5 == 1:
                            logger.critical(
                                f"🛑 [总杠杆超限] 总名义 ${_tot_n:.2f} / 总权益 ${_tot_eq:.2f} = {_u_total*100:.0f}% > 100% → 禁止开仓")
                        # [2026-11-11] 两腿失衡 >10% 且 spread 达标 → 削腿重平衡
                        _imb = abs(net_delta) / max(min(_n_hl, _n_okx), 1e-9) if min(_n_hl, _n_okx) > 0 else 0.0
                        if _imb > 0.10 and tick % 5 == 1 and not self.naked_halt:
                            logger.warning(f"⚖️ [两腿失衡] {_imb*100:.0f}% > 10% → 等待补/削腿触发")
                    except Exception as e:
                        logger.error(f"硬约束计算异常: {e}")
                        self.naked_halt = True
                    if tick % 15 == 1:
                        logger.info(
                            f"[全量健康巡检] HL仓位: {q_hl:.2f}股 (@${self.hl_entry_px:.2f}) | "
                            f"OKX仓位: {q_okx:.3f}张 (@${self.okx_entry_px:.2f}) | "
                            f"净Delta(USD): ${net_delta:+.2f} ({_pct:+.1f}% of leg) [{health}]"
                        )

                # 3. 实时利差计算
                # 3.1 开仓利差: 买 HL (hl_ask) + 卖 OKX (okx_bid)
                okx_equiv_bid = okx_bid * (10.0 / self.cfg.ASSUMED_SHARES_B)
                open_spread_bps = ((okx_equiv_bid - hl_ask) / hl_ask) * 10000.0 - 6.0

                # 3.2 平仓反向利差: 卖 HL (hl_bid) + 买 OKX (okx_ask)
                okx_equiv_ask = okx_ask * (10.0 / self.cfg.ASSUMED_SHARES_B)
                close_spread_bps = ((okx_equiv_ask - hl_bid) / hl_bid) * 10000.0 + 6.0

                # 3.3 隐含股本 S 与持仓盈亏平衡股本
                implied_s = self.compute_implied_s((hl_bid + hl_ask) / 2.0, (okx_bid + okx_ask) / 2.0)
                be_s = self.compute_breakeven_s()

                # 4. 心跳打印 (双口径明确标注, 避免误读)
                if tick % 15 == 0:
                    s_gap = (implied_s - be_s) / be_s * 100.0 if be_s > 0 else 0.0
                    edge_dbg = self.compute_edge_bps((hl_bid + hl_ask) / 2.0, (okx_bid + okx_ask) / 2.0)
                    logger.info(
                        f"[WS Heartbeat #{tick}] HL: ${hl_bid:.2f}/${hl_ask:.2f} | OKX: ${okx_bid:.2f}/${okx_ask:.2f} | "
                        f"门禁利差({self.cfg.ASSUMED_SHARES_B:.1f}B口径): {open_spread_bps:+.1f}bps "
                        f"(门槛 {self.cfg.ENTRY_SPREAD_BPS:.0f}) | "
                        f"参照折价({self.cfg.REF_SHARES_B:.2f}B口径): {edge_dbg:+.1f}bps "
                        f"-> {'OKX贵/多HL' if edge_dbg > 0 else 'HL贵/多OKX'}) | "
                        f"平仓反向利差: {close_spread_bps:+.1f} bps | "
                        f"隐含S: {implied_s:.2f}B | 保本S: {be_s:.2f}B (差 {s_gap:+.1f}%)"
                    )

                # 4.1 隐含股本 S 断路器
                if self.current_q_hl > 0.05 and abs(self.current_q_okx) > 0.005 and implied_s > 0:
                    if implied_s >= self.cfg.IMPLIED_S_HARD_LIMIT_B:
                        logger.critical(
                            f"🚨 [隐含股本硬熔断] S={implied_s:.2f}B >= {self.cfg.IMPLIED_S_HARD_LIMIT_B}B，"
                            f"收敛终点已远超保本线 {be_s:.2f}B，立即全平离场！"
                        )
                        if self.execute_atomic_close(reason=f"隐含股本 {implied_s:.2f}B 破硬上限", forced=True):
                            time.sleep(self.cfg.COOLDOWN_AFTER_FILL_SEC)
                            continue
                    elif implied_s >= self.cfg.IMPLIED_S_DELEVER_B:
                        logger.warning(
                            f"✂️ [隐含股本减仓] S={implied_s:.2f}B >= {self.cfg.IMPLIED_S_DELEVER_B}B，"
                            f"保本线 {be_s:.2f}B，强制减半仓"
                        )
                        if self.execute_atomic_deleverage(0.5, f"隐含股本 {implied_s:.2f}B 破减仓线"):
                            time.sleep(self.cfg.COOLDOWN_AFTER_FILL_SEC)
                            continue
                    elif implied_s >= self.cfg.IMPLIED_S_ALERT_B and tick % 60 == 0:
                        logger.warning(
                            f"⚠️ [隐含股本告警] S={implied_s:.2f}B >= {self.cfg.IMPLIED_S_ALERT_B}B，"
                            f"距保本线 {be_s:.2f}B 仅 {(implied_s - be_s) / be_s * 100:.1f}%，收敛利润正在被侵蚀"
                        )


                # 5. 状态机动作判断
                # [2026-11-11] 时间止损: >12h ROI<5% -> 强制平
                if (self.current_q_hl > 0.05 and abs(self.current_q_okx) > 0.005
                        and time.time() - getattr(self, '_hold_since', 0.0) > 12 * 3600):
                    _m = (abs(self.current_q_hl)*hl_mid+abs(self.current_q_okx)*okx_mid)/self.cfg.TARGET_LEVERAGE
                    _r = (self.current_q_hl*(hl_mid-self.hl_entry_px)+abs(self.current_q_okx)*(self.okx_entry_px-okx_mid))/_m*100 if _m>0 else 0
                    if _r < 5.0:
                        logger.critical(f"⏰ [时间止损] {(time.time()-self._hold_since)/3600:.1f}h ROI {_r:.1f}% < 5% 强制平")
                        if self.execute_atomic_close(reason=f"时间止损({(time.time()-self._hold_since)/3600:.1f}h)", forced=True):
                            self._hold_since = time.time(); time.sleep(self.cfg.COOLDOWN_AFTER_FILL_SEC); continue

                # 5.1 检查是否满足平仓条件 (持仓中且利差收窄)
                if self.current_q_hl > 0.05 and abs(self.current_q_okx) > 0.005:
                    # [2026-11-11] 波段/终极止盈已删除；出场由动态 ROI + 时间止损管理
                    pass

                # 5.2 检查是否满足开仓/加仓条件
                _halt = (getattr(self, "naked_halt", False) or getattr(self, "margin_halt", False)
                         or time.time() < getattr(self, "quota_until", 0.0))
                if _halt and tick % 15 == 0:
                    logger.warning(
                        f"⛔ [开仓门禁-硬约束] naked={getattr(self,'naked_halt',False)} "
                        f"margin={getattr(self,'margin_halt',False)} "
                        f"quota={time.time() < getattr(self,'quota_until',0.0)} → 禁止新增敞口(补腿除外)")

                # [2026-11-09] 补偿腿模式：裸敞口超限时不停手，
                # 只要价差仍达标就把「缺的那条腿」补齐到中性（只减 |net_delta|）
                if (getattr(self, "naked_halt", False)
                        and time.time() >= getattr(self, "quota_until", 0.0)):
                    self._topup_deficient_leg(hl_mid, okx_mid)
                if (not _halt) and open_spread_bps >= self.cfg.ENTRY_SPREAD_BPS:
                    # [已停用 2026-11-08] 原开仓 S 上限门禁：S=15.19 放行、S>=15.20 立刻硬熔断，
                    # 等于在悬崖边开仓再被自己推下去。平仓侧熔断 IMPLIED_S_HARD_LIMIT_B 保持不变。
                    # if implied_s >= self.cfg.MAX_ENTRY_IMPLIED_S_B:
                    #     if tick % 15 == 0:
                    #         logger.info(
                    #             f"开仓门禁被 S 上限拦截: 隐含 S={implied_s:.2f}B >= {self.cfg.MAX_ENTRY_IMPLIED_S_B}B, 只平不开"
                    #         )
                    #     time.sleep(1.0)
                    #     continue
                    logger.info(
                        f"🎯 开仓门禁满足！门禁利差({self.cfg.ASSUMED_SHARES_B:.1f}B口径) "
                        f"{open_spread_bps:+.1f}bps >= {self.cfg.ENTRY_SPREAD_BPS:.0f}bps, 进入配平规划"
                    )
                    success = self.execute_atomic_trade(hl_ask, okx_bid)
                    if success:
                        time.sleep(self.cfg.COOLDOWN_AFTER_FILL_SEC)
                    else:
                        time.sleep(self.cfg.SAFETY_FAILURE_COOLDOWN_SEC)

                time.sleep(1.0)

            except Exception as e:
                logger.error(f"主调度异常: {e}")
                time.sleep(5.0)


def main():
    OKX_API_KEY = os.environ.get("OKX_API_KEY", "")
    OKX_API_SECRET = os.environ.get("OKX_API_SECRET", "")
    OKX_PASSPHRASE = os.environ.get("OKX_PASSPHRASE", "")

    HL_ACCOUNT = "0xAF3E93d42b56d517F055dDF64bcB4531c0B1E815"
    HL_AGENT_KEY = os.environ.get("HL_AGENT_KEY", "")

    missing = [k for k, v in (("OKX_API_KEY", OKX_API_KEY), ("OKX_API_SECRET", OKX_API_SECRET),
                              ("OKX_PASSPHRASE", OKX_PASSPHRASE), ("HL_AGENT_KEY", HL_AGENT_KEY)) if not v]
    if missing:
        raise RuntimeError(f"缺少环境变量: {', '.join(missing)} (应来自 /etc/ant-arb.env)")

    engine = AtomicDualLegEngineV4(
        hl_account=HL_ACCOUNT,
        hl_agent_key=HL_AGENT_KEY,
        okx_api_key=OKX_API_KEY,
        okx_secret=OKX_API_SECRET,
        okx_passphrase=OKX_PASSPHRASE
    )
    engine.run()


if __name__ == "__main__":
    main()
