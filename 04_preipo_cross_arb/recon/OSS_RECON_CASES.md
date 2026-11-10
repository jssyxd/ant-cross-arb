# 跨所双腿套利「对账 / 孤儿腿 / 执行状态机」公开案例核验 + 落地映射

- 抓取日期：2026-11-09（本文所有"状态/日期"均指抓取当日所见）
- 目标引擎：`/opt/nautilus_cluster/ant_arb/ant_cross_arb_v3_ws.py`（本地副本 `04_preipo_cross_arb/core/ant_cross_arb_v3_ws.py`，618 行）
- 标注约定：
  - ✅ **已验证**：我打开了页面并读到原文
  - ◐ **部分验证**：页面可读，但关键结论是我对原文的推断，或只覆盖了用户表述的一部分
  - ✗ **未验证**：找不到可核实来源（明确写"未找到可核实来源"）
  - ❌ **不成立**：原文与用户表述冲突，或该机制在该场所并不存在
- 可达性说明：**`okx.com` 全域从本机不可达**（`curl` 对 `www/app/my/tr.okx.com/docs-v5` 全部 `000` 超时）。凡涉及 OKX 官方文档的结论，我用 CCXT 源码与其 issue、NautilusTrader 文档、第三方实现交叉验证，并逐条标注。凡只有 OKX 官方文档能确认的，标 `[OKX文档不可达]`。
- 线索条数：任务说"9 条"，实际列出 10 条，本文按 10 条核验。

---

## 1. 裁决先行

### 最值得直接照抄的 3 条

| 排名 | 线索 | 一句话理由 |
|---|---|---|
| 1 | **#4 Kestrel / #5 codedrives 的"交易所是事实、本地是意图"** | R6 的 -$4.5496 不是策略错，是把"读不到仓位"当成"仓位为 0"；这条是唯一直接命中的根因级原则，且改造成本最低（一个三态枚举 + halt）。 |
| 2 | **#3 Vike 的 `residual = target − hedge_pos`（目标而非增量）** | 它是"重试不会把仓位做双"的数学保证，而不是靠 if 判断；ANT 的 `HEDGE_RATIO` 本就非 1，残差是常态，必须有账本。 |
| 3 | **#1/#2 Hummingbot 的"撤单失败 ≠ 未成交，已撤订单仍需跟踪成交"** | 11-08 01:32–01:39 的 7 分钟里，正是"订单已终态但成交事件仍会到"的时序；不跟踪已撤订单的成交，任何重试都会漏记一条腿。 |

### 在 ANT 场景下会误导的线索

| 线索 | 误导点 |
|---|---|
| **#9 Paradigm block trade / 原子执行** | 它成立的前提是"两条腿在同一结算层"。HL(`xyz:ANT`,1 股) 与 OKX(`ANTHROPIC-USDT-SWAP`,1 张=1.27 股等价) 没有共同清算所、没有 RFQ/block 通道，"准原子"这个说法会诱使你把"20ms 内双腿"当成原子性——它不是。 |
| **#10 Cboe ADL** | **机制不成立**：Cboe 没有 ADL。ADL 是加密永续交易所机制。真正要防的是 HL 的 ADL/backstop liquidator 与 OKX 的 ADL（`adl` 字段 1–5 级、`positions-history` 的 `type=5`）。方向对、场所错。 |
| **#8 Dan1ro0 的 `Working Price = Fair Value − Inventory Penalty`** | 这是**做市报价倾斜**工具（乘子项 `q·λ·σ²·τ`），它的目的是"少买/少卖"，不是"必须把裸仓平掉"。ANT 是套利不是做市，把它当对冲触发器会让裸仓合法地长期存在——正是 R6 场景。 |
| **#3 Vike 的"永远不要读 broker 持仓"** | Vike 敢这么写，是因为它有完整的 fill stream 作权威、且 broker 读不到 in-flight。**ANT 目前没有任何私有成交流订阅**（已核验：v3 只订阅 HL `l2Book` 与 OKX `bbo-tbt` 公共频道）。照抄等于自废事实来源。 |

---

## 2. 逐条核验

### 2.1 核验总表

| # | 线索 | 结论 | 关键修正 |
|---|---|---|---|
| 1 | Hummingbot #8094 | ✅ 已验证 | 细节准确；场所是 MEXC/KuCoin **现货** XEMM V2，非永续；错误码 `-2011 "Order filled."` |
| 2 | Hummingbot #8468 | ✅ 已验证（**但 PR 仍 open，未合并**） | 用户线索说"实际实现要点"OK；但复审发现该修复本身会引入**双对冲**，说明方案不完整 |
| 3 | Vike xEMM hedge | ✅ 已验证（且比线索更强） | 是 `target = −hedge_qty(maker_pos)`、`residual = target − hedge_pos`；文档还明确禁止用 broker 持仓做权威 |
| 4 | Kestrel Execution Truth Validator | ✅ 已验证 | 原文即 "Local state is intent; exchange state is fact"，含 `-2013 Order does not exist` 日志 |
| 5 | codedrives 执行状态机 | ✅ 已验证 | 五条要点全部逐字命中（persist-first / reconcile-on-startup / clOrdId / partial 是正式状态 / fail closed） |
| 6 | Fincode clientOrderId（OKX clOrdId 只在未完成单内唯一） | ✗ **未找到可核实来源**（"Fincode"这个出处）；底层断言 ◐ 部分验证 | 可核实的只有：≤32 字符、`[a-z0-9-_]`、重复被拒（`51011 Duplicated order ID`）；"成交后复用产生第二笔单"**没有任何官方原文可证** |
| 7 | HN 预测市场双腿套利 | ✅ 已验证（帖 + 1 条评论） | 9 条原则 + `hedge_manager` 与线索一致；但它是**自我推广帖**（3 分、1 评论），无实现可供审计 |
| 8 | X @Dan1ro0 | ✅ 已验证 | 原文是 "Working Price = Fair Value − **Inventory Penalty**"，`Inventory Penalty = q × λ × σ² × τ`；"问题在第一笔成交之后"是第 5 节标题 |
| 9 | Paradigm "Watch Your Legs!" | ◐ 部分验证 | leg risk（滑点/延迟/技术）与 block trade 原子性都在；但原文**没有**"跨所做不到原子只能准原子"这句话，是我方推论 |
| 10 | Cboe ADL / 强平 | ❌ **不成立** | Cboe 无 ADL；Cboe Clear US 走的是保证金/违约管理。ADL 属 Binance/Bybit/Deribit/Delta 类永续场所 |

---

### 2.2 逐条详解

#### 线索 1 — Hummingbot issue #8094 ✅

- 链接：https://github.com/hummingbot/hummingbot/issues/8094 （可访问）
- 标题：`[Bug] XEMM V2 Executor: Cancel/Fill Race Condition causes missing Taker hedge order (Naked Position)`；opened 2026-03-01，**open**。
- 原文要点：
  - 11:50:02.960 因利润率跌破 `min_profitability` 发起撤单；11:50:02.975 订单在 MEXC 已完全成交；11:50:03.024 DELETE 返回 `{"msg":"Order filled.","code":-2011}`。
  - "the xemm_executor seemed to break its state machine. It caught the `BuyOrderCompletedEvent`, but it failed to execute the Taker hedge order on KuCoin… leaving me with an unhedged/naked position."
  - 期望行为："Even if the API cancel request fails because the order is already filled, the executor should … still trigger the Taker hedge order."
