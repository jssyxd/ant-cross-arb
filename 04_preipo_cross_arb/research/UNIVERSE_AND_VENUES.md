# 可对冲标的宇宙与场所能力（2026-11-11 实测）

本文件回答一个问题：**同一底层资产，在哪些场所能同时拿到两个可成交、可做空、单位可换算的永续腿。**

证据等级：`[实测]` = 本次抓取到的 API 原始返回；`[文档]` = 官方文档原文；`[推断]` = 由数据推得，未直接验证。
原始数据与复现命令见 `.herd-dispatch/jobs/universe-map/report.md` 与同目录 JSON。

---

## 0. 结论先行

1. **股票永续已经不是「HIP-3 独家」**：Binance USD-M 有 **235 个 `TRADIFI_PERPETUAL`**、OKX 有 **414 个股票类 USDT 永续**、HL 的 `xyz` dex 有 **138 个**、Lighter 有 **13 个股票 + 3 个 pre-IPO**。
   同一只股票常常有 **4 条腿**可挑，选票自由度远大于当前系统假设的两腿。`[实测]`
2. **单位换算几乎全部是 1:1**：OKX 股票永续 `ctVal=1, ctValCcy=<股票代码>`；Binance TradFi `contractSize=None` 且价格与现货一致（推断 1 单位 = 1 股）；HL `xyz` 1 张 = 1 股。
   **唯一例外是 Anthropic**（OKX 按假定 100 亿股计，HL 按 1 股计）——也就是当前系统正在做的那一对。`[实测]` + `[推断]`
3. **`io:ANTH` 不是 `xyz:ANT`**：两者 mark 相差 **17.5 倍**（1715 vs 98）。同名字符串匹配会把 175,000 bps 的假机会当成信号。`[实测]`
4. **流动性分层极其悬殊**：SOXL 在 Binance 日成交 **$5.85B**、在 HL `xyz` **$346M**；而 ASML 在 HL 只有 **$1.07M**、OKX 只有 743 股/日。
   用户点名的 8 个标的里，**只有 SOXL / INTC / KORU / SMSN 级流动性**同时满足「两边都吃得下」。`[实测]`
5. **「同 ticker」不等于「同资产」**：`xyz:STX`（Seagate $593）对 OKX `STX`（Stacks 加密 $0.31）差 −9994 bps；`xyz:CVX` 同类。
   **基差量级本身就是单位校准器**：>500 bps 的「机会」应先怀疑标的错配。`[实测]`
6. **pre-IPO 腿（OPENAI / SPACEX / ANTHROPIC 的 HL vntl 版）目前不可跨场所对冲**：vntl `OPENAI` mark 1336 vs OKX `OPENAI` 125（10.7 倍），两边估值机制各自内部。`[实测]`

---

## 1. 场所清单与能力矩阵

| 场所 | 股票类市场数 | 合约单位 | 最小下单 | 杠杆上限（代表性） | 费率（基准档） | 备注 |
|---|---:|---|---|---|---|---|
| HL `xyz` (HIP-3) | 138 | 1 张 = 1 股 | 由 `szDecimals` 定（ANT=0.01 股） | 10~50x（ANT 10x、NVDA 20x） | maker 1.5 bps / taker 4.5 bps + **deployer share 0~300%** | 单位最干净；`deployerFeeScale` 字段可见但比例需实测 |
| HL 其他 10 个 dex | 0~34 | 各自定义 | — | — | 同上 | `km`/`mkts`/`cash`/`flx` 多数 OI=0、mark 与 oracle 脱节，仅监控 |
| OKX | 414 | `ctVal=1`，1 张 = 1 股 | `lotSz` 0.001~0.1 股 | 10~75x | maker 2 bps / taker 5 bps（Lv1） | 需 `User-Agent` 否则 403；`maxLver` 字段不返回 |
| Binance USD-M | 235 `TRADIFI_PERPETUAL` | 推断 1 单位 = 1 股 | `stepSize` 0.01 | 视标的 | 与 crypto 同档（`[推断]`） | 流动性最好（SOXL $5.85B/日） |
| Lighter | 13 股票 + 3 pre-IPO | `[未验证]` | $10 | 20x（股票）/ 5x（pre-IPO） | 0 maker / 0 taker（标准账户） | `api.lighter.xyz` 在本机 TLS 失败，需换出口 |
| Robinhood | 现货/期权 | — | — | — | — | **无永续、不能裸空**，不构成对冲腿 |
| Uniswap / xStocks / Ondo / Dinari | 代币化股票 | 1:1 背书 | — | 需外部借券 | 池费 + 滑点 | 现货型，做空要先借券 → 摩擦远高于永续配对 |

---

## 2. 目标标的的四方对照（用户点名清单）

