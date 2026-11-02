<div align="center">
  <a href="https://github.com/OpenByteInc/QuantDinger">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/logo-dark.png">
      <img src="docs/screenshots/logo.png" alt="QuantDinger-Logo" width="420">
    </picture>
  </a>

  <h1>QuantDinger</h1>
  <p><strong>Open-Source-KI-Trading-Betriebssystem</strong></p>
  <p>Eine selbst gehostete Plattform, die Trading-Ideen in Python-Strategien, Backtests, Paper Trading, Live-Ausführung und Monitoring überführt.</p>
  <p><em>KI-Research → Strategiecode → Backtest → Paper-/Live-Ausführung → Monitoring</em></p>

  <p>
    <a href="README.md">English</a> · <a href="README_CN.md">简体中文</a> · <a href="README_TW.md">繁體中文</a> ·
    <a href="README_JA.md">日本語</a> · <a href="README_KO.md">한국어</a> · <strong>Deutsch</strong> ·
    <a href="README_FR.md">Français</a> · <a href="README_RU.md">Русский</a> · <a href="README_AR.md">العربية</a> ·
    <a href="README_TH.md">ไทย</a> · <a href="README_VI.md">Tiếng Việt</a>
  </p>

  <p>
    <a href="https://ai.quantdinger.com"><strong>Live-App</strong></a> ·
    <a href="https://www.quantdinger.com"><strong>Website</strong></a> ·
    <a href="README.md"><strong>Vollständige englische Dokumentation</strong></a>
  </p>

  <p>
    <a href="LICENSE"><img src="https://img.shields.io/badge/Backend-Apache%202.0-blue.svg?style=flat-square" alt="Apache 2.0"></a>
    <img src="docs/badges/python-3.12.svg" alt="Python 3.12">
    <img src="docs/badges/docker-compose.svg" alt="Docker Compose">
    <a href="https://github.com/OpenByteInc/QuantDinger/releases/latest"><img src="docs/badges/latest-release.svg" alt="Neueste Version"></a>
  </p>
</div>

> Wenn Live-Trading ausdrücklich aktiviert wird, kann QuantDinger echte Orders senden. Beginnen Sie mit Paper Trading, beschränken Sie die Rechte Ihrer API-Schlüssel und prüfen Sie die rechtlichen sowie regulatorischen Anforderungen Ihres Landes. Dieses Projekt ist keine Anlageberatung.

## Produktdemo

<p align="center">
  <img src="docs/screenshots/quantdinger-v5-2x.gif" alt="QuantDinger-Produktdemo" width="800">
</p>

## Was ist QuantDinger?

QuantDinger ist ein **Open-Source-KI-Trading-Betriebssystem** für unabhängige Trader, Entwickler von Python-Strategien und kleine Teams. Das lokale, selbst gehostete Konzept lässt Marktdaten, Strategiecode, Broker-Zugangsdaten und Bereitstellung unter Ihrer Kontrolle.

## Kernfunktionen

- KI-gestützte Marktanalyse mit mehreren Anbietern, professionelle Berichte und wiederverwendbare Universen
- Strategy API V2 für Python-Indikatoren, CTA- und Portfoliostrategien
- Serverseitige Backtests, Faktor-Research, Strategieoptimierung und Robustheitsanalysen
- Paper- und Live-Ausführung über Kryptobörsen und klassische Broker
- Web, Mobile H5, Human API, Agent Gateway und MCP
- PostgreSQL-Persistenz, verteilte Worker, Audit-Logs und optionales Monitoring

QuantDinger ist kein Blackbox-Signaldienst. Strategiecode, Risikoeinstellungen, Zugangsdaten und Betrieb bleiben unter der Kontrolle des Betreibers.

## Schnellstart

Voraussetzung: Docker mit Compose v2. Bei Verwendung der vorgefertigten Images sind weder Node.js noch eine lokale Python-Umgebung erforderlich.

Linux / macOS:

```bash
curl -fsSL https://raw.githubusercontent.com/OpenByteInc/QuantDinger/main/install.sh | bash
```

Windows PowerShell:

```powershell
irm https://raw.githubusercontent.com/OpenByteInc/QuantDinger/main/install.ps1 | iex
```

Nach der Installation:

- Web: <http://127.0.0.1:8888>
- Mobile H5: <http://127.0.0.1:8889>
- API-Status: <http://127.0.0.1:5000/api/health>

## Dokumentation und Community

- [Vollständiges englisches README](README.md) / [Chinesisches README](README_CN.md)
- [API-Dokumentation](docs/api/README.md) / [AI Agents und MCP](docs/agent/README.md)
- [Leitfaden zur Strategieentwicklung](docs/trading/STRATEGY_DEV_GUIDE.md) / [Installationshilfe](docs/deployment/INSTALL_TROUBLESHOOTING.md)
- [Telegram](https://t.me/quantdinger) / [Discord](https://discord.com/invite/tyx5B6TChr) / [YouTube](https://youtube.com/@quantdinger)

## Lizenz

Das Backend steht unter der [Apache License 2.0](LICENSE). Für Web-Frontend und Mobile-Client gelten eigene Source-Available-Lizenzen. Hinweise zur Nutzung von Marken und Branding finden Sie in [TRADEMARKS.md](TRADEMARKS.md).
