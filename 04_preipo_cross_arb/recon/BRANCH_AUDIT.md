# 分支健康比对：`main`（做市线） vs `tree-twin`（两腿对冲线）

> 2026-11-09 · 依据：`git ls-tree` / `git show` 实测，逐文件扫描 import 与关键词。
> 结论均可复算，命令附在每节末尾。

---

## 0. 结论先行

| | `main` | `tree-twin` |
|---|---|---|
| 定位 | 单账户做市（GLFT + LightGBM 门控 + VPIN/OFI 防毒 + 8.5bps 防插针 + 配额看门狗） | 两腿 delta 对冲（HL 多 / OKX 空，赚基差收敛） |
| 提交 | `917780e`（原 `78d3f4d` + 去掉 4 个 .pyc） | `cc478bb` |
| 文件数 | **72** | **30** |
| 含套利代码 | **0** ✓ | 25 个（全在 `04_preipo_cross_arb/` + 3 个 script + 1 个 shared） |
| 含做市代码 | 67 ✓ | **0** ✓（已清空） |
| 共有文件 | 5 个：两个对话归档 + `CHANGELOG.md` + `README.md` + `.gitignore` | |

**分离度已达成：两边的专有代码零重叠。** 剩余 5 个共有文件里，只有 `README.md`/`CHANGELOG.md` 需要各自适配（见 §3）。

---

## 1. 本次执行的剔除（`tree-twin`，96 files / −12,820 行）

### 1.1 删除（做市专用，`main` 上仍在）

| 路径 | 性质 | 为什么安全 |
|---|---|---|
| `01_crypto_altcoin_perp_mm/` | VPIN 门控 | 0 引用 |
| `02_rwa_tokenized_equity_mm/`（8 个文件） | GLFT 实盘做市 + LightGBM 毒性门控 + regime + 配额看门狗 | 0 引用 |
| `03_dex_concentrated_lp_mm/` | LP 对冲 | 0 引用 |
| `shared/glft_engine/`、`shared/toxic_gate/`、`shared/sentinel/`、`shared/aggregator/`、`shared/data_lake_client/` | 做市四大组件 | 0 引用 |
| `experiments/`（49 个产物） | 做市矩阵回测结果 | 与 arb 无关 |
| `audit_reports/`（3 份） | 做市审计报告 | 与 arb 无关 |
| `kaggle_pipeline/`、`tests/test_regime_sentinel.py`、`tests/test_kaggle_bridge.py` | ML 训练与测试 | 与 arb 无关 |
| `04_preipo_cross_arb/features/` | **VPIN/OFI 防毒单特征** | v4 活引擎 0 引用（`vpin`/`toxicity` 命中 0） |
| `04_preipo_cross_arb/models/` | **LightGBM 门控**（`ant_gating_lgb.txt` + 训练脚本） | 同上；且 AUC=1.0 是合成标签同义反复 |
| `04_preipo_cross_arb/workflow/` | 重训流水线 | 同上 |
| 3 个数据摄取脚本 | CryptoHFTData 入湖 | arb 不用 |
| **11 个 `.pyc`** | 编译产物误提交 | 已加 `.gitignore` |

### 1.2 归档而非删除（保留审计可追溯）

`core/{ant_cross_arb_live,ant_cross_arb_v2,ant_cross_arb_v3_ws}.py` → `core/legacy/`，附 `README.md` 说明：
- `_live.py` 的 `execute_live_clip()` **只打印日志 `return True`**，从不下单
- `_v2/_v3_ws` 的 `INSTANT_DRIFT_BPS` **定义但从未引用**（死代码）
- `_v3_ws` 无持久化、无 `clOrdId`、无私有成交流

### 1.3 参数统一（消除 D7）

`core/ant_arb_math.py`：