- 与 ANT 差异：
  1. XEMM 是**做市**（maker 腿挂单被吃才需要 taker 对冲），ANT 是**主动套利**（两腿都由自己发起）。但"撤单与成交竞态"在 ANT 同样存在：OKX 市价单返回 `code!=0` 时，订单可能已部分/全部成交（`sCode` 与 `code` 都要看）。
  2. XEMM 的对冲腿是**同一策略实例内的顺序动作**；ANT 的两腿跨两个 API、两个签名体系、两套限频，失败面更大。
  3. XEMM 的裸仓有 `min_profitability` 兜底（行情不利就撤）；ANT 的裸仓必须靠显式风控收敛。

#### 线索 2 — Hummingbot PR #8468 ✅（未合并）

- 链接：https://github.com/hummingbot/hummingbot/pull/8468 （可访问，**状态 open**，最后更新 2026-10-16）
- 实际实现要点（原文）：
  - "Track canceled maker orders in `self._canceled_orders`."
  - "Implemented `process_order_canceled_event`."
  - "In `process_order_completed_event`, verified whether completed order corresponds to active, canceling, **or failed** maker order."
  - 根因一句话："`control_update_maker_order` immediately set `self.maker_order = None` upon issuing the cancel request"，于是 `process_order_completed_event` 把成交事件丢了。
- **关键补充（用户线索没提，但价值最高）**：Greptile 复审给出 2 个 P1：「Replacement fill triggers double hedge」与「Late fill drops live retry」。作者在 `aa8c0d410` 里追加：撤单竞态成交时先主动撤掉 replacement maker，并把 `_failed_maker_orders` / `_failed_taker_orders` 拆开。
  → **教训**：用**单个成员变量**（`self.maker_order`）做订单身份，修好一个方向必然在另一个方向坏掉。必须用 `order_id → leg_state` 的映射表。
- 另注：作者在同一天说"感谢合并 PR #8477"，但 **#8477 与本修复无关**（是 dYdX v4 mnemonic 修复）。不要误读成 #8468 已合并。
- 与 ANT 差异：XEMM 的 maker 腿有 `order_id` 可用于回查；ANT 的 OKX 市价单**当前根本没传 `clOrdId`**（已核验：v3 全文无 `clOrdId`），失败后没有任何可用键去回查——照抄"跟踪已撤订单"在 ANT 上缺少身份基础。

#### 线索 3 — Vike xEMM hedge ledger ✅（比线索更强）

- 链接：
  - https://vike.io/docs/trader/market-making/xemm/hedge （The hedge ledger）
  - https://vike.io/docs/trader/market-making/xemm/guards （Guards）
  - https://vike.io/docs/trader/market-making/taker-flatten （Taker flatten）
- 原文要点：
  - "its defining choice is that it is a **target, not a delta**."
  - `target = −hedge_qty(maker_pos, hedge_ratio)`；`residual = target − hedge_pos`；`naked = maker_pos + hedge_pos`。
  - "A retry re-sends `residual`, which a late original fill has already shrunk — so **a retry can never double the position, whatever order the acks arrive in**."（测试名：`a_retry_never_doubles_the_position`）
  - "a broker read answers about **settled** state while this ledger exists for the **in-flight** hedge — no `Broker::position` can see a sent-but-unacked order, and a ledger derived from one would re-fire the full residual and double the position on a late ack."
  - `dust` 用来吞掉低于 taker 最小下单量的残差（否则会被永久重试）；`attempts_exhausted` 需要三件事同时成立：仍有欠账、`attempts >= max_attempts`、最后一次发送超时；触发 `HaltReason::HedgeUnfilled`。默认 `timeout 3s / 3 次`。
  - `XemmStateV1` 持久化 `maker_pos / hedge_pos / hedge_attempts / in-flight 时间戳`；"`load_state` is fail-open and can only make the maker more cautious"；halt 也会持久化，"a halted maker cannot un-halt merely by being restarted"。
  - Guards：`HaltReason` 是**具名**的原因枚举（`ReferenceStale / OwnTouchStale / FeedImpaired / HedgeUnfilled / NakedHardBand / BasisOutOfBand / UnknownFillSymbol`）；halt **latch**（第一个原因保留）；`resume_after_halt_ms` 默认 0 = 只能人工恢复；软带 = `qty`、硬带 = `3×qty`；`taker_flatten` 只切**超出软带的部分**，每次 halt 最多触发一次，且**只在账本无欠账时**触发。
- **重要批判**：三页都写着 "Built, tested, and mounted by nothing"——`XemmMaker` 没有任何二进制挂载，**从未在真实场所报价过**。所以它是"设计约束文献"，不是"实战验证过的实现"。可以照抄语义，不能引用它证明"这样做能赚钱/不会出事"。
- 与 ANT 差异：
  1. Vike 的 `hedge_ratio` 默认 1.0（"the only defensible v1 setting"），残差理论上为 0；ANT 的 `HEDGE_RATIO = 10/12.7 = 0.7874`，残差**永远非零**，且随 S-1/A 股本披露变动。Vike 里"`taker_flatten` 在 ratio=1 时按构造不可达"的逻辑，在 ANT 必须变成常规路径。
  2. Vike 的两腿在同一 `Broker` 抽象下（symbol 区分）；ANT 的两腿是两套 SDK + 两套鉴权 + 两种单位（股 / 张）。
  3. Vike 的 band 单位是 `qty`；ANT 的两所 `qty` 含义不同，必须换成"股等价/美元"。

#### 线索 4 — Kestrel Execution Truth Validator ✅

- 链接：https://dev.to/kestrelquant/execution-truth-validator-defeating-exchange-api-state-drift-and-phantom-orders-11nj （可访问，2026-11-06）
- 原文要点：
  - "The local state is merely a record of *intent*, not a reflection of *fact*."
  - 事故日志：`API error GET /fapi/v1/algoOrder → -2013 Order does not exist.` / `F-EXEC-TRUTH: SL algo … missing/wrong triggerPrice on exchange` / `F-476: SL not placed … - no lock applied`。
  - "If your system assumes that an HTTP `200 OK` (or even a timeout) means the order is definitively placed, you are building on sand."（对应用户说的"API 返回 200 但订单不存在"）
  - 解法是一个**独立于主执行循环**的常驻校验守护进程，交叉 WS 与 REST；发现背离即强制本地状态向交易所收敛。
  - "When the validator finds an open position on the exchange that has no corresponding `OPEN` record in the local database, it flags it as an anomaly. By default, it treats it as a manual intervention, isolates it from the automated strategy, and alerts the operator."
  - `clientOrderId` 用「策略状态+时间戳+意图」的确定性哈希，重试用同一个 id。
- 与 ANT 差异：
  1. 它是**单场所**（Binance USDⓈ-M），"交易所是事实"是单点真理；ANT 是双场所，**没有任何一处能给出 net delta 的真相**，事实只能是两侧快照 + 时间戳的合成。
  2. 它的异常处置是"隔离为手动"，R6 若照搬会把 1.858 张裸空永久隔离 15.5 小时。ANT 需要"隔离 + 强制收敛到 target"（reduce-only），不能只隔离。
  3. 它是自出版博客（作者 kestrelquant / kestrelquant.com），**无开源代码、无仓库**，日志为作者自述。当模式看，不当证据看。

#### 线索 5 — codedrives 执行状态机 ✅

