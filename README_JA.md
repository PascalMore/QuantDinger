<div align="center">
  <a href="https://github.com/OpenByteInc/QuantDinger">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/logo-dark.png">
      <img src="docs/screenshots/logo.png" alt="QuantDinger ロゴ" width="420">
    </picture>
  </a>

  <h1>QuantDinger</h1>
  <p><strong>オープンソースの AI トレーディング OS</strong></p>
  <p>売買アイデアを Python 戦略、バックテスト、ペーパートレード、ライブ執行、監視へつなぐセルフホスト型プラットフォーム。</p>
  <p><em>AI リサーチ → 戦略コード → バックテスト → ペーパー／ライブ執行 → 監視</em></p>

  <p>
    <a href="README.md">English</a> · <a href="README_CN.md">简体中文</a> · <a href="README_TW.md">繁體中文</a> ·
    <strong>日本語</strong> · <a href="README_KO.md">한국어</a> · <a href="README_DE.md">Deutsch</a> ·
    <a href="README_FR.md">Français</a> · <a href="README_RU.md">Русский</a> · <a href="README_AR.md">العربية</a> ·
    <a href="README_TH.md">ไทย</a> · <a href="README_VI.md">Tiếng Việt</a>
  </p>

  <p>
    <a href="https://ai.quantdinger.com"><strong>ライブアプリ</strong></a> ·
    <a href="https://www.quantdinger.com"><strong>公式サイト</strong></a> ·
    <a href="README.md"><strong>英語版の完全なドキュメント</strong></a>
  </p>

  <p>
    <a href="LICENSE"><img src="https://img.shields.io/badge/Backend-Apache%202.0-blue.svg?style=flat-square" alt="Apache 2.0"></a>
    <img src="docs/badges/python-3.12.svg" alt="Python 3.12">
    <img src="docs/badges/docker-compose.svg" alt="Docker Compose">
    <a href="https://github.com/OpenByteInc/QuantDinger/releases/latest"><img src="docs/badges/latest-release.svg" alt="最新リリース"></a>
  </p>
</div>

> ライブ取引を明示的に有効化すると、QuantDinger は実注文を送信できます。まずペーパートレードを利用し、API キーの権限を制限し、地域の法規制とリスク要件を確認してください。本プロジェクトは投資助言を提供しません。

## デモ

<p align="center">
  <img src="docs/screenshots/quantdinger-v5-2x.gif" alt="QuantDinger 製品デモ" width="800">
</p>

## QuantDinger とは

QuantDinger は、個人トレーダー、Python 戦略開発者、小規模チーム向けの**オープンソース AI トレーディング OS**です。ローカルファーストのセルフホスト設計により、市場データ、戦略コード、ブローカー認証情報、デプロイを運用者自身が管理できます。

## 主な機能

- 複数 AI プロバイダーによる市場調査、プロフェッショナルレポート、再利用可能なユニバース
- Python インジケーター、CTA、ポートフォリオ向け Strategy API V2
- サーバーサイドのバックテスト、ファクター研究、戦略進化、頑健性分析
- 暗号資産取引所と従来型ブローカーでのペーパー／ライブ執行
- Web、モバイル H5、Human API、Agent Gateway、MCP
- PostgreSQL 永続化、分散ワーカー、監査ログ、オプションの監視基盤

QuantDinger はブラックボックス型のシグナルサービスではありません。戦略ロジック、リスク設定、認証情報、デプロイは運用者が管理します。

## クイックスタート

必要環境：Docker と Compose v2。ビルド済みイメージを使う場合、Node.js と Python のローカル環境は不要です。

Linux / macOS：

```bash
curl -fsSL https://raw.githubusercontent.com/OpenByteInc/QuantDinger/main/install.sh | bash
```

Windows PowerShell：

```powershell
irm https://raw.githubusercontent.com/OpenByteInc/QuantDinger/main/install.ps1 | iex
```

起動後のアクセス先：

- Web：<http://127.0.0.1:8888>
- モバイル H5：<http://127.0.0.1:8889>
- API ヘルスチェック：<http://127.0.0.1:5000/api/health>

## ドキュメントとコミュニティ

- [英語版の完全な README](README.md)／[簡体中国語 README](README_CN.md)
- [API ドキュメント](docs/api/README.md)／[AI Agent と MCP](docs/agent/README.md)
- [戦略開発ガイド](docs/trading/STRATEGY_DEV_GUIDE.md)／[インストールのトラブルシューティング](docs/deployment/INSTALL_TROUBLESHOOTING.md)
- [Telegram](https://t.me/quantdinger)／[Discord](https://discord.com/invite/tyx5B6TChr)／[YouTube](https://youtube.com/@quantdinger)

## ライセンス

バックエンドは [Apache License 2.0](LICENSE) です。Web フロントエンドとモバイルクライアントには、それぞれのソース公開ライセンスが適用されます。商標とブランドの利用条件は [TRADEMARKS.md](TRADEMARKS.md) を参照してください。
