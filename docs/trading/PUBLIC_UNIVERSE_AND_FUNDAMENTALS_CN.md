# 公开股票池基础库与基本面数据约定

## 1. 默认公开股票池

QuantDinger 可以从公开数据源维护以下股票池。实际成员数量与来源版本会随
再平衡、数据源可用性和完整性校验变化，不应把文档中的示例数量当作交易保证。

| 股票池 | 来源与约束 |
|---|---|
| 上证50、沪深300、中证500、中证800、中证1000 | 中证指数公开成分接口，经 AKShare 适配 |
| 标普500 | `datasets/s-and-p-500-companies`，ODC PDDL |
| 纳斯达克100 | `Gary-Strauss/NASDAQ100_Constituents`，MIT；底层数据来自 Wikipedia，需保留 CC BY-SA 署名 |
| 道琼斯工业30 | Wikipedia 公开成分表，CC BY-SA |
| 标普中盘400、标普小盘600、罗素1000 | Wikipedia 公开成分表，CC BY-SA |
| 加密市值 Top-100 | CoinGecko 当前市值排序接口 |
| 恒生指数、恒生科技30、恒生国企50 | 恒生指数公司官网当前成分 API |
| 港股通 | 东方财富港股通当前成分接口 |

首次导入只建立当时可观察到的成分快照，不会伪造更早的历史成员。后续更新会
关闭被剔除成分的有效区间，并为新增成分建立新的 `valid_from`，逐步积累平台
自己的时点历史。

标普500和纳斯达克100可以按需补齐时点历史。标普500使用同一 PDDL 数据源的
GitHub 修订记录，纳斯达克100使用 `jmccarrell/n100tickers` 的 MIT 历史变更。
命令默认只预览，不写数据库：

```bash
python scripts/backfill_public_universe_history.py \
  --universe sp500 \
  --from 2023-01-01
```

把 `sp500` 改成 `nasdaq100` 即可补齐纳斯达克100。上证50、中证系列、道琼斯30、
标普中盘400、标普小盘600、罗素1000和恒生系列目前只使用当前公开快照；在没有
可审计的历史来源时，系统不会把当前成分倒填成历史成分。

确认预览的成员数、区间数和本地历史衔接日期后，加 `--apply` 执行。导入器会在
现有本地快照的最早日期处截断外部历史，保留此后的本地数据；重复执行会先替换
上一次由该导入器写入的区间，因此可以安全重跑。标普500以源文件的 GitHub
提交日期作为可观察日期；纳斯达克100按历史变更文件记录的生效日期落库。导入器
不会把后来获知的成分变化回填到更早日期。服务器受 GitHub API 限流时，可通过
`GITHUB_TOKEN` 环境变量提供只读令牌。

管理员也可以在“系统设置 → 数据状态与同步 → 系统标的池快照”中选择历史起点并
同步所选标的池。页面只会对标普500和纳斯达克100执行可审计的历史回填；其他
标的池更新当前快照并返回历史来源不可用提示，不会用当前成分伪造过去成分。

刷新命令：

```bash
python scripts/refresh_public_universe_snapshots.py \
  --universes sp500,nasdaq100,dow30,sp400,sp600,russell1000,sse50,csi300,csi500,csi800,csi1000,crypto_top100,hk_hsi,hk_tech30,hk_china_enterprises50,hk_stock_connect \
  --as-of YYYY-MM-DD
```

先增加 `--dry-run` 检查数量。脚本内置完整性门槛，数量异常时拒绝写库。

## 2. 港股和 ETF 分类

港股指数池使用恒生指数公司官网当前成分 API，ETF 分类直接使用证券主表：

- 港股指数池：完整恒生指数、恒生科技30、恒生国企50，以及覆盖更广的港股通股票池。
- 港股核心 ETF：`HKStock + etf + is_hot`，由证券目录维护宽基、科技、红利、黄金和债券 ETF
- 美股核心 ETF：`USStock + etf + is_hot`，由证券目录维护主流宽基、行业、债券和商品 ETF
- 全量港股仍保留在证券搜索主表，不默认作为截面回测池。

证券同步会读取 HKEX `ListOfSecurities.xlsx` 的 `Category` 字段，把 `Equity` 和 `Exchange Traded Products` 分开。美国证券目录使用 Nasdaq Trader 的 `ETF` 标记。

## 3. 基本面数据

`qd_fundamental_snapshots` 保存时点基本面。最重要的两个日期是：

- `period_end`：报表所属期间结束日。
- `available_at`：市场实际能看到这份数据的日期，回测只能从该日开始使用。

支持字段：

```text
revenue
net_income
book_value
shareholder_equity
total_debt
free_cash_flow
shares_outstanding
market_cap
```

小市值因子优先使用供应商给出的 `market_cap`；如果缺失，则使用当日收盘价乘以当时已公开的 `shares_outstanding`。财报值会从 `available_at` 起向后生效，不会回填到公告日前。

CSV 导入：

```bash
python scripts/import_fundamental_snapshots.py fundamentals.csv \
  --source manual_csv \
  --source-version YYYY-MM
```

CSV 至少包含：

```text
market,symbol,period_end,available_at,frequency,currency
```

其余基本面字段可以为空。做小市值策略至少需要 `market_cap`，或同时提供 `shares_outstanding` 和日线收盘价。

## 4. 已知限制

- 策略候选池按数据周期限制容量：分钟级默认最多 600 只，日线和周线默认最多
  1,200 只。两个上限可分别通过 `STRATEGY_V2_MAX_SYMBOLS` 和
  `STRATEGY_V2_MAX_DAILY_SYMBOLS` 调整；行情加载固定为最多 8 个并发任务。
- 公开快照适合产品启动和研究验证，不等价于官方商业指数授权。
- 标普和纳斯达克名称、原始成分展示及商业使用仍应在正式运营前复核许可。
- 加密 Top-100 当前未自动排除稳定币、包装资产和质押衍生品，生产规则需要另行固化。
- 真正的历史基本面需要接入财务数据源，手工 CSV 只适合小规模验证。
- 小市值策略在没有时点市值或股本数据时会得到空排名，系统不会用股价或成交额伪造市值。