| 项 | 原值 | 新值 | 依据 |
|---|---|---|---|
| `min_net_arb_spread_bps` | 15.0（与 25/250/750 并存） | **750.0** | 与 v4 `ENTRY_SPREAD_BPS=750` 对齐 |
| `max_slippage_bps` | 5.0 | **2.33** | Maker/Taker 费差盈亏平衡点（实测） |
| `instant_drift_breaker_bps` | 8.5 | **删** | 做市防插针语义，arb 不适用 |
| `vpin_toxic_threshold` | 0.65 | **删** | 做市防毒单语义 |

---

## 2. 剔除前后的等价性验证（用户要求"先测再剔"）

| 检查 | 结果 |
|---|---|
| 全部 `.py` AST 解析 | **0 失败** |
| 活引擎 `ant_cross_arb_v4_adaptive.py` 是否被改动 | **未改动**（git blob hash `2745fbd4b43c` 前后一致） |
| 对账核心 `recon/recon_ledger.py` 是否被改动 | **未改动**（blob `7752250b8772`） |
| 对账回归测试 43 条断言 | **ALL TESTS PASSED** |
| 剔除项是否有活代码引用 | **0 处**（全量 import 扫描：`glft_solver`/`toxic_gate`/`sentinel`/`aggregator` 均 0；`lightgbm` 仅在被删的 `train_ant_model.py`） |
| `.pyc` 残留 | **0** |
| 纯净度守卫 | `BRANCH PURITY OK`（30 tracked，MM-only 命中 0） |

> **为什么可以断定"不影响实盘"**：`ant_cross_arb_v4_adaptive.py` 的 import 列表里只有
> `gateway_okx_client`、`websockets`、`hyperliquid.*`、`eth_account`、stdlib ——
> 没有一个是本次删掉的模块。且它的 blob hash 在剔除前后完全相同（未被触碰）。

---

## 3. 各分支仍存在的问题（不掩盖）

### 3.1 `main`（做市线）

| 严重度 | 问题 | 证据 |
|---|---|---|
| 中 | **仓库与 VPS 部署不一致**：VPS 上 `/opt/nautilus_cluster/shared/` 有 `okx_ws_manager.py`、`robust_glft_engine.py`、`depth_sentinel.py` 三个模块，**仓库里没有** | 仓库只读扫描：main 的 `.py` 无一处 import 它们，但 VPS 上 `run_chz_v2.py` 等确实 import |
| 低 | **没有 `requirements.txt`** | `ls-tree` 无命中 |
| 低 | `README.md` 仍称"系统三大业务分支"，未提及 `tree-twin` 是独立策略线 | README 首段 |
| 低 | GLFT 公式量纲缺陷（D8，缺 √，量纲 price² 而非 price）**未修** | `shared/glft_engine/glft_solver.py` |
| 低 | `CHANGELOG.md` 最后一条是配额看门狗，未记录"移除 4 个 .pyc" | 本次提交 |

### 3.2 `tree-twin`（两腿对冲线）

| 严重度 | 问题 | 证据 |
|---|---|---|
| **高** | **`recon_ledger.py` 没有接进活引擎**：v4 里 `HedgeLedger` 0 命中、`residual` 0 命中 → 我做的对账核心**当前是死代码**，v4 用的是自己的 `reconcile_orphan_position` | `grep` v4: `recon` 2 次（仅注释）、`HedgeLedger` 0、`residual` 0 |
| **高** | **两套孤儿腿逻辑并存**（v4 的 `reconcile_orphan_position` + recon 的 `recon_ledger`），没有仲裁谁是权威 | 同上 |
| 中 | v4 **没有私有成交流订阅**（只订 `l2Book` + `bbo-tbt`）→ 无法做 fill-implied 持仓校验 | v4 `userFills` 0 命中 |
| 中 | `CHANGELOG.md` 继承自 main，**全是做市条目**，arb 的变更没有记录 | 文件内容 |
| 中 | `README.md` 已加 arb 横幅，但**下半部分仍是做市说明** | 文件内容 |
| 低 | `deploy/ant-arb.service` 的 `ExecStart` 原指向 `ant_cross_arb_service.py`（仓库里不存在） | 本次已修为 `ant_cross_arb_v4_adaptive.py` |
| 低 | `scripts/smoke_test_v4.py` / `smoke_test_ws_engine.py` 需要真实网络与密钥，无法在 CI 跑 | 文件内容 |
| 低 | 没有 `pytest` 用例覆盖 v4 的决策函数（`plan_dual_leg_position`、`resolve_hedge_ratio`、`compute_implied_s`） | 只有 recon 有测试 |

