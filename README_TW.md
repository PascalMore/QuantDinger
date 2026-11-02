<div align="center">
  <a href="https://github.com/OpenByteInc/QuantDinger">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/logo-dark.png">
      <img src="docs/screenshots/logo.png" alt="QuantDinger 標誌" width="420">
    </picture>
  </a>

  <h1>QuantDinger</h1>
  <p><strong>開源 AI 交易作業系統</strong></p>
  <p>在一套可自行託管的平台中，將交易構想轉化為 Python 策略、回測、模擬交易、實盤執行與監控。</p>
  <p><em>AI 投研 → 策略程式碼 → 回測 → 模擬／實盤執行 → 監控</em></p>

  <p>
    <a href="README.md">English</a> · <a href="README_CN.md">简体中文</a> · <strong>繁體中文</strong> ·
    <a href="README_JA.md">日本語</a> · <a href="README_KO.md">한국어</a> · <a href="README_DE.md">Deutsch</a> ·
    <a href="README_FR.md">Français</a> · <a href="README_RU.md">Русский</a> · <a href="README_AR.md">العربية</a> ·
    <a href="README_TH.md">ไทย</a> · <a href="README_VI.md">Tiếng Việt</a>
  </p>

  <p>
    <a href="https://ai.quantdinger.com"><strong>線上應用</strong></a> ·
    <a href="https://www.quantdinger.com"><strong>官方網站</strong></a> ·
    <a href="README.md"><strong>完整英文文件</strong></a>
  </p>

  <p>
    <a href="LICENSE"><img src="https://img.shields.io/badge/Backend-Apache%202.0-blue.svg?style=flat-square" alt="Apache 2.0"></a>
    <img src="docs/badges/python-3.12.svg" alt="Python 3.12">
    <img src="docs/badges/docker-compose.svg" alt="Docker Compose">
    <a href="https://github.com/OpenByteInc/QuantDinger/releases/latest"><img src="docs/badges/latest-release.svg" alt="最新版本"></a>
  </p>
</div>

> QuantDinger 在明確啟用實盤交易後可以提交真實訂單。請先使用模擬交易、限制 API Key 權限，並確認所在地區的法規與風險要求。本專案不提供投資建議。

## 產品展示

<p align="center">
  <img src="docs/screenshots/quantdinger-v5-2x.gif" alt="QuantDinger 產品展示" width="800">
</p>

## QuantDinger 是什麼？

QuantDinger 是為獨立交易者、Python 策略開發者與小型團隊打造的**開源 AI 交易作業系統**。本地優先、可自行託管的設計，讓市場資料、策略程式碼、券商憑證與部署控制權保留在使用者手中。

## 核心能力

- 多供應商 AI 市場研究、專業分析報告與可重用標的池
- Python 指標、CTA 與投資組合 Strategy API V2 開發環境
- 伺服器端回測、因子研究、策略演化與穩健性分析
- 支援加密貨幣交易所與傳統券商的模擬及實盤執行
- Web、行動 H5、Human API、Agent Gateway 與 MCP 存取
- PostgreSQL 持久化、分散式工作程序、稽核日誌與可選監控

QuantDinger 不是黑盒訊號服務；策略邏輯、風險設定與部署方式均由操作者掌控。

## 一鍵安裝

需求：Docker 與 Compose v2。使用預建映像時不需要安裝 Node.js 或 Python。

Linux / macOS：

```bash
curl -fsSL https://raw.githubusercontent.com/OpenByteInc/QuantDinger/main/install.sh | bash
```

Windows PowerShell：

```powershell
irm https://raw.githubusercontent.com/OpenByteInc/QuantDinger/main/install.ps1 | iex
```

安裝完成後開啟：

- Web：<http://127.0.0.1:8888>
- 行動 H5：<http://127.0.0.1:8889>
- API 健康檢查：<http://127.0.0.1:5000/api/health>

## 文件與社群

- [完整英文 README](README.md)／[簡體中文 README](README_CN.md)
- [API 文件](docs/api/README.md)／[AI Agent 與 MCP](docs/agent/README.md)
- [策略開發指南](docs/trading/STRATEGY_DEV_GUIDE.md)／[安裝疑難排解](docs/deployment/INSTALL_TROUBLESHOOTING.md)
- [Telegram](https://t.me/quantdinger)／[Discord](https://discord.com/invite/tyx5B6TChr)／[YouTube](https://youtube.com/@quantdinger)

## 授權

後端採用 [Apache License 2.0](LICENSE)。Web 前端與行動端使用各自的原始碼可用授權；商標與品牌使用規則請參閱 [TRADEMARKS.md](TRADEMARKS.md)。
