#!/usr/bin/env python3
"""recon_ledger.py — ANT 跨所双腿套利的对账核心（纯逻辑，无网络依赖，可单测）

设计约束（来自 CAPTAIN_VERDICT.md §1）：
  L1  持仓用「股等价」计算裸敞口，不能用合约张数直接相加
      1 张 OKX = S/10 股；S=12.7 → 1.27 股/张（不是 0.7874！0.7874 是「每 1 股需要几张」）
  L2  Hedge Ledger 存 target 而非 delta：residual 由两条腿的实际持仓重算，
      任何 ack 到达顺序都不会让重发翻倍仓位
  L3  「读到空列表」≠「零持仓」：必须 4 项确认全部通过，否则该腿 UNKNOWN
  L4  单腿异常按 A/B/C 三分支查表处置，禁止「猜哪条腿该留」
  L5  平仓是双腿事务：只有两腿真实为 0 才 FLAT
  L6  时间维度裸持止损（30/120/300s），比价格维度早 3 个数量级触发

本模块不发起任何网络请求；读取器由调用方注入，便于用 R6 时序做回归测试。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Sequence, Tuple

# ---------------------------------------------------------------- 配置

ASSUMED_SHARES_B = 12.7          # 股本基准（B 股）；必须与结算 rebase 用同一个 S
OKX_INDEX_DENOM_B = 10.0         # OKX 指数分母：报价 = 估值 / 100 亿份

DUST_SHARES = 0.01               # 实测 HL xyz:ANT szDecimals=2 -> 最小可交易步长 0.01 股；
                                 # 对比 OKX lotSz=0.001 张 × 1.27 = 0.00127 股。取 max = 0.01 股。
HEDGE_TIMEOUT_MS = 3000
HEDGE_MAX_ATTEMPTS = 3
# 时间维度阈值：以「尾部分布」而非「期望漂移」定标（见 CAPTAIN_VERDICT §1.7）
# 价格维度在 R6 里结构性失效：16% 止损按当时漂移率需 172 小时才触发（=7.2 天）
NAKED_WARN_S = 60.0      # 60s = 约 30x 设计意图的对冲延迟（引擎 2.0s 死区 / 60ms STP 间隔）
NAKED_FORCE_S = 180.0    # 180s：各被动阶梯至少跑完 2 轮；按 R6 实测漂移只损失 ~bash.009，远小于立即市价平的 ~bash.10
NAKED_HALT_S = 600.0     # 600s：自动补救本身已失效 -> 全撤 + 双腿平 + 停服 + 告警
ZERO_CONFIRM_READS = 3
ZERO_CONFIRM_INTERVAL_S = 1.0
MAX_REPAIR_SLIPPAGE_BPS = 10.0
REPAIR_TIMEOUT_S = 5.0
REPAIR_MAX_ATTEMPTS = 1


def shares_per_contract(shares_b: float = ASSUMED_SHARES_B) -> float:
    """1 张 OKX 合约在结算时对应多少股 = S / 10B。S=12.7 → 1.27"""
    if shares_b <= 0:
        raise ValueError("shares_b must be positive")
    return shares_b / OKX_INDEX_DENOM_B


def contracts_per_share(shares_b: float = ASSUMED_SHARES_B) -> float:
    """对冲 1 股 HL 需要多少张 OKX = 10B / S。S=12.7 → 0.7874（即 HEDGE_RATIO）"""
    return 1.0 / shares_per_contract(shares_b)


# ---------------------------------------------------------------- 状态

class ReconState(Enum):
    FLAT = "FLAT"
    OPENING = "OPENING"
    HEDGED = "HEDGED"
    HEDGE_PENDING = "HEDGE_PENDING"
    OVERHEDGED = "OVERHEDGED"
    UNDERHEDGED = "UNDERHEDGED"
    ORPHAN_SUSPECT = "ORPHAN_SUSPECT"
    ORPHAN_CONFIRMED = "ORPHAN_CONFIRMED"
    CLOSING = "CLOSING"
    HALT = "HALT"


NEW_ENTRY_ALLOWED = frozenset({ReconState.FLAT, ReconState.HEDGED})


class ReadStatus(Enum):
    OK = "OK"
    STALE = "STALE"
    UNKNOWN = "UNKNOWN"


@dataclass
class LegReading:
    """单腿一次读取结果。qty 为带符号持仓（+多 -空）；None 表示读不到。"""
    qty: Optional[float]
    status: ReadStatus
    ts: float
    open_orders: int = 0
    pending_cancels: int = 0
    explained_by_fills: bool = False
    http_ok: bool = True
    code_ok: bool = True
    structure_ok: bool = True


# ---------------------------------------------------------------- Ledger

@dataclass
class HedgeLedger:
    """两条腿唯一权威。所有派生量都是纯函数，不做增量累加。"""
    q_hl: float = 0.0
    q_okx: float = 0.0
    shares_b: float = ASSUMED_SHARES_B
    dust: float = DUST_SHARES

    @property
    def spc(self) -> float:
        """股/张"""
        return shares_per_contract(self.shares_b)

    @property
    def target_okx(self) -> float:
        """与 HL 完全对冲所需的 OKX 持仓（带符号）"""
        return -self.q_hl / self.spc

    @property
    def residual_okx(self) -> float:
        """>0 买入、<0 卖出 OKX"""
        return self.target_okx - self.q_okx

    @property
    def naked_shares(self) -> float:
        """股等价裸敞口。完美对冲为 0。"""
        return self.q_hl + self.q_okx * self.spc

    def is_flat(self) -> bool:
        return abs(self.q_hl) <= self.dust and abs(self.q_okx * self.spc) <= self.dust

    def is_hedged(self) -> bool:
        return abs(self.naked_shares) <= self.dust and not self.is_flat()

    def classify(self) -> ReconState:
        if self.is_flat():
            return ReconState.FLAT
        if self.is_hedged():
            return ReconState.HEDGED
        if self.residual_okx > self.dust:
            return ReconState.OVERHEDGED      # OKX 空得太多 -> 需买回
        if self.residual_okx < -self.dust:
            return ReconState.UNDERHEDGED     # OKX 空得不够 -> 需补空
        return ReconState.HEDGE_PENDING


# ---------------------------------------------------------------- 零持仓正向确认

def confirm_zero(reads: Sequence[LegReading]) -> Tuple[bool, str]:
    """一串「读到 0/空」是否足以认定该腿真为零持仓。任一不满足即 False（fail closed）。"""
    if not reads:
        return False, "no readings"
    for i, r in enumerate(reads):
        if not r.http_ok:
            return False, f"read#{i}: http not ok"
        if not r.code_ok:
            return False, f"read#{i}: exchange code != 0"
        if not r.structure_ok:
            return False, f"read#{i}: unexpected response structure"
        if r.status is not ReadStatus.OK:
            return False, f"read#{i}: status={r.status.value}"
        if r.qty is None:
            return False, f"read#{i}: qty unreadable"
        if r.open_orders or r.pending_cancels:
            return False, f"read#{i}: {r.open_orders} open / {r.pending_cancels} pending-cancel"
    if len(reads) < ZERO_CONFIRM_READS:
        return False, f"only {len(reads)} readings, need {ZERO_CONFIRM_READS}"
    tail = reads[-ZERO_CONFIRM_READS:]
    if any(abs(r.qty or 0.0) > 0 for r in tail):
        return False, "nonzero qty inside confirmation window"
    if not all(r.explained_by_fills for r in tail):
        return False, "disappearance not explained by fills/positions-history"
    return True, "confirmed zero"


def build_ledger(
    hl_read: LegReading,
    okx_read: LegReading,
    hl_zero_confirmed: bool,
    okx_zero_confirmed: bool,
) -> Tuple[Optional[HedgeLedger], str]:
    """读数 -> 账本。读数不可信时返回 None（调用方必须 MUST_CONFIRM，禁止继续）。"""
    if hl_read.status is not ReadStatus.OK or hl_read.qty is None:
        return None, f"HL reading {hl_read.status.value}"
    if okx_read.status is not ReadStatus.OK or okx_read.qty is None:
        return None, f"OKX reading {okx_read.status.value}"
    if abs(hl_read.qty) == 0 and not hl_zero_confirmed:
        return None, "HL reads zero but not positively confirmed"
    if abs(okx_read.qty) == 0 and not okx_zero_confirmed:
        return None, "OKX reads zero but not positively confirmed"
    return HedgeLedger(q_hl=hl_read.qty, q_okx=okx_read.qty), "ok"


# ---------------------------------------------------------------- 单腿三分支

class Action(Enum):
    NONE = "NONE"
    HALT = "HALT"
    MUST_CONFIRM = "MUST_CONFIRM"
    CLOSE_SURVIVOR = "CLOSE_SURVIVOR"      # A
    WAIT_INFLIGHT = "WAIT_INFLIGHT"        # B
    TRADE_RESIDUAL = "TRADE_RESIDUAL"      # C


@dataclass
class Decision:
    action: Action
    leg: str = ""              # 要交易的那条腿
    size: float = 0.0          # 带符号数量（该腿单位）
    max_slippage_bps: float = 0.0
    timeout_s: float = 0.0
    max_attempts: int = 0
    reason: str = ""


def decide(
    ledger: Optional[HedgeLedger],
    ledger_error: str = "",
    okx_inflight_close: bool = False,
    hl_inflight_close: bool = False,
) -> Decision:
    """现场 -> 动作。禁止猜测。"""
    if ledger is None:
        return Decision(Action.MUST_CONFIRM, reason=ledger_error)

    hl_has = abs(ledger.q_hl) > ledger.dust
    okx_shares = ledger.q_okx * ledger.spc
    okx_has = abs(okx_shares) > ledger.dust

    # A: 一腿为 0、另一腿有仓、无在途平仓
    if hl_has and not okx_has:
        if okx_inflight_close:
            return Decision(Action.WAIT_INFLIGHT, leg="OKX",
                            timeout_s=REPAIR_TIMEOUT_S,
                            max_attempts=REPAIR_MAX_ATTEMPTS,
                            reason="OKX flat but close inflight; wait terminal state")
        return Decision(Action.CLOSE_SURVIVOR, leg="HL", size=ledger.q_hl,
                        max_slippage_bps=MAX_REPAIR_SLIPPAGE_BPS,
                        timeout_s=REPAIR_TIMEOUT_S, max_attempts=REPAIR_MAX_ATTEMPTS,
                        reason="ORPHAN: OKX confirmed flat, close HL survivor")
    if okx_has and not hl_has:
        if hl_inflight_close:
            return Decision(Action.WAIT_INFLIGHT, leg="HL",
                            timeout_s=REPAIR_TIMEOUT_S,
                            max_attempts=REPAIR_MAX_ATTEMPTS,
                            reason="HL flat but close inflight; wait terminal state")
        return Decision(Action.CLOSE_SURVIVOR, leg="OKX", size=ledger.q_okx,
                        max_slippage_bps=MAX_REPAIR_SLIPPAGE_BPS,
                        timeout_s=REPAIR_TIMEOUT_S, max_attempts=REPAIR_MAX_ATTEMPTS,
                        reason="ORPHAN: HL confirmed flat, close OKX survivor")

    if not hl_has and not okx_has:
        return Decision(Action.NONE, reason="flat")

    # C: 两腿都有仓但不匹配 -> 只交易 residual
    if abs(ledger.naked_shares) > ledger.dust:
        return Decision(Action.TRADE_RESIDUAL, leg="OKX", size=ledger.residual_okx,
                        max_slippage_bps=MAX_REPAIR_SLIPPAGE_BPS,
                        timeout_s=REPAIR_TIMEOUT_S, max_attempts=REPAIR_MAX_ATTEMPTS,
                        reason=f"rebalance residual={ledger.residual_okx:+.4f} ct")

    return Decision(Action.NONE, reason="in balance")


# ---------------------------------------------------------------- 时间维度裸持止损

class OrphanWatchdog:
    """价格维度的 16% 止损在 R6 里从未触发（15.5h 只走 1.47%）。时间维度才是主止损。"""

    def __init__(self) -> None:
        self.since: Optional[float] = None

    def observe(self, now: float, naked_shares: float, inflight_hedge: bool) -> Optional[str]:
        exposed = abs(naked_shares) > DUST_SHARES and not inflight_hedge
        if not exposed:
            self.since = None
            return None
        if self.since is None:
            self.since = now
            return None
        held = now - self.since
        if held >= NAKED_HALT_S:
            return f"HALT: naked {held:.0f}s >= {NAKED_HALT_S:.0f}s"
        if held >= NAKED_FORCE_S:
            return f"FORCE: naked {held:.0f}s >= {NAKED_FORCE_S:.0f}s"
        if held >= NAKED_WARN_S:
            return f"WARN: naked {held:.0f}s >= {NAKED_WARN_S:.0f}s"
        return None


# ---------------------------------------------------------------- 填充链与"填充隐含持仓"

@dataclass
class Fill:
    """一条成交。start_position 用于 HL 免费提供的链式校验。"""
    leg: str                     # "HL" | "OKX"
    signed_sz: float             # +买 -卖
    start_position: Optional[float] = None
    trade_id: Optional[str] = None


def verify_fill_chain(fills: Sequence[Fill]) -> Tuple[bool, str]:
    """校验同一腿的成交链是否连续。

    Hyperliquid 的 userFills 每条都带 startPosition，于是有一条**免费且精确**的不变式：
        下一条.startPosition == 上一条.startPosition + 上一条.signed_sz
    链断 = 有成交没被我们收到 = 该腿持仓未知（绝不能当成 0）。

    R6 这类事故的根治就在这里：不依赖 positions 快照，而是用成交把持仓"算"出来。
    """
    by_leg = {}
    for f in fills:
        by_leg.setdefault(f.leg, []).append(f)
    for leg, seq in by_leg.items():
        ordered = sorted(seq, key=lambda x: (x.trade_id is None, x.trade_id or ""))
        for prev, cur in zip(ordered, ordered[1:]):
            if prev.start_position is None or cur.start_position is None:
                continue
            expect = prev.start_position + prev.signed_sz
            if abs(expect - cur.start_position) > 1e-6:
                return False, (f"{leg} fill chain broken: expect start={expect:.6f} "
                               f"got {cur.start_position:.6f}")
    return True, "chain ok"


def fill_implied_position(fills: Sequence[Fill], leg: str) -> Optional[float]:
    """由成交推算该腿持仓：最后一条的 startPosition + signed_sz。

    这是独立于 positions 快照的第二事实源——快照说 0、成交链说 -1.858 时，以成交链为准。
    没有成交时返回 None（未知），不返回 0。
    """
    seq = [f for f in fills if f.leg == leg and f.start_position is not None]
    if not seq:
        return None
    last = seq[-1]
    return last.start_position + last.signed_sz


def cross_check_position(
    snapshot_qty: Optional[float],
    fills: Sequence[Fill],
    leg: str,
    tol: float = DUST_SHARES,
) -> Tuple[bool, str]:
    """快照 vs 成交链。任一为 None 即不可判；两者冲突即 UNKNOWN（绝不取 0）。"""
    implied = fill_implied_position(fills, leg)
    if snapshot_qty is None or implied is None:
        return False, f"{leg}: insufficient evidence (snapshot={snapshot_qty}, implied={implied})"
    if abs(snapshot_qty - implied) > tol:
        return False, (f"{leg}: snapshot {snapshot_qty:+.6f} != fill-implied "
                       f"{implied:+.6f} (diff {snapshot_qty - implied:+.6f})")
    return True, "consistent"


# OKX：这些 subType / position type 表示"被交易所强制平仓"，策略假设已失效
LIQ_BILL_SUBTYPES = frozenset({"100", "101", "104", "105", "125", "126", "127", "128"})
LIQ_POSITION_TYPES = frozenset({"3", "4", "5"})


def liquidation_or_adl_detected(bill_subtypes: Sequence[str] = (),
                                position_types: Sequence[str] = ()) -> bool:
    """强平 / 部分强平 / ADL 检测。命中即必须 KILL —— 不是普通的持仓不一致。"""
    return bool(set(bill_subtypes) & LIQ_BILL_SUBTYPES) or \
           bool(set(position_types) & LIQ_POSITION_TYPES)


def external_drift_detected(
    snapshot_qty: Optional[float],
    ledger_qty: float,
    unmatched_fill_count: int,
    tol: float = DUST_SHARES,
) -> Tuple[bool, str]:
    """快照与账本不一致，且没有对应成交 -> 外部干预（手动平/ADL/强平）。

    处理方式不是"忽略"，而是"隔离该腿 + reduce-only 收敛回 target"。
    """
    if snapshot_qty is None:
        return False, "unknown"
    if abs(snapshot_qty - ledger_qty) <= tol:
        return False, "in sync"
    if unmatched_fill_count > 0:
        return False, "explained by fills"
    return True, (f"EXTERNAL_DRIFT: snapshot {snapshot_qty:+.6f} vs ledger "
                  f"{ledger_qty:+.6f} with no matching fill")