- 链接：https://codedrives.com/blog/execution-engine-state-machine.html （可访问，2026-08-02）
- 五条要点逐字命中：
  - 状态机：`PENDING → SUBMITTED → PARTIAL → FILLED`，出口 `CANCELLED / REJECTED`。
  - "**persist first, act second**. Write the intended transition to durable storage *before* you send anything to the broker."
  - 重启："**reconcile on every startup**: fetch open orders and current positions from the broker … resolve differences in the broker's favour. Always. The broker is the source of truth; your database is a cache of what you last heard."
  - 去重："a **client order ID** … so a retry with the same ID is recognised and ignored by the venue rather than executed twice."
  - 部分成交："the strategy has to be able to express 'partly on' — if it can't, it isn't describing a state the real world can put it in."
  - Fail closed："If reconciliation can't resolve … **do not guess**. Stop trading, cancel what you can, and demand a human."
  - 额外可照抄："A cancel can race with a fill … handle 'cancel rejected — already filled' as a normal Tuesday, not an exception." / "Deduplicate by fill ID" / "Reconnects replay."
- 与 ANT 差异：文章语境是 NSE 指数期权日内（有收盘、有会话），ANT 是 24/7——**没有"收盘对账"这个天然窗口**，对账必须常驻（见 §3.B）。文章建议"重启时以 broker 为准"，但 ANT 的双所 broker 各自只知一半，需要"以两侧快照合成 + 期望账本"两条腿互相校验。

#### 线索 6 — "Fincode clientOrderId（OKX 特例）" ✗

- **未找到可核实来源**：检索 `"fincode" clOrdId / clientOrderId / OKX` 多轮，无任何可打开的对应文章、仓库或文档（唯一"fincode"相关命中是支付服务商 fincode，与交易无关）。**结论：出处不可核实，任何人不应引用它。**
- 可核实的部分（替代来源）：
  - CCXT `okx.py` 对 OKX 下单参数的注释（line 3110）：`# 'clOrdId': clientOrderId, // up to 32 characters, must be unique`；另一处（line 4291）：`# 'clOrdId': 'abcdef12345', // optional, [a-z0-9]{1,32}`。https://github.com/ccxt/ccxt/blob/master/python/ccxt/okx.py
  - CCXT 错误码表含 `'51011': InvalidOrder, # Duplicated order ID`（同一文件 line 830）——说明"重复 clOrdId 会被拒"这一半成立。
  - NautilusTrader issue #3191（已关闭）实测：默认带连字符的 `client_order_id` 被 OKX 拒，报 `Parameter clOrdId error`。https://github.com/nautechsystems/nautilus_trader/issues/3191
  - 第三方汇总表：OKX `clOrdId` 32 字符、字母数字下划线连字符、**rejects duplicate**。https://www.gearprism.com/client-order-ids-crypto-apis-preventing-duplicate-trades
- **"clOrdId 只在未完成订单范围内唯一 / 成交后复用可能产生第二笔单"**：`[OKX文档不可达]`，无官方原文可证。**按未验证处理**。
- 但工程结论不变，且更强：**不要依赖"复用一定会被拒"**。OKX 只在订单仍存在时会拒；HL 的 `cloid` 文档只说"optional 128 bit hex string"，**完全没有承诺去重**（见 §3.F）。所以幂等必须由本地账本 + 按 id 回查确认来保证，而不是指望交易所。
- 与 ANT 差异：ANT 现在**完全没有 clOrdId**，等于放弃了唯一可回查的键。

#### 线索 7 — HN 预测市场双腿套利 ✅

- 链接：https://news.ycombinator.com/item?id=47416315 （可访问；score 3，1 条评论）
- 原文要点（与线索一致）：
  - 风险不是方向而是 "execution imbalance" / "Unpaired Exposure"。
  - 9 条：Limit Unpaired Inventory（"Track unpaired tokens and stop new buys when imbalance exceeds a threshold"）、Time-Based Forced Hedging（"If the opposite side isn't filled within a set time: Force-buy it at a more aggressive price"）、Progressive Hedging、Liquidity-Aware Execution、Time Window Control、Emergency Hedging、Order Escalation（"Passive limit → Retry with better pricing → Aggressive fill"）、Edge Validation、Inventory Skew Control。
  - "The Role of the Hedge Manager：A proper system separates hedging logic … Its goal: Ensure every position becomes a complete pair."
  - 评论（`sharp_runner_84`）："You basically need a **naked position tracker that halts new pairs** on that match until you close the leg."
- 与 ANT 差异（关键）：
  1. 预测市场的未配对风险**有界**：单份最大损失 = 买入价（≤$1），且到期自动结算。ANT 的裸空（OKX short 对上涨）**无上界**，且无到期，只能靠保证金/强平收口。
  2. 预测市场的"对冲"是买**互补合约**（Up+Down），结算价恒为 $1；ANT 的对冲是买**另一个场所的近似标的**，二者价格由基差联系，可能同向偏离（basis widening 是本策略的收益来源，也是风险来源）。
  3. 该帖是自我推广（含 Telegram/X/GitHub 链接），无代码可审计，只有 1 条评论。

#### 线索 8 — X @Dan1ro0 ✅

- 链接（X 长文镜像）：https://threadnavigator.com/thread/2085766554407317704/ （可访问，2026-08-16，约 13 分钟阅读，378 views；原文为 @Dan1ro0 的 X Article）
- 原文要点：
  - 第 5 节标题："**The biggest problem often starts after the first fill**"；场景是 `Up + Down = 94¢`，Up 成交、Down 只部分成交、随后行情走开。
  - 决策清单：等多久、能接受多差的价格、多大不平衡是安全的、继续挂限价还是穿价、是否直接小亏平掉第一腿。
  - 公式："**Working Price = Fair Value − Inventory Penalty**"，"**Inventory Penalty = q × λ × σ² × τ**"（q=当前不平衡，λ=风险敏感度，σ=波动率，τ=剩余时间）。
  - 硬限额清单："maximum capital per market / maximum unhedged inventory / daily loss limit / correlated-position limit / automatic kill switch when market data becomes unreliable"。
- 与 ANT 差异：
  1. 这是**做市的报价偏移**，`τ` 是"到期剩余时间"，ANT 的 τ 没有自然终点（永续），必须换成"持仓时长"或"in-flight 时长"。
  2. `σ²` 项对基差策略要换成**基差波动**（spread σ），不是单一标的 σ。
  3. 该文是对 50+ 个机器人的**二手综述**，无仓库、无成交记录；公式是简化教学版（原文自称 "A simplified structure"）。

#### 线索 9 — Paradigm "Watch Your Legs!" ◐

- 链接：https://paradigm.co/blog/watch-your-legs （可访问，2022-08-27，作者 Kris Machowski）
- 原文要点：
  - "**Leg risk** is therefore the risk that the execution of any of the legs of a multi-leg trade fails to achieve its desired objective." 三类成因：Slippage / Latency / Technology。
  - 限价单的悖论："you could have set a limit sell price of 102 … The result of this however, is that you would end up long 1000 units of spot and short only 200 units of the future, meaning that your spot position is now only partially hedged … full delta risk (of 800)."
  - 技术故障："the exchange itself can go down, or the connectivity between you and the exchange can be interrupted causing only some of the legs to be executed … you may not even *know* if you are exposed."
  - 原子性："**Atomicity** is a property that means that **either all of the legs execute, or none of them do**"；block trade 提供该性质，"It is essentially **offloading the leg risk to a third party**"。
  - 价差报价："This would not be safe to do if you were placing the legs separately due to the risk of getting only filled on one leg."