### 3.3 分支内部冗余（两分支共性）

| 冗余 | 位置 | 建议 |
|---|---|---|
| 四代引擎并存 | tree-twin `core/` + `core/legacy/` | 已用 `legacy/` 明确"勿部署"，足够 |
| 两份 `SHARED_FACTS.md` 类文档 | `recon/SHARED_FACTS.md`（arb） vs 无（main） | 无冲突 |
| 3 份对话归档 | 两个分支都有 2~3 份 HTML | 可接受（用户要求全量归档） |
| `recon/` 下 4 份 md 有内容重叠 | `CAPTAIN_VERDICT` / `CROSS_CHECK` / `OSS_RECON_CASES` / `PROPOSED_SCHEME_PNL_ANALYSIS` | 分工明确（裁决/交叉/外部核验/量化），保留 |

---

## 4. Merge 复活风险与对策（**已按红队实测更正**）

> **上一版本文写错了。** 原文称"main 上仍存在、tree-twin 已删除的文件会被 merge 重新加入且不报冲突"。
> 红队在临时克隆里做了实际 merge 实验，结论相反。以下为实测结果。

| 场景 | git 行为 | 是否污染 tree-twin |
|---|---|---|
| main **未修改**该文件，tree-twin 已删除 | 删除**正确传播**，无冲突，文件**不会**回来 | **否** |
| main **修改过**该文件 | `modify/delete` **冲突**；冲突后 git 把文件**留在工作区**，一个 `git add -A` 就把它复活 | **会（需人工处理）** |
| main **新增**了文件（tree-twin 从未有） | **静默入库、无冲突** | **会（无提示）** |

**所以真正的风险是两个**：`modify/delete` 冲突后误 `git add -A`，以及 main 新增文件静默流入。

**对策对比（修正后）**：

| 对策 | 有效性 | 采纳 |
|---|---|---|
| `.gitattributes` 的 `-merge` / `merge=ours` | **无效**：对 `modify/delete` 完全不生效（实测仍 `DU`），且 `merge=ours` 还要每个 clone 自己配 driver（不可提交） | ✗ |
| 只靠人工纪律 | 低 | ✗ |
| **可执行守卫 `check_branch_purity.py`（读索引）** | **有效**：覆盖"冲突后 add -A"与"新增文件静默流入"两种真实路径，命中即退出码 1 | **✓ 已实施** |

**另一条更重要的更正**：把剔除提交**反向并进 `main`** 是真正的炸弹 —— 实测会让 67 个 MM 文件从 main 消失、`01_/02_/03_` 目录在磁盘上直接不见，而只报 1 个 `.gitignore` 冲突。
**删除是双向生效的指令。** 因此纪律不只是"别把 main 并进 tree-twin"，更是**"永远不要把 tree-twin 的删除提交 cherry-pick 或 merge 回 main"**。

## 5. 复算命令

```bash
git -C <repo> ls-tree -r --name-only main        | wc -l    # 72
git -C <repo> ls-tree -r --name-only tree-twin   | wc -l    # 30
comm -12 <(git ls-tree -r --name-only main|sort) <(git ls-tree -r --name-only tree-twin|sort)   # 5 个共有
python 04_preipo_cross_arb/check_branch_purity.py            # BRANCH PURITY OK
cd 04_preipo_cross_arb/recon && python test_recon_ledger.py   # ALL TESTS PASSED
git -C <repo> cat-file -p tree-twin:04_preipo_cross_arb/core/ant_cross_arb_v4_adaptive.py | head -60   # 只有 4 个第三方依赖
```

