# 成交概率模型：最小可行实验（CPU-only）

源自 `kaggle-data` worker 的扫描（`.herd-dispatch/jobs/kaggle-data/report.md`），此处只保留可执行部分，
并标注哪些是官方事实、哪些是推断。目标不是「训一个模型」，而是**回答一个决策问题**：
*这张 maker 单在 Δt 内成交的概率是多少？低于阈值就该直接 taker 或干脆不下。*

---

## 0. 结论先行

1. **先训 `L1-fill`**：标签 `1{对手方 best 价在 Δt 内穿越我的挂单价}`，只需 L2 + trades，不需要队列位置。
2. **数据不用自建**：Kaggle 有 HL BTC 高频数据集（`marvingozo/hyperliquid-btc-high-frequency-microstructure`，约 60 GB）
   与 HL L2 5 分钟快照（`adamatractor/dex-orderbook-data-5m`，17 MB，够原型）。
3. **特征是现成的**：Optiver Realized Volatility / Trading at the Close 的 imbalance、microprice、depth-weighted price
   与「我的挂单价到 microprice 的距离」组合即可，30~50 维。
4. **评估只看两件事**：**校准曲线**（说 30% 就应成交 30%）与**经济价值**（用模型 gate 后的净收益），不看 AUC。
5. **LightGBM CPU 足够**：100k 行 × 50 特征 0.5~2 s；1M 行 5~20 s。符合 field-guide 的「4 核 60~120 s」约束。

---

## 1. 标签定义（按实现难度排序）

| 标签 | 定义 | 数据要求 | 用途 |
|---|---|---|---|
| **L1-fill** | `1{best_opp 在 [t, t+Δt] 内到达或穿过 my_price}` | L2 + trades | **首选**，近似「我的单会被 touch」 |
| L2-fill | `1{累计对手成交量 ≥ 我前面的队列量}` | L3 或队列推断 | 真实但数据门槛高 |
| mid-touch | `1{mid 穿越 my_price}` | L2 | 比 L1 松，适合做上界 |
| 生存分析 | 成交时间的 hazard | L2 + trades | 输出整条生存曲线，成本最高 |

`Δt` 建议扫 1 s / 5 s / 30 s 三档：**薄腿的成交概率对 Δt 极敏感**，而这个敏感性正是「时间阶梯该多长」的输入。

---

## 2. 特征（按可得性分层）

**L2 直接算（立刻可用）**：`spread_bps`、`book_imbalance_5`、`microprice`、`depth_weighted_price`、
`distance_to_my_price_bps`、`own_side_depth_5`、`opp_side_depth_5`、`level_count_within_25bps`。

**需要 trades 流**：`OFI_Ns`、`trade_intensity`、`aggressive_buy_ratio`、`volume_imbalance`、`last_trade_age_ms`。

**需要 L3**：`queue_ahead_volume`、`cancel_rate`。

**跨场所（本系统特有，优先级最高）**：`basis_bps`、`basis_zscore`、`basis_vol_1h`、`other_venue_spread_bps`、
`other_venue_depth_25bps`。理由：**我们的挂单价值来自基差，不来自单场所微观结构**；
同一个 `book_imbalance` 在基差 5 bps 与 50 bps 时的经济含义完全不同。

---

## 3. 样本构造（含反事实）

```
每 Δt 一个决策点：
  对 p ∈ {best_bid, best_bid−1tick, …, −5tick}：
    X = f(L2(t), trades(t−60s..t), p, 跨场所状态)
    y = 1{p 在 (t, t+Δt] 内被 touch}
```
- 负样本天然占多数（正例率 5%~20%），`scale_pos_weight` 即可。
- **必须做时间序列 CV**（按日/按小时分 fold），禁止随机 split。
- **反事实偏差**：只有「真的挂了单」的样本才有真实成交结果；用全价格网格生成样本是**模拟**，
  会系统性高估（真实世界里我们的挂单会影响别人）。缓解办法：只在 `p` 接近 `best_bid` 的档位训练，并用实盘 fill 日志做外部校验。

---

## 4. 评估

| 指标 | 通过线 | 说明 |
|---|---|---|
| 校准偏差 | < 5 个百分点 | 模型说 30% 的桶，实际 25%~35% |
| Logloss | < 0.5 | 比 AUC 更贴近决策 |
| 经济价值 | gate 后净收益 > 固定规则 | 用同一段行情回放：`gate: p>0.3 才挂` vs `无脑挂` |
| 稳定性 | 分月不翻转 | 至少 3 个时间 fold 同号 |

**失败判据**：校准崩、Logloss>0.7、经济价值无改善 → 停，不要继续调参。

---

## 5. 与执行逻辑的接口

模型输出不是「信号」，是**执行参数**：

```
p_fill = model(state, my_price, Δt)
若 p_fill ≥ p_hi      → 挂 maker（省费）
若 p_lo ≤ p_fill < p_hi → 挂 maker 但设短 deadline（≤ 2 s），到点 IOC 补
若 p_fill < p_lo      → 直接 taker（或放弃本轮）
```
`p_lo / p_hi` 由 `b*` 反解：maker 相对 taker 省下的费用（≈2.5~4 bps）必须大于
`(1 − p_fill) × 裸腿风险 + 追价滑点`。

---

## 6. 数据源清单（可用性已标注）

| 源 | 覆盖 | 访问 | 备注 |
|---|---|---|---|
| HL `/info` `l2Book` / `recentTrades` / `candleSnapshot` | HL 全标的 | 免费、无 key | `l2Book` 的 `n` 字段是**该价位订单数**，可作队列长度代理 |
| Kaggle `marvingozo/hyperliquid-btc-high-frequency-microstructure` | HL BTC | 需 Kaggle 凭据 | 60 GB，字段完整，**唯一现成的 HL 高频集** |
| Kaggle `adamatractor/dex-orderbook-data-5m` | HL L2 | 需凭据 | 17 MB，5 分钟快照，适合跑通流程 |
| Binance `data.binance.vision` | Binance 全量 | 免费 | 日级 ZIP（trades/depth/klines） |
| OKX v5 REST | OKX 全量 | 免费 | **必须带 UA**，否则空 body |
| Lighter `api.lighter.xyz` | Lighter | 免费 | 本机 TLS 失败；需换出口 |
| Tardis | 中心化所 | 付费/1 天样本 | **不覆盖 HL / Lighter** |

---

## 7. 两天实验计划

**Day 1**：拉 HL `l2Book` + `recentTrades` 4~8 小时（或直接用 17 MB 的 Kaggle 快照跑通），
按 §3 构造 `Δt ∈ {1,5,30}s` 三份样本，存 Parquet（1 秒聚合后约 3 MB/天，见 field-guide）。

**Day 2**：LightGBM 三份模型；出校准曲线；用 `p_fill` 做 gate 在同一段数据上回放，
对比「无脑挂 / 固定 deadline / 模型 gate」三种执行规则的净收益（扣半价差与费）。

**成功标准**：gate 规则的净收益比固定规则高 ≥10%，且校准偏差 <5 个百分点。
**不论成败都要产出**：`p_fill(Δt)` 曲线本身就能替换当前的「固定 2 s 超时」这类拍脑袋参数。