- **纠正**：原文**没有**"跨所做不到原子性只能准原子"这句话。它说的是"在同一个结算层上的 block/RFQ 可以原子；分腿下单不安全"。用户的表述是合理推论，但不是原文。
- 与 ANT 差异：Paradigm 的原子性来自**同一交易所内的 block trade 结算**；ANT 两腿在两个独立清算体系，**任何"准原子"实现都只是时序压缩，不改变原子性缺失这一事实**。文中的 FTX 已不存在（文章已过期 4 年），Paradigm 现为 Delta-1 spreads / Unified Markets。

#### 线索 10 — "Cboe ADL / 强平" ❌

- **结论：Cboe 没有 ADL 机制**。检索 `site:cboe.com auto-deleveraging` 无任何 ADL 文档；Cboe 侧的相关制度是保证金、清算基金与**违约管理**（Cboe Clear US Default Management Plan, https://cdn.cboe.com/resources/membership/CCUS-Default-Management-Plan.pdf ；Cboe Clear U.S. Risk Management, https://www.cboe.com/solutions/clearing/us/risk-management ）。Cboe 自身还公开质疑过把加密式"fully automated liquidation engine"引入传统清算的做法（https://cdn.cboe.com/resources/government_relations/comment_letters/CFTC_Margin_Final.pdf ）。
- ADL 的真实归属：Binance / Bybit / Deribit / Delta 等永续场所。已核验样例：https://www.bybit.com/en/help-center/article/Auto-Deleveraging-ADL 、https://support.deribit.com/hc/en-us/articles/38374258465437-Auto-Deleveraging-ADL 。
- **对 ANT 有意义的替代事实**：
  - **HL HIP-3 明确存在 ADL**："Each DEX's onchain backstop liquidator is an independent user and falls back to ADL to mathematically guarantee solvency of the DEX."（https://hyperliquid.gitbook.io/hyperliquid-docs/hyperliquid-improvement-proposals-hips/hip-3-builder-deployed-perpetuals.md ）
  - **OKX 有可观测的 ADL 等级**：持仓字段 `adl`（1–5），CCXT 解析里直接保留（`"adl": "3"`）；`GET /account/positions-history` 的 `type` 取值含 `5: adl`、`3: liquidation`、`4: partial liquidation`（CCXT `fetch_positions_history` 文档字符串，已读源码）。
  - Nautilus 记录了一条重要事实：**Hyperliquid 的清算以 `FillReport` 形式到达——有成交、没有用户订单**（"Use `FillReport` for a venue-initiated closure that has a fill but no user-level order. Hyperliquid liquidations follow this pattern."）。这意味着"只看自己的订单流"必然漏掉清算导致的仓位变化。
- 与 ANT 差异：用户担心的"对冲腿在最需要时消失"在 ANT 上**真实存在**，但机制是 HL 隔离池 $39.69 权益被强平/ADL、或 OKX 10x cross 被部分强平；Cboe 不是这个风险的来源。

---

## 3. 新检索方向 A–F

### A. 跨所套利的腿风险（legging risk）工程实践

| 来源 | 可落地结论 |
|---|---|
| Paradigm《Watch Your Legs!》https://paradigm.co/blog/watch-your-legs | 腿风险三源（滑点/延迟/技术），且**限价单会把"价格风险"换成"数量风险"**：宁可接受部分成交，也必须承认"部分成交是正式状态"。 |
| LiquidView《Cross-Exchange Arbitrage on DEX Perpetuals》https://www.liquidview.app/blog/cross-exchange-arbitrage-dex | 原文："**Latency risk — one leg fills before the other, leaving you with directional exposure — is the primary operational risk.**" 并给出净利差恒等式：`Net = 价差 − 腿1费 − 腿2费 − 腿1滑点 − 腿2滑点 − 腿1价差成本 − 腿2价差成本 − gas`；结论"价差 8–15 bps 以上才可靠盈利"。→ ANT 的 `MIN_NET_SPREAD_BPS = 250` 在数量级上远超该门槛，说明真正吃掉 R1–R8 的是**执行与对账**，不是入场阈值。 |
| Vike xEMM 三页（hedge / guards / taker-flatten）https://vike.io/docs/trader/market-making/xemm/hedge | 跨所 maker 的"腿"必须是**账本对象**，不是策略分支；软带/硬带/超时/预算四件套缺一不可。 |

### B. 24/7 加密交易里的 position reconciliation

| 来源 | 可落地结论 |
|---|---|
| NautilusTrader《Execution Reconciliation》https://nautilustrader.io/docs/latest/concepts/execution/reconciliation/ | 本文档给出了本任务最需要的**语义规范**：`An explicit position report is authoritative …`，而 `**Not** evidence of a flat position: A missing report. A null quantity. A venue that does not publish positions.` 且 `Unresolved reports prevent actors and strategies from starting.`（启动即 fail closed）。另有"快照不能撤销快照时点之后的成交"（snapshot freshness）与"mass status 填充量下降要按留存成交处理"两条防误判规则。 |
| TierZero《Order State Machines: Idempotent Fills & Crash Recovery》https://tierzero.dev/blog/trading-bot-order-state-machine-idempotency-crash-recovery | persist-before-side-effect + 每次状态跃迁是 CAS（`UPDATE … WHERE state='SUBMITTED'`）；重启先 `venue.lookup(cloid)` 再决定重发；`resubmit_ttl` 防"过期意图重发"；终态不可回退。 |
| codedrives《From signal to order》https://codedrives.com/blog/execution-engine-state-machine.html | "broker 是事实、你的库是缓存"；重启对账"一律以 broker 为准"；无法解决就停。 |
| Vike《The hedge ledger》 | 状态需持久化 `maker_pos/hedge_pos/attempts/in-flight ts` + halt 原因；恢复只能更保守。 |

**OKX vs HL 持仓端点行为差异（已核验部分）**

| 维度 | OKX `GET /api/v5/account/positions` | HL `POST /info {type: clearinghouseState}` |
|---|---|---|
| 返回值语义 | 只返回**未平仓**仓位（CCXT docstring："fetch all open positions"） | 返回 `assetPositions[]`，需按 `coin` 精确匹配 |
| 平仓表示 | `data: []`；或残留一条 `pos:"0"` 的记录（见 ccxt #26317） | 该 `coin` 不在 `assetPositions` 中，或 `szi:"0.0"` `[未验证具体形态]` |
| 单位 | 张（此处 `ctVal=1`，但通用情形要乘 `ctVal`） | 股（`szi`，带符号） |
| 方向 | `posSide:"net"` 时 `pos` 带符号 | `szi` 带符号（`+` 多 `−` 空） |
| 错误形态 | `code != "0"` 且 `data: []`（例：`51000 Parameter instId error`） | 异常抛出 / HTTP 4xx |
| 分区 | 无 dex 概念；子账户隔离 | **必须传 `dex`**（默认 `""` = 主 dex） |
| 特殊 | 有 `adl`、`mgnMode`、`imr`、`liqPx` | 有 `leverage.type`(isolated/cross)、`liquidationPx`、`marginUsed` |

### C. OKX `positions` 返回空列表但实际有仓：已知原因清单