---

## 6. 建议的下一步优先级

1. **P0**：把 `recon_ledger` 接进 v4，或明确废弃它并只保留 v4 的 `reconcile_orphan_position` —— **两套孤儿腿逻辑并存是当前最大风险**。
2. **P0**：给 v4 加私有成交流订阅（HL `userFills` / OKX 私有 `positions` 频道），否则 fill-implied 校验无法落地。
3. **P1**：补 `main` 的 `requirements.txt`，并注明 VPS 上那 3 个未入库模块。
4. **P1**：给 `tree-twin` 的 `CHANGELOG.md` 补 arb 条目（现有条目全是做市）。
5. **P2**：修 `main` 的 GLFT 量纲（D8）。

---

## 7. 补充：`ant_cross_arb_v4_adaptive.py` 内部的死配置（独立复核）

对 v4 的 42 个 `EngineConfigV4` 常量做全文本引用计数（排除定义行），**5 个 0 引用 + 1 个只在死方法内引用**：

| 常量 | 值 | 引用数 | 后果 |
|---|---|---|---|
| `REFILL_SYMBOL` | `"BTC"` | **0** | 逐小时 Taker 扩容已停用，常量残留 |
| `EXIT_TERMINAL_CONVERGE_BPS` | `50.0` | **0** | 注释里的"终极收敛 50bps 平仓"**从未实现** |
| `MAX_EFFECTIVE_LEVERAGE` | `5.0` | **0** | 与 `TARGET_LEVERAGE = 10` **自相矛盾**，且从未生效 |
| `INSTANT_DRIFT_BPS` | `8.5` | **0** | D10：8.5 bps 防插针，v2/v3/v4 三代都是死常量 |
| `MAX_CARRY_LOSS_APR` | `10.0` | **0** | 资金费容忍从未接线 |
| `DYNAMIC_HEDGE_RATIO` | `False` | 1（在**死方法** `resolve_hedge_ratio` 内） | 开关无效 |

**死符号占比 6/42 = 14.3%。** 这不是"技术债"，是**假控制** —— 读配置的人（包括审计方与 LLM）
会以为"我们防插针、有资金费上限、有 5x 有效杠杆上限"，实际一条都没运行。

### 7.1 同一决策两套实现（活一套、死一套，口径不同）

| 函数 | 行 | 调用点 | 越界行为 | honor `DYNAMIC_HEDGE_RATIO` |
|---|---|---|---|---|
| `plan_dual_leg_position` | 419 | **1**（`execute_atomic_trade`） | 返回 `ok=False` | **忽略**（恒用实时 S） |
| `resolve_hedge_ratio` | 910 | **0（死）** | 返回 `ratio=0.0` | honor |

留着死方法 = 留一条"看起来能改配平口径"的假入口。

### 7.2 注释与配置相互矛盾（会误导运维）

| 位置 | 注释写的 | 配置实际 |
|---|---|---|
| 第 12 行 | 净收益率 `>= 3.0%` 止盈 | `TARGET_NET_ROI_PCT = 8.0` |
| 第 13 行 | `Single-Leg 8% Price Deviation Breaker` | `SINGLE_LEG_MAX_PRICE_DRIFT = 0.16`（16%） |
| 第 15 行 | 任一腿 `>= 8.0%` | 同上 16% |
| 第 1026 行 | `>= 8%` | 同上 16% |

### 7.3 本轮为什么不改 v4

v4 是**唯一活引擎**（1292 行），本机跑不起来（缺 `websockets`/`hyperliquid`/`eth_account`，且无交易所连接）。
删除死常量属于"低风险但不可本地验证"的改动，与 §2 的纪律（必须证明订单序列不变）冲突，
故**本轮只记录不改**，把动作留给能跑冒烟测试的环境。

**建议 P1（在 VPS 上做）**：备份 → 删 6 个死常量 + 删 `resolve_hedge_ratio` →
把注释里的 3%/8%/8% 改为读 `self.cfg.*` → 跑 `scripts/smoke_test_v4.py` → 对比订单序列。

