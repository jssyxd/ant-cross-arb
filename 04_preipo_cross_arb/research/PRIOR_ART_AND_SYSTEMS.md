# 先行技术：公开实现能给我们什么（2026-11-11 核验版）

只收录**我自己打开过 README 或 API 字段**的仓库。星数/语言/最后推送时间为 2026-11-11 抓取
（`https://api.github.com/repos/<owner>/<repo>`）。未能打开仓库页的结论一律标 `[未验证]`。

---

## 0. 结论先行

1. **公开仓库里没有「跨场所原子双腿」**：HL L1 与 CEX 之间不存在共同清算域，Solana 的 DvP 类方案要求两腿同链。
   → **裸腿只能靠「状态机 + 账本 + 时间止损」管理，不能靠下单原语消灭。** 这条决定了架构，任何仓库都替不了。
2. **最接近我们策略的公开实现是 `adolyb/entropy-anth-carry`**（0 star，2026-08-28 推送）：`io:ANTH` 多 + Binance `ANTHROPICUSDT` 空，delta-neutral 资金费 carry，外加「刻意做小」的双向基差 fade。
   它的 README 直接给出了**可复用的实测数字**（见 §2），并且明确说执行部分是唯一能下单的组件、默认只读。
3. **工程范式最值得抄的是三家**：Hummingbot 的 `ArbitrageExecutor`（状态机+成交跟踪）、VOOI 的示例 bot（17 道开仓闸门 + 仓位级止损）、`Gajesh2007/funding-arb-bot`（漂移检测 + 崩溃恢复 + 组合回撤熔断）。
4. **「有引擎」的仓库是少数**：GitHub 上 80% 以上带 `hyperliquid arbitrage` 字样的仓库只是扫描器或 SEO 占位，没有持仓跟踪、没有对账。
5. **本仓库与 `arbTwin` 已有实现对多数缺陷已有答案**，缺的是「换到多腿/多场所之后的账本与闸门」，而不是从零造轮子。

---

## 1. 核验过的仓库清单