| # | 原因 | 证据 | 可验证性 |
|---|---|---|---|
| C1 | **请求参数错误却返回空 data**：`instId=[]` → `{"code":"51000","data":[],"msg":"Parameter instId  error"}` | ccxt/ccxt issue #15468 https://github.com/ccxt/ccxt/issues/15468 | ✅ 已验证 |
| C2 | **账户确实为空**（最常见），空数组是"平仓"的正常编码 | ccxt `fetch_positions` docstring "fetch all open positions" | ✅ 已验证 |
| C3 | **返回 `pos:"0"` 的残留记录**，"记录存在"≠"有仓" | ccxt/ccxt issue #26317 https://github.com/ccxt/ccxt/issues/26317 | ✅ 已验证（原始 JSON 含 `'pos': '0', 'posSide': 'long', 'posId': ...`） |
| C4 | **子账户/主账户隔离**：引擎跑子账户「dayuan」；用主账户 key 查只能看到主账户仓位 | `[OKX文档不可达]`；共享事实基线：账户=子账户 dayuan | ✗ 未验证（验证方法：用主 key 与子 key 各调一次 `GET /account/positions`，比对同一 `instId` 结果） |
| C5 | **net_mode vs long_short_mode 语义不同**：net 模式同一 `instId` 一条记录、`pos` 带符号；双向模式有 `posSide=long/short` 两条 | CCXT `parse_position` line 6131 注释 + `hedged = side != 'net'`；ccxt issue #16737 | ◐ 部分验证（官方文档不可达） |
| C6 | **读路径异常被吞**（本仓库缺陷，不是交易所原因）：`get_real_positions` 的 `except` 分支把 `q_hl/q_okx` 留在 0.0 并继续返回 | 已读源码 `ant_cross_arb_v3_ws.py:403-425` | ✅ 已验证（这是 R6 真因） |
| C7 | **`code` 未校验**：只要 `data` 里没匹配到 `instId` 就落到 `q_okx = 0.0` | 同上 | ✅ 已验证 |
| C8 | 限频/HTTP 非 200/网关错误 | `[OKX文档不可达]`；CCXT 对 `50011 RateLimitExceeded` 有映射 | ◐ 部分验证 |
| C9 | 下单后立即查询的读后写延迟 | 无可靠公开来源 | ✗ 未验证（验证方法：demo 环境市价单后以 50ms 间隔连查 20 次，看首次非空延迟分布） |

> **结论**：C1/C3/C4/C5 都是"**数据形态层面的假平仓**"，C6/C7 是"**代码把'未知'折叠成 0**"。R6 是 C6+C7 的组合，交易所端未必有任何异常。这决定了修复重点：**先修代码的语义，再谈交易所怪癖**。

### D. Hyperliquid `clearinghouseState`（含 `dex` 参数）读取语义与坑

| 坑 | 证据（原文） | 影响 |
|---|---|---|
| D1 | `dex` 默认是空串，代表**第一个 perp dex**："Perp dex name. Defaults to the empty string which represents the first perp dex." | 不传 `dex:"xyz"` 就**读不到 `xyz:ANT`**，返回主 dex 的仓位 → 会被误判为 HL 腿为空 |
| D2 | HIP-3 是"**独立保证金、独立订单簿、独立 deployer 参数**"："each perp dex features independent margining, order books, and deployer settings" | HL 腿的可用保证金/强平价只能从该 dex 的 `crossMarginSummary`/`marginSummary` 读，不能看主账户 |
| D3 | HIP-3 meta 里 `xyz:*` 资产带 `onlyIsolated:true / marginMode:"strictIsolated"` | "HL 10x cross"这个说法对 `xyz:ANT` **可能不成立**；实际是隔离池（与 $39.69 权益一致）。需以 `meta(dex=xyz)` 返回为准 |
| D4 | 文档注："Under unified account or portfolio margin, use spot balances endpoint instead for trading account balance across spot and perps." | 若账户是统一账户，`marginSummary.accountValue` 不等于可用于该池的保证金 |
| D5 | 客户端初始化需一致：`Info(..., perp_dexs=["xyz"])`、`Exchange(..., perp_dexs=["xyz"])` | 已核验 v3 两者都传了 `perp_dexs=["xyz"]`（v3:365-366），这点是对的 |
| D6 | 下单 `asset` 是 `meta` 里 `universe` 的索引 | 若 `meta` 拉的是主 dex，`asset` 索引会指向**别的资产**（下错标的）。必须 `meta` 带 `dex:"xyz"` |
| D7 | 仓位对象字段：`szi`（带符号字符串）、`leverage.type`、`liquidationPx`、`marginUsed`、`positionValue` | 现在代码只取 `szi`，丢掉了 `liquidationPx`/`marginUsed`，无法判断"这一腿是否即将消失" |
| D8 | 清算/ADL 会以**没有用户订单的成交**出现 | 只按自己的订单流对账必然漏；需按 `userFills`/`FillReport` 语义处理 |
| D9 | `perpDexs` 可列出所有 dex 及 `deployer` | 可用来**断言** `xyz` 的 deployer 仍是 `0x88806a71…f0888`，防止 dex 被改名/替换 |

来源：https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint/perpetuals.md （clearinghouseState / meta / perpDexs / metaAndAssetCtxs，均含 `dex` 参数）；https://hyperliquid.gitbook.io/hyperliquid-docs/hyperliquid-improvement-proposals-hips/hip-3-builder-deployed-perpetuals.md ；https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/exchange-endpoint.md （`cloid` 为 128-bit hex）。

### E. unpaired inventory 的数值化风控：时间维度 vs 价格维度

**先回答"哪个先触发更好"：都不是单一答案，正确的结构是三档、且触发目的不同。**

| 维度 | 代表来源 | 它该负责什么 | 不该负责什么 |
|---|---|---|---|
| **数量/金额**（`|naked|`） | Vike：软带 `= qty`、硬带 `= 3×qty`；HN："Limit Unpaired Inventory"；Dan1ro0："maximum unhedged inventory" | **守门**：超软带即刻禁止增仓（suppress 增长侧），超硬带即刻 halt + 切超额 | 不该用它决定"何时追价"（否则小残差会永远不动） |
| **时间**（in-flight 时长 + 尝试次数） | Vike：`timeout 3s × max_attempts 3` → `HedgeUnfilled`；HN："Time-Based Forced Hedging"；codedrives："resubmit_ttl" | **驱动重试/升级**：T1 查单、T2 追价、T3 吃单、T4 强制 reduce-only | 不该单独当风险上限（低波动时会过度砍仓，高波动时太慢） |
| **价格**（基差/滑点恶化 bps） | Dan1ro0 的 `q·λ·σ²·τ`；Vike 的 `BasisOutOfBand` | **放弃判据**：价差已反向到"再对冲就是亏更多"，改为直接平腿 | 不该当触发器（基差本就是本策略的收益来源，用价格触发会把自己震出局） |

**建议的取值（针对 ANT 现参数，`BASE_CLIP_HL=0.1` 股、`R=0.7874`、价格约 $98/$140）**

