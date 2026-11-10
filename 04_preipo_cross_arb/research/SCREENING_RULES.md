# 选票判据：b\*、p95、深度密度（可执行版）

本文件把用户那句判据形式化，并给出两个仓库（`ant-cross-arb` / `arbTwin`）口径的对接与差异。
公式出处：`screen-math` worker 的推导（`.herd-dispatch/jobs/screen-math/report.md`）+ `arbTwin/arbtwin/{screening,fees,mechanics,spread}.py`。
原型：`.herd-dispatch/jobs/screen-math/proto_screen.py`（纯函数、无网络、自测通过）。

---

## 0. 结论先行

1. **判据是联立的**：`p95(|b|) ≥ k · b*` 且 两腿深度都过闸。单看基差或单看深度都会误放。
2. **`b*` 必须六项齐全**：往返手续费 + 两腿半价差 + 成交滑点 + 资金费 carry + **腿风险溢价** + 对账摩擦。
   `arbTwin` 当前只算了前三项（≈8.5 bps），完整口径 ≈**19.6 bps**（同一组假设下）。
   **差 11 bps 足以把一个 p95=22 bps 的标的从「通过」翻成「否决」**。
3. **k 不是保守系数，是 p95 的抽样误差**：`k = 1 + 1/√ESS`，`ESS = n(1−ρ)/(1+ρ)`。
   14 天小时线、ρ≈0.6 → k≈1.15；只有 50 个点、ρ=0.8 → k≈1.41。
4. **深度密度要用两个互补定义**：`D₁`（每 bp 名义额，USD/bp）拦薄盘口；`D₂`（吃掉本单所需 bps）拦「只有少数价位有量」。两者 AND。
5. **`p95 − b*` 才是可交易性；`p95` 只是机会大小。** 出场按 `p50` 回归计，故一轮的期望捕获 ≈ `p95 − p50 − b*`。

---

## 1. 符号表

| 符号 | 含义 | 单位 | 约定 |
|---|---|---|---|
| `b` | 基差 = `(P_A / ratio − P_B) / P_B` | bps | 正 = A 侧贵 |
| `ratio` | 单位换算比（校准得出，不是猜） | — | 1:1 时 = 1 |
| `p95` | `|b|` 的 95 分位（**分时段**：RTH / 收盘后分开算） | bps | 有效样本数需报出 |
| `p50` | `|b|` 的中位 | bps | 出场锚 |
| `ρ` | `b` 的 AR(1) 自相关 | — | 半衰期 `t½ = −ln2/lnρ` |
| `b*` | 保本基差（六项） | bps | 见 §2 |
| `t_leg` | 一腿成交到另一腿成交的时长（中位数） | s | 从 fill 链测 |
| `P_1leg` | 单腿成交另一腿未成的概率 | — | 从 fill 链测，无数据取 0.30 |
| `σ_b` | 基差 1 分钟波动 | bps/min | 1m 收益标准差 |
| `D₁` | 深度密度 = 覆盖名义 / 覆盖 bps | USD/bp | 盘口两侧分别算 |
| `D₂` | 吃掉本单所需 bps | bps | 逆函数 |
| `N_leg` | 单腿名义 | USD | `clip × price` |

---

## 2. `b*` 六项展开

$$
b^* = \underbrace{f_{\text{round}}}_{①} + \underbrace{S_A + S_B}_{②} + \underbrace{s_{\text{exec}}}_{③} + \underbrace{c_{\text{carry}}}_{④} + \underbrace{r_{\text{leg}}}_{⑤} + \underbrace{f_{\text{recon}}}_{⑥}
$$

| 项 | 定义 | 可观测来源 | ant 场景取值 |
|---|---|---|---|
| ① 往返手续费 | `Σ(进+出)` 每腿 | OKX `makerFee/takerFee`；HL perp 基础档 4.5/1.5 bps + deployer share；实测回执 `fee` 字段 | 双 maker ≈3.6 bps；一腿 taker ≈8.6 bps；实测台账 ≈6.05 bps/往返 |
| ② 两腿半价差 | 每条腿跨一次半价差，往返两次 | `l2Book` / `books5` 的 `(ask−bid)/mid` | 见 `MEASURED_CROSS_DEX.md`：HL 0.6~1.2 bps，io 5.5~9.4 bps |
| ③ 成交滑点 | 限价容差 τ 的期望 ≈ τ/2；IOC 按穿透深度 | 配置 `MAX_SLIPPAGE_BPS` | τ=2.33 → 1.17 bps；τ=5 → 2.5 bps |
| ④ 资金费 carry | `|Σ r_v · h/τ_v|`（保守取绝对值） | OKX `fundingRate`（/8h）、HL `funding`（/1h） | 与持仓时长成正比：h=4h、HL 0.00125%/h → 5 bps |
| ⑤ 腿风险溢价 | `√(2/π)·σ_b·√(t_leg/60)·P_1leg·α` | fill 链；σ 从 1m K 线 | t_leg=4 s → 0.2 bps（σ_b=12）；t_leg=30 s、σ_b=23 → 0.4 bps |
| ⑥ 对账摩擦 | 固定预留 | `[假设]` 0.5 bps | 有实测后替换 |

