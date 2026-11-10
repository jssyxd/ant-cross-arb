# Field Guide: Herdr 派发与 HIP-3 取数（captain 实测）

## 2026-11-10: `herdr-dispatch` 在本机只能用 `--kind omp`，不能用 `--kind pi`

- `doctor` 的 `kinds` 里有 `pi`，但 `assign --kind pi` 两次都 `timed out waiting for agent startup`
  （默认 30s 与 `--start-timeout 150000` 均失败）。`pi --list-models` / `pi -p` 本身可用，故障在 Herdr 侧的就绪探测。
- 本机所有在跑的 worker（`herdr agent list`）`agent` 字段都是 `omp`，进程是 `C:\Users\da\AppData\Local\omp\omp.exe`，
  提示符 `π >` 是 **omp** 的 UI，不是 `pi`。→ 要派 worker 用 `--kind omp`。
- `--model` 对 `omp` 无映射（`doctor.model_kinds` 只有 claude/codex/cursor/grok/pi）。用 args 传：
  `herd-dispatch assign --kind omp "--args=--model=alibaba-token-plan/glm-5.3" --name ...`
  必须写成 `--args=...`（用空格分隔会被 argparse 当成 `--args` 缺值）。
- 可用模型（`omp models`）：provider `alibaba-token-plan` 下有 `deepseek-v4-pro`、`deepseek-v4.1-flash-1017`、
  `glm-5.2`、`glm-5.3`、`qwen3.7-max/plus`、`qwen3.8-max/flash`。`glm-5.2` / `qwen3.8-max` 有 1M 上下文。

## 2026-11-10: OKX 公共 REST 从本机直连返回空 body

```
curl -s -m 20 "https://www.okx.com/api/v5/market/candles?instId=ANTHROPIC-USDT-SWAP&bar=1H&limit=3"
→ 空字符串（无 HTTP 错误、无 JSON）
```
`aws.okx.com` 同样空。而 `api.hyperliquid.xyz` 正常。→ 需要 OKX 数据时不要假设「空 = 没有数据」，
先判定为**网络/风控拦截**；生产路径依赖 VPS 的 IP 白名单（23.27.186.126、155.254.60.38）。

## 2026-11-10: HIP-3 取数必须带 `dex`，且存在「有标的、零成交」的死 dex

- `{"type":"meta","dex":"xyz"}` / `{"type":"l2Book","coin":"xyz:NVDA"}` 才返回该 dex 的标的与盘口；
  不带 `dex` 返回主池（对 `xyz:ANT` 来说权益与标的全是 0）。
- `km` 与 `mkts` 是**同一 deployer**（`0x71f0019cc7fa79e4f42587fb7b9a817d8d2429ec`）的两个 dex，
  标的与报价逐位相同，**24h 成交额全为 0**，但 `markPx` 仍在返回。
  用 markPx 算出来的「跨 DEX 基差 300~5300 bps」是陈旧报价，不是机会。
- `io:ANTH` 与 `xyz:ANT` 名字近似、单位完全不同：mid 基差 **163,995 bps**（≈1650 倍）。
  任何按 ticker 名匹配的配对逻辑都会把它当大机会 → 配对必须过单位校准（比值 CV / 收益相关）。

## 2026-11-10: HL 费率的可引用基数（HIP-3 需另加 deployer share）

- 主网 perp 基础档：taker **0.045%**（4.5 bps）、maker **0.015%**（1.5 bps）。
  来源 `https://hyperliquid.gitbook.io/hyperliquid-docs/trading/fees`（2026-11-10 抓取）。
- HIP-3 deployer 可配置 **0–300%** 的额外 fee share（growth mode 0–100%）；share > 100% 时
  协议费同步抬到与 deployer 费相等 → 极端配置下 taker 可达基础的 4 倍。`perpDexs` 不返回该比例，
  只能从成交回执的 `fee` 字段反推。
- HIP-3 其他硬约束（`https://hyperliquid.gitbook.io/.../hip-3-builder-deployed-perpetuals`）：
  主网质押 **500k HYPE**、每个 dex 前 3 个标的不需拍卖、后续标的走荷兰拍、每个 dex 独立保证金与订单簿、
  deployer 负责 oracle 与杠杆上限。