| 标的 | HL `xyz` | OKX | Binance | Lighter | 结论 |
|---|---|---|---|---|---|
| **ASML** | $1.07M/日，OI 974 股 | 743 股/日，top ask 0.37 股 | 有（$5.5M，深 0.08/0.45 股） | 无 | 三方有腿但**都薄**；只能小仓 |
| **IREN** | $11.0M，OI 207k | $98.9K（ask 255 股） | $24.9M，深度厚 | 无 | **HL ↔ Binance 最优** |
| **KIOXIA（铠侠）** | $14.6M，OI 101k，funding −0.000446 | $9.99K，深 0.04/0.24 股 | 无 | 无 | 只能在 HL 单腿做市，**不构成双腿** |
| **KORU** | $50.8M，OI 1.24M | $13.8M，深 12/90 | 无 | 无 | **HL ↔ OKX，可做** |
| **SAMSUNG** | **无** | $84.2K，深 8/2 | 无 | 有（无 live 价） | 腿最少；KRW/USD 换算风险 |
| **INTC** | $61.5M，OI 1.68M | $742K | $278M，深度极厚 | 无 | **HL ↔ Binance 最优** |
| **COIN** | $17.3M | $51.4K | $45.8M | $3.4M | Binance 为厚腿 |
| **SOXL** | $346M | $22.1M | **$5.85B** | 无 | **三方齐全，首选** |
| **ANT (Anthropic)** | $116M | $360K | 无 | 有 ANTHROPIC（5x） | 单位口径是唯一风险点 |

额外发现（用户未点名但同族更强）：**NVDA**（HL $486M / Binance $1.34B / Lighter $36.6M / OKX $1.54M）、**MSTR**（HL $18M / Binance $220M）、**GOOGL**、**META**、**MSFT**、**AMD**、**AAPL** 都是四方齐全。

---

## 3. 换算风险（会让「美元中性」静默失效）

| 配对 | 风险源 | 误配后的偏离 | 如何验证 |
|---|---|---:|---|
| xyz:ANT ↔ OKX ANTHROPIC | OKX 按假定 100 亿股计，S-1/A 后强制 rebase | 股本假设 ±10% → ±1000 bps | 等 S-1/A；维护 `ASSUMED_SHARES_B` 版本号 |
| xyz:ANT ↔ io:ANTH | 报价语义不同 | **175,000 bps** | 已证伪，禁止配对 |
| xyz:ANT ↔ para:ANTH | 名称近似，未验证乘数 | 未知（mark 97.6 vs 98.0 接近） | 查 para dex 公告；默认禁止 |
| vntl:OPENAI ↔ OKX OPENAI | 各自内部估值 | **107,000 bps** | 禁止配对 |
| OKX SAMSUNG ↔ Lighter SAMSUNG | KRW/USD + 股本 | ~1300 bps（若一侧报韩元） | 核对报价货币 |
| Binance TradFi（全部） | `contractSize=None`，无显式乘数字段 | 若实为 0.01 股 → 10,000 bps | 小额实测下单 + 对账 PnL |
| 任意「同名不同资产」 | 例如 STX（Seagate vs Stacks）、CVX（Chevron vs Convex） | **~10,000 bps** | 基差量级过滤 + 比值 CV / 收益相关 |

**工程要求**：配对表必须存「单位表达式 + 校准证据 + 校准时间」，不能存布尔 `is_pair`。
任何 `|basis| > 500 bps` 的实时读数应触发 **单位告警**，而不是下单。

---

## 4. 交叉校验：本次 captain 独立抓到的两个反例

1. **OKX 无 UA → 空 body**（不是 403 文本，是空字符串）。见 `.field-guide/herdr_dispatch_and_hip3_ops.md`。
2. **跨 DEX 同 ticker 的 `km`/`mkts` 有报价、无成交**：mark 与 xyz 差 300~5300 bps，但 24h 成交额全 0。
   → 选票第一道闸门必须是「24h 名义额 > 0 且盘口深度 > 0」，而不是「标的在 universe 里」。

---

## 5. 与「即将上线」有关的可操作结论

- Bybit TradFi 每周 1~2 个新标的（FN/DUOL/CEG 于 2026-11 上线），是**新增双腿机会最快的场所**，但本仓库尚未接入。
- HL HIP-3 **没有统一上币公告板**：只能周期轮询 `POST /info {"type":"perpDexs"}` 与各 dex 的 `meta`，把 `universe[].name` 的差集当新标的信号。
- OKX / Binance 有公告但节奏慢；Lighter 的 pre-IPO（OPENAI/SPACEX）目前不可跨场所对冲。

**监控规格**（建议加入 `scripts/`）：
```
每 10 min:  拉 perpDexs → 每个 dex 的 meta.universe 名称集合
            与上一版快照做差集 → 新标的告警（含 dex、szDecimals、maxLeverage）
每 1 h:     对新标的抓 l2Book top5 → 若两侧都有 ±25bps 内 ≥$10k 深度 → 进入候选
```