- 换算恒等式（本文推导，仅用共享事实基线的常量）：令 `naked_shares = q_hl + q_okx / R`，则 `naked_shares = −RESIDUAL / R`。即**残差每 0.7874 张 = 1 股等价敞口**。
- `naked_soft` = 1 个 OKX clip = `0.1 × 0.7874 = 0.0787` 张 ≈ **0.1 股 ≈ $10 名义**
- `naked_hard` = `3 × soft = 0.236` 张 ≈ **0.3 股 ≈ $30 名义**
- 时间阶梯：`T1=2s`（按 clOrdId 查单）→ `T2=8s`（追价一次，仍限价）→ `T3=25s`（IOC 吃单，允许 `MAX_SLIPPAGE_BPS`）→ `T4=45s`（reduce-only 强制对冲 + halt）
- σ 缩放：`T_eff = clamp(T × (σ_ref/σ), T/2, 2T)`，`σ` 用基差 5 分钟实现波动率，`σ_ref` 取近 7 日中位数
- **触发优先级（明确顺序）**：
  1. `|naked| ≥ naked_hard` → 立即 halt + `taker_flatten(naked, naked_soft)`（**不等时间**）
  2. `|naked| ≥ naked_soft` → 立即 suppress 增仓侧（**不等时间**）
  3. `residual ≠ 0` 且 in-flight 超 `T1/T2/T3` → 按阶梯升级
  4. `attempts_exhausted` → halt（`HedgeUnfilled`）
  5. `basis` 反向超阈值 → 直接平腿，不再尝试"对冲"
- 按 R6 回放：01:32 时 `naked_shares = 1.90 − 1.858/0.7874 = 1.90 − 2.359 = −0.459`（已超 `naked_hard` 的 0.3），**开仓后 1 个巡检周期内就该 halt**；01:39 误平后 `naked_shares = −2.359`（= hard 的 7.9 倍）。→ 说明真正的失效不是阈值不够严，而是**巡检读到了假平仓**（C6/C7）。

来源：https://vike.io/docs/trader/market-making/xemm/guards ；https://vike.io/docs/trader/market-making/taker-flatten ；https://news.ycombinator.com/item?id=47416315 ；https://threadnavigator.com/thread/2085766554407317704/

### F. 幂等下单 / 去重：多场所差异

| 场所 | 幂等键 | 官方承诺 | 实际可依赖度 |
|---|---|---|---|
| OKX | `clOrdId`（≤32，`[a-z0-9-_]`，重复被拒 `51011`） | `[OKX文档不可达]`；CCXT 注释 "must be unique" | **中**：只在订单仍存在时拒；成交后是否可复用**未验证**。→ 只能当"下单去重"，不能当"永远只成交一次" |
| Hyperliquid | `cloid`，"optional 128 bit hex string"（exchange-endpoint 文档原文） | 文档**未承诺**去重 | **低**：必须自己在重启后 `openOrders` + `userFills` 里按 cloid 回查（TierZero 明确这样写） |
| Solana（若走链上腿） | 交易签名 | 同签名同 blockhash 只落一次；**重建即新签名** | **高但脆弱**（TierZero：重启重建就会双花） |
| Polymarket CLOB | order salt → 同签名同 hash | 重发同 hash = no-op（TierZero） | 中 |
| 通用 fill 去重 | fill id（OKX `tradeId`；HL 成交 `tid`） | — | **必须**：codedrives "Deduplicate by fill ID; never accumulate position by adding up messages you assume are unique" |

**ANT 可落地的键设计**（本文建议）：

```
intent_id = f"{yyyymmddHHMMSS}-{seq:04d}-{leg}"        # leg ∈ {HLO, OKX}
OKX : clOrdId = intent_id[:32]
HL  : cloid   = "0x" + blake2b(intent_id, 16).hexdigest()   # 128-bit hex 合法形态
fill 去重键    : (venue, tradeId)  | fallback (venue, oid, tid)
```

来源：https://github.com/ccxt/ccxt/blob/master/python/ccxt/okx.py ；https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/exchange-endpoint.md ；https://tierzero.dev/blog/trading-bot-order-state-machine-idempotency-crash-recovery ；https://www.gearprism.com/client-order-ids-crypto-apis-preventing-duplicate-trades

---

## 4. 落地映射表（针对 `ant_cross_arb_v3_ws.py`）

> 行号基于本地副本（618 行）。**注意两处与共享事实基线的出入，已核实**：
> 1. `execute_atomic_close` **不在 v3**，它在 `ant_cross_arb_v4_adaptive.py:447`；v3 只有 `execute_atomic_trade`（v3:427）。
> 2. v3 **完全没有持久化**（全文无 `json.dump`/`open(`/sqlite）、**没有 clOrdId**、**没有订单状态查询**（已 grep 确认）。这意味着第 1–4 行是"新增"，不是"改参数"。