**关键结论**：腿风险溢价单独很小（<1 bps），**它杀死策略的方式不是自身大，而是与「追价滑点」叠加后吃掉全部限价收益**。
`CAPTAIN_VERDICT.md §2.2` 已独立算出：pulse 态下 4 秒档库存成本 2.37 bps > 单边费差 2.33 bps → 时间阶梯不能超过 ~2 s，或直接改用 0.1 s IOC。

---

## 3. 联立判据

```
可交易 ⟺  p95(|b|)  ≥  k · b*
       ∧  D₁_A ≥ D_min  ∧  D₁_B ≥ D_min
       ∧  D₂_A ≤ max_impact  ∧  D₂_B ≤ max_impact
       ∧  ratio 校准通过（CV ≤ 0.5% 且 收益相关 ≥ 0.99）
       ∧  两腿 24h 名义额 > 0 且 top-of-book 两侧深度 > 0
```

最后一条是本次实测新加的：`km`/`mkts` 有 mark、无成交，会被前五条漏过。

### 3.1 为什么不能用 p50 或均值
- `p50`：一半时间基差 ≤ 中位 → 那一半时间连成本都不够。
- 均值：被 0 附近噪声主导，无法区分「偶尔宽」与「一直窄」。
- `p95` 的语义是「5% 的尾事件」；策略经济性来自**尾事件的频率 × 回归幅度**，所以必须再配一次**事件研究**（选出所有 `|b| ≥ p95` 的时刻，看之后 1/2/4 h 的回归幅度与完成率）。

### 3.2 k 的取值
`k = 1 + 1/√ESS`。表：

| n（14 天小时线） | ρ | ESS | k |
|---:|---:|---:|---:|
| 336 | 0.4 | 144 | 1.083 |
| 336 | 0.6 | 84 | 1.109 |
| 168 | 0.6 | 42 | 1.154 |
| 50 | 0.8 | 5.6 | 1.42 |
| 1200 | 0.3 | 646 | 1.039 |

对大 tick、低流动的股票合约（1h 采样稀疏），**先报 ESS，再报 p95**；ESS < 30 时 p95 只能当参考。

---

## 4. 深度密度

### 4.1 两个定义
- `D₁ = N_covered / covered_bps`（USD/bp）：与 tick 密度无关，可跨标的比较；但如果盘口只有 3 档，`covered_bps` 很小，`D₁` 会虚高。
- `D₂ = argmin{x : notional_to_cross(x, side) ≥ N_leg}`（bps）：直接是冲击成本；但盘口很深时只能给下界。

### 4.2 最小深度
```
D_min = max( N_leg / (100 × 0.10),  configured_min_depth_usd_pct / 100 )
```
含义：100 bps 区间内的深度至少要是单笔的 10 倍。
以 `ant` 现状（clip 0.1 股 ≈ $9.84）为例，`D_min ≈ $200/bp`（配置下限主导），几乎不约束；
真正约束的是 `D₂`：薄腿（`io`/OKX 小票）上 `N_leg` 会把盘口推 5~10 bps，**直接吃掉 `p95 − b*`**。

### 4.3 与实测的对接
`MEASURED_CROSS_DEX.md` 的 ±25 bps 深度：`io:EWY` 仅 **$42,214**，按「单笔 ≤ 深度 10%」上限是 **$4.2k**；
`io:AAPL` $133k → $13.3k；`xyz:NVDA` $1.74M → $174k。
→ **`arbTwin` 的 `$40k` 绝对门槛在 HIP-3 小票上等于「一票否决」**，包括 ASML（日成交 $1.07M）。
要么把门槛改成「相对本系统单笔规模」的倍数，要么接受 ASML 这类标的只能做极小仓。

---

## 5. 立即能跑的自检（不接网络）

| # | 检验 | 数据 | 期望 |
|---|---|---|---|
| 1 | `p95 − k·b*` 的正负与已批准/已拒绝清单一致 | `arbTwin` 的 `config/symbols.yaml` + screening 输出 | 无反转 |
| 2 | 做空侧资金费 APR > 0 | `state/monitor.jsonl` 的 `funding_*_apr` | 否则 carry 是逆风，要计入 b* |
| 3 | `P_1leg` 实测 | fill 链（两腿时间戳差） | >0.3 说明阶梯太长或盘口太薄 |
| 4 | `max_hold ≤ 2 × t½` | screening 缓存的 `half_life` | 超过说明大部分时间在「已回归仍持有」 |
| 5 | `D₁ × 100 × 0.10 ≥ max_position_usd` | `arbTwin` 的 `risk.yaml` | 违反则名义与深度自相矛盾 |

---

## 6. 口径差异（不要混用）

| 项 | `arbTwin`（OKX↔Lighter） | `ant-cross-arb`（HL↔OKX） |
|---|---|---|
| 单位 | `calibration.py` 实测 ratio，CV<0.5% | 硬编码 `ASSUMED_SHARES_B` + rebase 事件 |
| 费率 | Lighter 0/0 + OKX 2/5 | HL −0.2/2.5（maker 返佣）+ OKX 2/5 |
| 往返成本 | 双 maker 4 bps / taker-hedge 12 bps | 双 maker 3.6 bps / taker-hedge 12.3 bps |
| 资金费 | Lighter 1h / OKX 8h | HL 1h / OKX 8h |
| 选票主风险 | 单位漂移（CV） | rebase（一次性跳变）+ 快照陈旧 |

**把 `arbTwin` 的 8.5 bps 直接搬到 `ant` 会误杀 + 误放，两者 b\* 基值差 ≈11 bps。**