# ant-cross-arb：两腿跨所基差波动收割

Hyperliquid `xyz:ANT`（多）对 OKX `ANTHROPIC-USDT-SWAP`（空），**赚两所 Anthropic 隐含价差的来回振荡（500~1300 bps），不赌上市 rebase 终值。**

## 核心假设

- 以 Anthropic S-1 的 **12.7B 股本**为锚（`ASSUMED_SHARES_B = 12.7`），计算两标的等价价差
- 市场隐含股本 ~14–15B → 价差常驻 500~1300 bps 区间
- 策略在这个区间里双边开仓（≥750bps 进场 → ≤500bps 或 8% ROI 出场）
- **不赌 S 最终会落到 12.7**——只赚它在区间里来回震荡的钱
- 两腿在开仓计划端保持美元中性；入场 / 平仓 / S 熔断全有断路器

## 目录

```
04_preipo_cross_arb/          # 跨所套利引擎（唯一主逻辑）
  core/                       # 计划、执行、平仓、配平
  recon/                      # 审计与量化研究（共享事实基线、PnL 分析、孤儿腿案例）
  legacy/                     # v2/v3 历史版本
  deploy/                     # systemd 单元文件
shared/                       # OKX REST 网关（只此一组件复用）
scripts/                      # 配额补充、冒烟测试
```

本仓库**不含任何做市组件**（GLFT 稳态报价、LightGBM 毒性门控、VPIN-OFI、LP 区间挂载）。以上全部留在 `GLFTmm@main`。两条线是完全不同的策略。纯净度由 `python 04_preipo_cross_arb/check_branch_purity.py` 断言。

## 快速开始

```bash
pip install -r 04_preipo_cross_arb/requirements.txt
# 配置 /etc/ant-arb.env（HL 私钥 + OKX API key，只读权限足够）
sudo cp 04_preipo_cross_arb/deploy/ant-arb.service /etc/systemd/system/
sudo systemctl enable --now ant-arb.service
```

## 研究文档

`04_preipo_cross_arb/recon/` 下包含本系统的完整量化审计：
- `SHARED_FACTS.md` — 共享事实基线
- `PROPOSED_SCHEME_PNL_ANALYSIS.md` — 滑点 / 容差 / 成本模型
- `OSS_RECON_CASES.md` — 跨所对账与孤儿腿案例
- `BRANCH_AUDIT.md` — 分支纯净度审计

## 风险

基差波动策略≠无风险套利。已知尾部包括：S 的单向漂移（股本一致预期移动）、HL API 配额耗尽导致平仓被拒、OKX rebase 规则变更、以及两腿同侧成交失败导致的单腿裸持。详见 `SHARED_FACTS.md` 的风险登记表。