| # | 缺陷 | 采用方案（来源） | 改哪里 | 验收方式 |
|---|---|---|---|---|
| 1 | `get_real_positions()` 把"读不到"折叠成 `q=0`（v3:403-425 的 `except` + 缺 `code` 校验） | Nautilus 三态语义：**有仓 / 明确空仓 / 未知**；"missing report is not evidence of flat" | `AtomicDualLegEngineV3.get_real_positions` → 返回 `PositionsSnapshot(ok: bool, reason: str, ts: float, q_hl, q_okx)`；OKX 分支必须校验 `code == "0"`；HL 分支必须 `dex="xyz"` 且命中 `xyz:ANT` | 单测注入 `{"code":"51000","data":[]}`、`{"code":"0","data":[]}`、抛异常三种返回：前两者分别为 `ok=False/ok=True(flat)`，`ok=False` 时主循环**不得**发出任何订单 |
| 2 | 只有 `net_delta` 阈值，没有残差账本（v3:420-424） | Vike `target/residual/naked` 三式；`naked_shares = −RESIDUAL/R` | 新增 `HedgeLedger`（`apply_fill(leg, sz, px, trade_id)` / `snapshot()`），落盘 `ledger.json` + `fsync`，写入在发单**之前** | 回放 R5→R6 事件序列：01:32 后账本 `residual=+1.858`（不是 0）；01:39 日志必须出现 `UNRESOLVED_RESIDUAL` 而非"已归零" |
| 3 | `execute_atomic_trade` 先 HL 后 OKX，OKX 失败即 `_emergency_flatten_hl` 单腿市价回滚（v3:427-484） | codedrives/Kestrel：先持久化意图再发单；腿失败进入正式状态而非就地猜测 | `execute_atomic_trade`：① 生成 `intent_id` 并写 ledger（`PENDING`）② HL 下单带 `cloid` ③ 解析 fill ④ OKX 下单带 `clOrdId` ⑤ 失败 → 状态置 `PENDING_HEDGE`，交给 `drive_hedge`，**不立刻市价回滚** | 模拟 OKX 返回 `code:"1"`：断言 ledger 停在 `PENDING_HEDGE`、`_emergency_flatten_hl` 未被调用；下一 tick `drive_hedge` 按 residual 重发 |
| 4 | `run()` 直接进主循环，无启动对账（v3:517-528） | codedrives："reconcile on every startup … always"；Nautilus："unresolved reports prevent strategies from starting" | `run()` 第一步（`align_account_leverage()` 之后、`ws_mgr.start()` 之前）加 `reconcile_before_trading()`：读 ledger → 拉双所快照 → 算 residual → `|residual| > naked_hard` 则 halt 等人；否则由 `drive_hedge` 收敛 | 断点测试：ledger 里 `q_okx=-1.858, q_hl=0`，启动后**只**下 OKX 买入 1.858，HL 侧零动作 |
| 5 | 订单无身份，失败后无法回查（v3 全文无 `clOrdId`） | OKX `clOrdId` / HL `cloid`（§3.F） | `execute_atomic_trade` 下单参数补 `clOrdId`/`cloid`；新增 `OMS` 状态机 `PENDING→SUBMITTED→ACKED→PARTIAL→FILLED/CANCELLED/REJECTED/UNKNOWN` + `query_by_client_id(venue, id)` | 回放 #8094 时序（撤单返回"已成交"同时收到 fill）：断言 hedge 只发**一次**，且 `order_id→leg` 映射里两条腿都在 |
| 6 | 无未配对仓位上限，`net_delta` 只在日志里打印（v3:543-544，`health` 仅字符串） | Vike 软/硬带 + HN "halt new pairs" | 新增 `naked_band_gate(snapshot, ledger) -> (suppress_open: bool, halt: bool)`；在 v3:584 门禁前调用 | 造 `residual=+2`：断言不再开新仓、日志 `NAKED_HARD_BAND`、`drive_hedge` 只切"超出软带的部分" |
| 7 | v3 **没有任何退出侧风控**：`INSTANT_DRIFT_BPS=8.5` 只在 `EngineConfig`（v3:82）定义，**全文未被引用**；16% 单腿止损与 S=15.20 熔断都在 v4（`ant_cross_arb_v4_adaptive.py:84,120`） | Vike `HedgeUnfilled`（欠账 + 预算耗尽 + 超时）；HN 时间强制对冲 | 新增 `drive_hedge(now)`：`T1/T2/T3/T4` 阶梯 + `attempts` 计数 + `halt_reason`；并把 `INSTANT_DRIFT_BPS` 真正接进门禁 | 模拟 OKX 连续 3 次拒单：断言 `T4` 后 halt，日志带具名原因 `HEDGE_UNFILLED`，且 halt 状态写盘（重启不复位） |
| 8 | 保证金预检只看 OKX `availBal`（v3:390-401），不看 HL 隔离池 | D2/D4：HIP-3 独立保证金；两边取小 | `check_okx_margin_availability` → `check_margin_availability`：同时读 `clearinghouseState(dex=xyz).withdrawable` 与 `marginSummary`，`required_margin = max(两所各自要求)`，取可用较小者 | 设 HL 权益 $39.69、请求 $60 名义：断言拒绝开仓并在日志给出两侧可用余量 |
| 9 | HL 配额超限时 `sleep(60)` 阻塞在对冲路径上（v3:456-459） | Vike"对冲优先于重新报价"；codedrives"腿失败要保持状态" | `execute_atomic_trade`：配额检查前移到发单前（`query_quota_status` 已有）；`sleep` 期间若 `residual≠0` 必须继续 `drive_hedge`（改为不阻塞的状态机 tick） | 模拟配额错误：断言主循环不阻塞 >1s，且 `drive_hedge` 仍按期执行 |
| 10 | 无法区分"外部干预"（ADL/强平/手动平）与"自己成交" | Kestrel "unrecorded position → 标异常"；Nautilus `EXTERNAL` + `FillReport`（HL 清算无订单） | `reconcile_before_trading` 与每 30s 巡检（v3:543）：快照与 ledger 差异若无对应 fill → 标 `EXTERNAL_DRIFT`，**隔离该腿但强制收敛到 target**（reduce-only），并告警 | 手动在 OKX 平 0.1 张：下一巡检日志 `EXTERNAL_DRIFT okx=-0.1`，且随后自动用 reduce-only 回到 target |

---

## 5. 反面清单：在 ANT 上**不能**照搬的地方

1. **Vike 的"永不读 broker 持仓"不能照搬（最危险）**
   理由：Vike 有 fill stream 作唯一权威，且其 broker 读不到 in-flight 单。**ANT 完全没有私有成交流**（已核验：v3 只订阅 HL `l2Book` 与 OKX 公共 `bbo-tbt`）。照抄会让我们既没有事实源也没有意图源。
   正确做法：**先用持仓快照建立事实基线**（带 `ok/ts` 三态），**再**用账本表达意图；两者冲突 → fail closed。等私有 WS 订单频道上线后，再把权威迁移到 fill stream。

2. **Paradigm 的 block trade 原子性无法获得**
   理由：原子性来自"同一结算层的 RFQ/block"。HL 与 OKX 无共同清算所、无 block 通道，且两腿标的不是同一工具（1 股 vs 1.27 股等价）。把两笔 IOC 压到 20ms 内**不是原子性**，只是把窗口缩小；一旦跨所网络分区，窗口内仍可能只有一腿成交。

3. **`hedge_ratio = 1.0` 的假设不成立**
   理由：ANT 的 `HEDGE_RATIO = 0.7874` 来自 `ASSUMED_SHARES_B = 12.7` 的**假设**，S-1/A 披露实际股本后必须改。Vike 里"残差恒为 0、所以 flatten 腿不可达"的推理，在 ANT 上反转为"残差恒非 0、flatten 是常规路径"。

4. **Vike 的软/硬带不能直接用 `qty`**
   理由：HL 单位是"股"，OKX 单位是"张"，且存在 rebase 风险（OKX 侧 1 张 = 1/10B 估值，S-1/A 后强制 rebase）。band 必须以"**股等价**"或"**美元名义**"表达：`naked_shares = q_hl + q_okx/R`。

5. **HN / Dan1ro0 的"未配对库存"直觉不能跨品类平移**
   理由：预测市场单份最大损失 = 买入价（≤$1）、到期强制结算；ANT 的裸空对上涨**无上界**、无到期、只能靠保证金。同样的 bps 阈值放到 ANT 上会宽 1–2 个数量级（R6 裸持 15.5 小时即为例证）。

6. **Kestrel 的"未记录仓位 = 手动，隔离不管"不能全盘照搬**
   理由：R6 的 1.858 张若被"隔离且不管理"，就是 15.5 小时裸空。ANT 必须"隔离 + reduce-only 收敛到 target"，且收敛动作本身要有时间预算。

7. **codedrives 的"broker 是唯一事实"在双所场景要弱化**
   理由：每个所只知道自己那半；没有任何单一端点能给出 net delta。事实 = 两侧快照的**带时间戳合成**，且必须显式处理"一侧未知"。

8. **Hummingbot #8468 的"单变量跟踪（`self.maker_order`→`_canceled_orders`）"不够**
   理由：该 PR 自己的复审发现修完一个方向会在另一个方向产生"双对冲"。ANT 必须用 `intent_id → leg_state` 映射表 + `cloid/clOrdId` 回查，不能用成员变量表达订单身份。

9. **Dan1ro0 的 inventory penalty 不能当"对冲触发器"**
   理由：`q·λ·σ²·τ` 是**报价倾斜**（少买/少卖），它允许不平衡长期存在。ANT 需要的是"不平衡超限即强制收敛"，两者是不同机制。

10. **不要假定"同一 clientOrderId 重发一定会被交易所拒绝"**
    理由：OKX 只在订单仍存在时拒（`51011`），成交后复用行为**未验证**；HL 的 `cloid` 文档只说"可选 128-bit hex"，**没有去重承诺**。幂等必须落在"本地账本 + 按 id 回查 + fill id 去重"，交易所只是第二道防线。

11. **不要把"ADL/强平"当成 Cboe 类机制来建模**
    理由：Cboe 无 ADL。真正要建模的是：HL HIP-3 的 ADL + backstop liquidator（且清算以**无订单的成交**出现）、OKX 的 `adl` 等级 1–5 与 `positions-history type∈{3,4,5}`。

