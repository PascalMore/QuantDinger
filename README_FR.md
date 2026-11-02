<div align="center">
  <a href="https://github.com/OpenByteInc/QuantDinger">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/logo-dark.png">
      <img src="docs/screenshots/logo.png" alt="Logo QuantDinger" width="420">
    </picture>
  </a>

  <h1>QuantDinger</h1>
  <p><strong>Système d'exploitation de trading IA open source</strong></p>
  <p>Une plateforme auto-hébergée pour transformer des idées de trading en stratégies Python, backtests, paper trading, exécution réelle et supervision.</p>
  <p><em>Recherche IA → Code de stratégie → Backtest → Exécution simulée/réelle → Supervision</em></p>

  <p>
    <a href="README.md">English</a> · <a href="README_CN.md">简体中文</a> · <a href="README_TW.md">繁體中文</a> ·
    <a href="README_JA.md">日本語</a> · <a href="README_KO.md">한국어</a> · <a href="README_DE.md">Deutsch</a> ·
    <strong>Français</strong> · <a href="README_RU.md">Русский</a> · <a href="README_AR.md">العربية</a> ·
    <a href="README_TH.md">ไทย</a> · <a href="README_VI.md">Tiếng Việt</a>
  </p>

  <p>
    <a href="https://ai.quantdinger.com"><strong>Application en ligne</strong></a> ·
    <a href="https://www.quantdinger.com"><strong>Site officiel</strong></a> ·
    <a href="README.md"><strong>Documentation anglaise complète</strong></a>
  </p>

  <p>
    <a href="LICENSE"><img src="https://img.shields.io/badge/Backend-Apache%202.0-blue.svg?style=flat-square" alt="Apache 2.0"></a>
    <img src="docs/badges/python-3.12.svg" alt="Python 3.12">
    <img src="docs/badges/docker-compose.svg" alt="Docker Compose">
    <a href="https://github.com/OpenByteInc/QuantDinger/releases/latest"><img src="docs/badges/latest-release.svg" alt="Dernière version"></a>
  </p>
</div>

> Lorsque le trading réel est explicitement activé, QuantDinger peut envoyer de vrais ordres. Commencez par le paper trading, limitez les droits de vos clés API et vérifiez les exigences légales et réglementaires de votre juridiction. Ce projet ne fournit aucun conseil en investissement.

## Démonstration

<p align="center">
  <img src="docs/screenshots/quantdinger-v5-2x.gif" alt="Démonstration de QuantDinger" width="800">
</p>

## Qu'est-ce que QuantDinger ?

QuantDinger est un **système d'exploitation de trading IA open source** destiné aux traders indépendants, aux développeurs de stratégies Python et aux petites équipes. Son architecture locale et auto-hébergée vous laisse le contrôle des données de marché, du code, des identifiants de courtage et du déploiement.

## Fonctionnalités principales

- Recherche de marché avec plusieurs fournisseurs d'IA, rapports professionnels et univers réutilisables
- Strategy API V2 pour indicateurs Python, stratégies CTA et portefeuilles
- Backtests côté serveur, recherche factorielle, évolution de stratégies et analyses de robustesse
- Exécution simulée et réelle sur les plateformes crypto et auprès de courtiers traditionnels
- Web, mobile H5, Human API, Agent Gateway et MCP
- Persistance PostgreSQL, workers distribués, journaux d'audit et supervision optionnelle

QuantDinger n'est pas un service de signaux opaque : l'opérateur garde le contrôle du code, des risques, des identifiants et du déploiement.

## Démarrage rapide

Prérequis : Docker avec Compose v2. Les images précompilées ne nécessitent ni Node.js ni environnement Python local.

Linux / macOS :

```bash
curl -fsSL https://raw.githubusercontent.com/OpenByteInc/QuantDinger/main/install.sh | bash
```

Windows PowerShell :

```powershell
irm https://raw.githubusercontent.com/OpenByteInc/QuantDinger/main/install.ps1 | iex
```

Après l'installation :

- Web : <http://127.0.0.1:8888>
- Mobile H5 : <http://127.0.0.1:8889>
- État de l'API : <http://127.0.0.1:5000/api/health>

## Documentation et communauté

- [README anglais complet](README.md) / [README chinois](README_CN.md)
- [Documentation API](docs/api/README.md) / [Agents IA et MCP](docs/agent/README.md)
- [Guide de développement de stratégies](docs/trading/STRATEGY_DEV_GUIDE.md) / [Dépannage de l'installation](docs/deployment/INSTALL_TROUBLESHOOTING.md)
- [Telegram](https://t.me/quantdinger) / [Discord](https://discord.com/invite/tyx5B6TChr) / [YouTube](https://youtube.com/@quantdinger)

## Licence

Le backend est distribué sous [licence Apache 2.0](LICENSE). Le frontend Web et le client mobile possèdent leurs propres licences « source available ». Consultez [TRADEMARKS.md](TRADEMARKS.md) pour les règles relatives aux marques et à l'identité visuelle.