| 仓库 | 语言 / 星 / 最后推送 | 场所对 | 它做了什么 | 可借鉴点 | 等级 |
|---|---|---|---|---|---|
| [adolyb/entropy-anth-carry](https://github.com/adolyb/entropy-anth-carry) | Python / 0 / 2026-08-28 | HL `io:ANTH` ↔ Binance `ANTHROPICUSDT` | 资金费 carry（主策略）+ 双向基差 fade（刻意小仓） | 同族策略的实测费率/OI cap/盘口结论；`--live` 才下单的默认安全姿态 | **高** |
| [vooi-app/vooi-funding-bot-example](https://github.com/vooi-app/vooi-funding-bot-example) | Python / 13 / 2026-06-04 | HL ↔ Lighter（Aster 代码内、默认关） | 生产级 delta-neutral funding arb，经 VOOI Perps API | 17 道开仓闸门、每腿保证金检查、仓位级止损、NDJSON 审计 | **高** |
| [Gajesh2007/funding-arb-bot](https://github.com/Gajesh2007/funding-arb-bot) | TypeScript / 30 / 2025-12-06 | HL ↔ Lighter | 资金费套利全流程 | 费率 2σ 尖峰过滤、双腿执行+失败即对账、**漂移检测**、崩溃恢复、组合回撤熔断、资金费实收对账 | **高** |
| [hummingbot/hummingbot](https://github.com/hummingbot/hummingbot) `ArbitrageExecutor` | Python / 大 / 持续 | 多场所 | 跨所套利执行器 | `control_task()` 轮询订单状态；`CloseType` 区分 COMPLETED/FAILED/INSUFFICIENT_BALANCE；`TrackedOrder` 记成交均价 | **高**（范式） |
| [SamEhr44/funding-rate-arb](https://github.com/SamEhr44/funding-rate-arb) | Python / 0 / 2026-06-08 | HL ↔ Binance | Hummingbot v2 资金费栈 | FastAPI 风控服务、Prometheus/Grafana、3% 日损断路器、默认 paper | 中 |
| [counterfactual5/funding-arb](https://github.com/counterfactual5/funding-arb) | Rust/TS / — / — | CEX+DEX | 资金费 + cash-and-carry | `real_edge` 计算口径、多语言文档 | 中 `[未验证代码]` |
| [AlexSilka/arbitrage-bot](https://github.com/AlexSilka/arbitrage-bot) | Rust / 1 / — | 5 场所 | delta-neutral 跨所 | 异步执行引擎 + 风控管理器分层 | 中 |
| [yodablocks/hip3-divergence](https://github.com/yodablocks/hip3-divergence) | Python / 0 / 2026-06-16 | HL HIP-3 | Oracle/mark/外部价三价背离**监控** | HIP-3 特有的「三价」口径，可作为上游告警 | 低（只读） |
| [cryptoquantumwave/khunquant](https://github.com/cryptoquantumwave/khunquant) | **Go** / 51 / 2026-11-09 | 多场所 | **AI 组合助手**，非套利引擎 | 无（先前 worker 报告称其为双腿状态机实现，**已证伪**） | 排除 |
| 其余 `*-arbitrage-bot`（扫描器类） | — | — | 只扫描价差、不执行 | 无 | 排除 |

**证伪记录**：`cryptoquantumwave/khunquant` 的 GitHub 描述是 "Your personal AI portfolio assistant at home"，语言 Go，与「双腿执行状态机」无关；`adolyb/entropy-anth-carry` 的描述为 "Delta-neutral funding carry: long io:ANTH (Entropy / Hyperliquid)..."，与我们的策略同族但执行未公开。

---

## 2. `adolyb/entropy-anth-carry` 里可直接用的实测数字

README 原文要点（2026-08-28 版，抓自 `https://api.github.com/repos/adolyb/entropy-anth-carry/readme`）：

- Binance `ANTHROPICUSDT` 空头每 8 小时收 **0.005%** 资金费，**实测 200 个结算窗口从未变过**。
- Entropy（`io:`）多头**几乎不付费**：实测费率乘数 **0.00125**、interest rate 为 0。
- 两腿对冲后年化约 **5.4%**，不吃方向。
- 三条硬约束：`io:ANTH` 有**全市场共享 OI 上限**；Binance 盘口很薄，建仓靠**小 clip 慢慢磨**；**两腿保证金不互通**，各自要独立扛住暴涨暴跌。
- 基差 fade 只做了 **12 笔**回测，作者自己说"deliberately tiny until live data hardens"。

对我们的意义：
1. **carry 与 basis oscillation 是两笔不同的交易**，可以叠加但不能互相替代。若资金费恒定，carry 的期望是可算的、几乎无方向风险；而基差振荡的期望取决于 p95/b\*。
2. **共享 OI 上限**意味着「规模」不是由我们的保证金决定，而是由 dex 的 cap 决定——选票时必须把 `assetToStreamingOiCap` 读进来（`perpDexs` 返回里有）。
3. **保证金不互通** → 两腿要各自留足强平距离，这与 `CAPTAIN_VERDICT.md` 的「两条腿各自的强平距离 ≥18%」一致。

---

## 3. 从这些仓库里能抄的具体机制（按价值排序）

### 3.1 状态机 + 成交跟踪（Hummingbot `ArbitrageExecutor`）
- 每轮 poll 两条腿各自的订单状态，用 `CloseType` 明确「为什么结束」，而不是布尔 `done`。
- 移植落点：`04_preipo_cross_arb/core/wave.py` 的 `RoundState` 需要补一个**终止原因字段**（`completed / failed_leg_a / failed_leg_b / insufficient_balance / timeout`），审计与 PnL 归因都靠它。

### 3.2 漂移检测（Gajesh2007）
- 周期性比对两场所的**实际持仓**，差异超阈值即停。
- 这正是 R6 缺的那一块：我们只在「重启」时对账，没有周期性 drift 检查。落点：独立对账进程，周期 5 s（`CAPTAIN_VERDICT.md §1.7`）。

### 3.3 开仓闸门清单（VOOI）
- 每腿开仓前检查：费率有效、spread 阈值、**per-venue 保证金占用**、settled balance ≥ 1.1× 抵押、并发仓位上限、仓位级止损。
- 我们的现状只有账户级 `EQUITY_STOP`，缺 per-venue 保证金与 per-position 止损。落点：`gates.py`，闸门顺序按「最便宜的先查」排列（本地状态 → 缓存行情 → 远程余额）。

### 3.4 组合回撤熔断（Gajesh2007 / SamEhr44）
- 总回撤超阈值 → 全停。我们已经有了 5%/10% 两级，但尚未接进主循环。

### 3.5 默认只读、`--live` 才下单（entropy-anth-carry / SamEhr44）
- 三个仓库都默认 dry-run。我们应在配置层强制要求两个独立开关（`ARBTWIN_DRY_RUN=0` + `--live`）才允许发单。

---

## 4. 公开仓库里**不存在**的东西（这些必须自己写）

| 缺什么 | 证据 | 后果 |
|---|---|---|
| 跨场所原子双腿 | 无仓库声明做到；DvP 类方案要求同链 | 裸腿窗口必须由状态机 + 时间止损兜住 |
| HIP-3 pre-IPO 的股本校准器 | 唯一同族仓库直接用了固定乘数，未处理 rebase | `ASSUMED_SHARES_B` 必须版本化 + 变更前停新仓 |
| 多腿（>2）账本 | 所有仓库都是双腿 | 选到 4 条腿时必须推广 `target = -Σ(其他腿)/spc` 的向量形式 |
| 成交概率模型接入执行 | 没有任何仓库在挂单前用 fill-prob 做 gate | 见 `FILL_PROBABILITY_MODEL.md` 的最小实验 |

---

## 5. 一句话取舍

**不要把「找一个更健壮的开源项目」当成目标。** 现成的都缺同一块（跨场所腿风险）；能抄的是**件**：
状态机的终止原因、周期漂移检测、per-venue 保证金闸门、默认 dry-run、组合级熔断。
这五件拼到现有 `wave` + `reconcile` 上，比换框架收益大一个量级。