12. **不要在 `xyz:ANT` 上假定"cross"**
    理由：HIP-3 `meta` 样例显示 `xyz:*` 资产带 `onlyIsolated:true / marginMode:"strictIsolated"`；HL 腿实际是隔离池（权益 $39.69）。"双边 10x cross 对称"只对 OKX 成立，"保证金占用对称"这个前提需要对 HL 重新核实。

---

## 6. 参考链接汇总（去重）

| # | 链接 | 可访问性 | 用途 |
|---|---|---|---|
| 1 | https://github.com/hummingbot/hummingbot/issues/8094 | ✅ | 撤单/成交竞态导致裸仓（MEXC/KuCoin 现货 XEMM V2） |
| 2 | https://github.com/hummingbot/hummingbot/pull/8468 | ✅（open，未合并） | `_canceled_orders` + `process_order_canceled_event` + 复审发现的 P1 双对冲 |
| 3 | https://github.com/hummingbot/hummingbot/pull/8477 | ✅ | **无关**（dYdX v4 mnemonic），用于纠正"已合并"的误读 |
| 4 | https://vike.io/docs/trader/market-making/xemm/hedge | ✅ | target/residual/naked、retry 不翻倍、broker 读不到 in-flight、持久化 |
| 5 | https://vike.io/docs/trader/market-making/xemm/guards | ✅ | 具名 halt 枚举、latch、软/硬带、freshness 2s/2s/5s、人工恢复 |
| 6 | https://vike.io/docs/trader/market-making/taker-flatten | ✅ | 只切超额、每次 halt 一次、账本无欠账才触发 |
| 7 | https://dev.to/kestrelquant/execution-truth-validator-defeating-exchange-api-state-drift-and-phantom-orders-11nj | ✅ | 本地=意图、交易所=事实；`-2013 Order does not exist`；未记录仓位隔离 |
| 8 | https://codedrives.com/blog/execution-engine-state-machine.html | ✅ | 五态机、persist-first、重启对账、clOrdId、partial、fail closed |
| 9 | https://codedrives.com/blog/risk-limits-kill-switch.html | ✅（仅索引） | 风控/熔断的配套文（未逐字核验） |
| 10 | https://github.com/ccxt/ccxt/blob/master/python/ccxt/okx.py | ✅ | OKX `clOrdId` ≤32 字符/唯一、`51011 Duplicated order ID`、`pos` 语义注释、`fetch_positions` |
| 11 | https://github.com/ccxt/ccxt/issues/15468 | ✅ | `instId=[]` → `code 51000` + **空 data** |
| 12 | https://github.com/ccxt/ccxt/issues/26317 | ✅ | 返回 `pos:"0"` 的残留记录（"存在"≠"有仓"） |
| 13 | https://github.com/ccxt/ccxt/issues/16737 | ✅ | OKX 双向（long/short）模式下的持仓解析问题 |
| 14 | https://github.com/nautechsystems/nautilus_trader/issues/3191 | ✅ | OKX clOrdId 参数格式实测拒单 |
| 15 | https://nautilustrader.io/docs/latest/concepts/execution/reconciliation/ | ✅ | **missing report ≠ flat**；启动 fail closed；snapshot freshness；`FillReport`（HL 清算无订单） |
| 16 | https://nautilustrader.io/docs/nightly/integrations/okx | ⚠️ 地理位置拦截 | OKX client order id 要求（本机被重定向到 region-unavailable） |
| 17 | https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint/perpetuals.md | ✅ | `clearinghouseState`（含 `dex`）、`meta`、`perpDexs`、`marginTables` |
| 18 | https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/exchange-endpoint.md | ✅ | 下单体、`cloid`（128-bit hex）、`expiresAfter`、`vaultAddress` |
| 19 | https://hyperliquid.gitbook.io/hyperliquid-docs/hyperliquid-improvement-proposals-hips/hip-3-builder-deployed-perpetuals.md | ✅ | 每个 dex 独立保证金/订单簿、ADL 与 backstop liquidator、cross margin 不可逆 |
| 20 | https://paradigm.co/blog/watch-your-legs | ✅ | 腿风险三源、限价单的量价互换、block trade 原子性、"offloading the leg risk" |
| 21 | https://news.ycombinator.com/item?id=47416315 | ✅ | 未配对库存/时间强制对冲/渐进对冲/hedge_manager/订单升级 |
| 22 | https://threadnavigator.com/thread/2085766554407317704/ | ✅（X 镜像） | "问题从第一笔成交后开始"、`Inventory Penalty = q·λ·σ²·τ` |
| 23 | https://www.liquidview.app/blog/cross-exchange-arbitrage-dex | ✅ | 延迟即首要操作风险、单腿成交、净利差恒等式 |
| 24 | https://tierzero.dev/blog/trading-bot-order-state-machine-idempotency-crash-recovery | ✅ | persist-before-effect、CAS 跃迁、cloid 重启回查、Solana 签名去重的边界 |
| 25 | https://www.gearprism.com/client-order-ids-crypto-apis-preventing-duplicate-trades | ✅ | 各所 clientOrderId 参数对照（OKX 32 字符/rejects duplicate） |
| 26 | https://www.cboe.com/solutions/clearing/us/risk-management | ✅ | Cboe Clear US 风控（**无 ADL**） |
| 27 | https://cdn.cboe.com/resources/membership/CCUS-Default-Management-Plan.pdf | ✅ | Cboe 的违约管理路径（对照 ADL 的缺席） |
| 28 | https://www.bybit.com/en/help-center/article/Auto-Deleveraging-ADL | ✅ | ADL 的真实归属（加密永续） |
| 29 | https://support.deribit.com/hc/en-us/articles/38374258465437-Auto-Deleveraging-ADL | ✅ | 同上（另一场所） |
| 30 | https://www.okx.com/docs-v5/en/ · https://app.okx.com/docs-v5/ · https://my.okx.com/docs-v5/ · https://tr.okx.com/docs-v5/en/ | ❌ **本机全部不可达（HTTP 000 超时）** | 所有 OKX 官方原文结论均标 `[OKX文档不可达]` |
| — | "Fincode" 的 OKX `clOrdId` 文章 | ❌ **未找到可核实来源** | 禁止引用 |

---

## 附：与共享事实基线的三处勘误（供其它 worker 引用时修正）

1. `execute_atomic_close` 不存在于 `ant_cross_arb_v3_ws.py`；它在 `ant_cross_arb_v4_adaptive.py:447`。v3 的对应函数是 `execute_atomic_trade`（v3:427），其失败路径是 `_emergency_flatten_hl`（v3:502），**不是**"先平 HL 再平 OKX"。
2. v3 的 `net_delta = q_hl * R + q_okx`（v3:424），与基线里的 `RESIDUAL = TARGET_OKX − q_OKX` **符号相反**（`net_delta = −RESIDUAL`）。两者绝对值相同，但改代码时若混用会引入双重取反。建议统一为 `RESIDUAL`，并显式写出 `naked_shares = −RESIDUAL / R`。
3. 基线 §7 说"已有风控：… 8.5bps 瞬时漂移熔断"。**实测 v3 并未启用**：`INSTANT_DRIFT_BPS`（v3:82）在全文只被定义、从未被读取；`run()` 里真正生效的门只有 `net_spread_bps >= 250`（v3:584）与双所 2.0s 行情陈旧拦截（v3:560-568）。16% 单腿止损（`v4:84`）与 S=15.20 熔断（`v4:120`）都只存在于 v4。→ "裸持 15.5 小时无人拦"在 v3 上不是"阈值太宽"，而是**没有退出侧逻辑**。