---

## 8. 红队审计后的两处更正（不可跳过）

### 8.1 【已修】5 个"单副本"文件曾被删到两边都没有

本轮的剔除把这些文件从 `tree-twin` 删掉，但它们**在 `main` 上从来不存在**：

| 文件 | 行数 |
|---|---|
| `shared/sentinel/market_regime_sentinel.py` | 215 |
| `shared/aggregator/micro_aggregator.py` | 99 |
| `kaggle_pipeline/kaggle_trainer_bridge.py` | 95 |
| `tests/test_regime_sentinel.py` | 71 |
| `tests/test_kaggle_bridge.py` | 41 |
| **合计** | **521** |

**后果**：两条分支的 tip 上都不再有它们（只能从 `73d50b6` 的历史里捞）。
**已修**：`main` 提交 `6275235` 把这 5 个文件恢复到 `main`（做市组的正确归属），main 现为 78 文件。

**教训（写进纪律）**：删除任何文件前，必须先验证"目标分支上是否存在同名文件"。
凡是单副本，先搬到归属分支、再删。

### 8.2 【未修，P0】`ant_cross_arb_v4_adaptive.py` 里**两条腿用了不同的对冲比率**

这是本次审计发现的**唯一能直接产生美元损失**的缺陷：

| 行 | 代码 | 口径 |
|---|---|---|
| 400 | `ratio = 10.0 / s_now if s_now > 0 else self.cfg.HEDGE_RATIO` | **动态**（按实时隐含 S） |
| 419–550 | `plan_dual_leg_position()` 用上面的动态 ratio 规划两腿 | 动态 |
| 552 | `plan = self.plan_dual_leg_position(hl_ask, okx_bid)` | 拿到动态计划 |
| **603** | **`actual_okx_sz = round(hl_filled_sz * self.cfg.HEDGE_RATIO, 3)`** | **静态 0.7874** |

**后果**：Leg 1 的规模按动态 ratio 规划，Leg 2 实际下单却用静态 `HEDGE_RATIO`。
当隐含 S ≠ 12.7B 时，两腿系统性错配：

- 当前隐含 S ≈ **13.94B** → 动态 ratio = `10/13.94 = 0.7174`
- Leg 2 实下 `0.7874` → **偏大 9.8%**
- 每 1 股 HL 多空 `0.0700 张 = 0.0889 股` → **净空约 8.9% 的裸敞口**

**两种修法（按用户已定 S=12.7B 的政策）**：

| 方案 | 做法 | 适用 |
|---|---|---|
| **A（推荐，符合用户政策）** | 把 400 行与 797 行的 `10.0/s_now` 改回 `self.cfg.HEDGE_RATIO`，并删掉 `DYNAMIC_HEDGE_RATIO` 开关 | 用户已明确 **S = 12.7B 固定** |
| B | 把 603 行改成用 `plan["okx_sz"]`（动态），并删掉 `resolve_hedge_ratio` 死方法 | 若改主意走动态隐含 S |

**验收**：任一 `hl_filled_sz` 下，`actual_okx_sz / hl_filled_sz` 必须恒等于规划用的 ratio（断言相等）。

### 8.3 本轮对我方执行的最终定性

| 项 | 结论 |
|---|---|
| 删除 MM 组件是否影响实盘 | **否**（v4 对它们 0 引用，blob hash 未变） |
| 剔除是否引入新 bug | **是，一处**：5 个单副本文件一度两边都没有 → 已恢复到 `main` |
| `BRANCH_AUDIT §4`（旧版）关于 merge 的说法 | **错**，已按实测更正 |
| 是否有更该修的东西 | **是**：§8.2 的两腿比率不一致 |

> 红队这一轮的净贡献：拦住了一次不可逆的内容丢失，并把注意力从"删文件"纠正到"修比率"。
> 这正是多 agent 对立论证应有的产出。
