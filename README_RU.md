<div align="center">
  <a href="https://github.com/OpenByteInc/QuantDinger">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/logo-dark.png">
      <img src="docs/screenshots/logo.png" alt="Логотип QuantDinger" width="420">
    </picture>
  </a>

  <h1>QuantDinger</h1>
  <p><strong>Открытая операционная система для AI-трейдинга</strong></p>
  <p>Самостоятельно размещаемая платформа, которая превращает торговые идеи в Python-стратегии, бэктесты, бумажную торговлю, реальное исполнение и мониторинг.</p>
  <p><em>AI-исследование → Код стратегии → Бэктест → Демо-/реальное исполнение → Мониторинг</em></p>

  <p>
    <a href="README.md">English</a> · <a href="README_CN.md">简体中文</a> · <a href="README_TW.md">繁體中文</a> ·
    <a href="README_JA.md">日本語</a> · <a href="README_KO.md">한국어</a> · <a href="README_DE.md">Deutsch</a> ·
    <a href="README_FR.md">Français</a> · <strong>Русский</strong> · <a href="README_AR.md">العربية</a> ·
    <a href="README_TH.md">ไทย</a> · <a href="README_VI.md">Tiếng Việt</a>
  </p>

  <p>
    <a href="https://ai.quantdinger.com"><strong>Онлайн-приложение</strong></a> ·
    <a href="https://www.quantdinger.com"><strong>Официальный сайт</strong></a> ·
    <a href="README.md"><strong>Полная документация на английском</strong></a>
  </p>

  <p>
    <a href="LICENSE"><img src="https://img.shields.io/badge/Backend-Apache%202.0-blue.svg?style=flat-square" alt="Apache 2.0"></a>
    <img src="docs/badges/python-3.12.svg" alt="Python 3.12">
    <img src="docs/badges/docker-compose.svg" alt="Docker Compose">
    <a href="https://github.com/OpenByteInc/QuantDinger/releases/latest"><img src="docs/badges/latest-release.svg" alt="Последний релиз"></a>
  </p>
</div>

> После явного включения реальной торговли QuantDinger может отправлять настоящие заявки. Начните с бумажной торговли, ограничьте права API-ключей и проверьте требования законодательства и регулирования вашей юрисдикции. Проект не предоставляет инвестиционных рекомендаций.

## Демонстрация

<p align="center">
  <img src="docs/screenshots/quantdinger-v5-2x.gif" alt="Демонстрация QuantDinger" width="800">
</p>

## Что такое QuantDinger?

QuantDinger — это **открытая операционная система для AI-трейдинга** для независимых трейдеров, разработчиков Python-стратегий и небольших команд. Локальная самостоятельная установка сохраняет под вашим контролем рыночные данные, код стратегий, учётные данные брокеров и развёртывание.

## Основные возможности

- Рыночные исследования с несколькими AI-провайдерами, профессиональные отчёты и повторно используемые наборы инструментов
- Strategy API V2 для Python-индикаторов, CTA- и портфельных стратегий
- Серверные бэктесты, факторные исследования, эволюция стратегий и анализ устойчивости
- Демо- и реальное исполнение через криптобиржи и традиционных брокеров
- Web, Mobile H5, Human API, Agent Gateway и MCP
- PostgreSQL, распределённые воркеры, аудит и дополнительный мониторинг

QuantDinger — не закрытый сервис сигналов. Код стратегий, риски, учётные данные и развёртывание контролирует оператор.

## Быстрый старт

Требования: Docker и Compose v2. При использовании готовых образов локальные Node.js и Python не нужны.

Linux / macOS:

```bash
curl -fsSL https://raw.githubusercontent.com/OpenByteInc/QuantDinger/main/install.sh | bash
```

Windows PowerShell:

```powershell
irm https://raw.githubusercontent.com/OpenByteInc/QuantDinger/main/install.ps1 | iex
```

После установки:

- Web: <http://127.0.0.1:8888>
- Mobile H5: <http://127.0.0.1:8889>
- Проверка API: <http://127.0.0.1:5000/api/health>

## Документация и сообщество

- [Полный README на английском](README.md) / [README на китайском](README_CN.md)
- [Документация API](docs/api/README.md) / [AI Agents и MCP](docs/agent/README.md)
- [Руководство по стратегиям](docs/trading/STRATEGY_DEV_GUIDE.md) / [Решение проблем установки](docs/deployment/INSTALL_TROUBLESHOOTING.md)
- [Telegram](https://t.me/quantdinger) / [Discord](https://discord.com/invite/tyx5B6TChr) / [YouTube](https://youtube.com/@quantdinger)

## Лицензия

Backend распространяется по [Apache License 2.0](LICENSE). Для Web-интерфейса и мобильного клиента действуют отдельные лицензии с доступным исходным кодом. Правила использования товарных знаков и бренда приведены в [TRADEMARKS.md](TRADEMARKS.md).
