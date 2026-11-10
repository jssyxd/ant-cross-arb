# legacy —— 已被取代的历史引擎（仅存档，**不要部署**）

| 文件 | 状态 | 取代者 | 说明 |
|---|---|---|---|
| `ant_cross_arb_live.py` | 已废弃 | `../ant_cross_arb_v4_adaptive.py` | 早期骨架。`execute_live_clip()` **只打印日志并 `return True`**，从不真正下单。曾被误描述为"生产级执行"，已更正。 |
| `ant_cross_arb_v2.py` | 已废弃 | 同上 | 含 `INSTANT_DRIFT_BPS` 常量但**从未引用**（死代码）。 |
| `ant_cross_arb_v3_ws.py` | 已废弃 | 同上 | 纯 WebSocket 版（619 行）。`INSTANT_DRIFT_BPS` 同样未接线；无持久化、无 `clOrdId`、无私有成交流订阅。 |

## 为什么保留而不是删除
1. **审计可追溯**：`recon/CROSS_CHECK_vs_twinCheck.md` 里对这三代引擎的具体缺陷判定（D9、D10）
   需要可回查的源文件，否则结论无法复算。
2. **回滚参考**：若 v4 出现无法就地修复的问题，需要一份可对照的旧实现。

## 使用纪律
- 任何 `deploy/` 下的 systemd 单元、任何 `ExecStart` **只能指向** `ant_cross_arb_v4_adaptive.py`。
- `check_branch_purity.py` 不检查本目录（存档允许存在），但会检查 `features/`、`models/`、`workflow/`
  这三个做市专用目录不得出现在本分支。
