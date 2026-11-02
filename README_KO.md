<div align="center">
  <a href="https://github.com/OpenByteInc/QuantDinger">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/logo-dark.png">
      <img src="docs/screenshots/logo.png" alt="QuantDinger 로고" width="420">
    </picture>
  </a>

  <h1>QuantDinger</h1>
  <p><strong>오픈 소스 AI 트레이딩 OS</strong></p>
  <p>트레이딩 아이디어를 Python 전략, 백테스트, 모의 거래, 실거래 실행 및 모니터링으로 연결하는 셀프 호스팅 플랫폼입니다.</p>
  <p><em>AI 리서치 → 전략 코드 → 백테스트 → 모의／실거래 실행 → 모니터링</em></p>

  <p>
    <a href="README.md">English</a> · <a href="README_CN.md">简体中文</a> · <a href="README_TW.md">繁體中文</a> ·
    <a href="README_JA.md">日本語</a> · <strong>한국어</strong> · <a href="README_DE.md">Deutsch</a> ·
    <a href="README_FR.md">Français</a> · <a href="README_RU.md">Русский</a> · <a href="README_AR.md">العربية</a> ·
    <a href="README_TH.md">ไทย</a> · <a href="README_VI.md">Tiếng Việt</a>
  </p>

  <p>
    <a href="https://ai.quantdinger.com"><strong>라이브 앱</strong></a> ·
    <a href="https://www.quantdinger.com"><strong>공식 웹사이트</strong></a> ·
    <a href="README.md"><strong>영문 전체 문서</strong></a>
  </p>

  <p>
    <a href="LICENSE"><img src="https://img.shields.io/badge/Backend-Apache%202.0-blue.svg?style=flat-square" alt="Apache 2.0"></a>
    <img src="docs/badges/python-3.12.svg" alt="Python 3.12">
    <img src="docs/badges/docker-compose.svg" alt="Docker Compose">
    <a href="https://github.com/OpenByteInc/QuantDinger/releases/latest"><img src="docs/badges/latest-release.svg" alt="최신 릴리스"></a>
  </p>
</div>

> 실거래를 명시적으로 활성화하면 QuantDinger가 실제 주문을 제출할 수 있습니다. 먼저 모의 거래를 사용하고 API 키 권한을 제한하며 해당 지역의 규정과 위험 요건을 확인하세요. 이 프로젝트는 투자 자문을 제공하지 않습니다.

## 제품 데모

<p align="center">
  <img src="docs/screenshots/quantdinger-v5-2x.gif" alt="QuantDinger 제품 데모" width="800">
</p>

## QuantDinger란?

QuantDinger는 개인 트레이더, Python 전략 개발자, 소규모 팀을 위한 **오픈 소스 AI 트레이딩 OS**입니다. 로컬 우선 셀프 호스팅 설계를 통해 시장 데이터, 전략 코드, 브로커 자격 증명 및 배포를 운영자가 직접 관리할 수 있습니다.

## 핵심 기능

- 여러 AI 제공자를 활용한 시장 리서치, 전문 분석 보고서 및 재사용 가능한 유니버스
- Python 지표, CTA 및 포트폴리오용 Strategy API V2 개발 환경
- 서버 측 백테스트, 팩터 리서치, 전략 진화 및 견고성 분석
- 암호화폐 거래소와 전통 브로커를 통한 모의 및 실거래 실행
- Web, 모바일 H5, Human API, Agent Gateway 및 MCP 접근
- PostgreSQL 영속성, 분산 워커, 감사 로그 및 선택형 모니터링

QuantDinger는 블랙박스 신호 서비스가 아닙니다. 전략 로직, 위험 설정, 자격 증명 및 배포는 운영자가 통제합니다.

## 빠른 시작

요구 사항: Docker 및 Compose v2. 사전 빌드 이미지를 사용하면 로컬 Node.js 또는 Python 환경이 필요하지 않습니다.

Linux / macOS:

```bash
curl -fsSL https://raw.githubusercontent.com/OpenByteInc/QuantDinger/main/install.sh | bash
```

Windows PowerShell:

```powershell
irm https://raw.githubusercontent.com/OpenByteInc/QuantDinger/main/install.ps1 | iex
```

설치 후 접속 주소:

- Web: <http://127.0.0.1:8888>
- 모바일 H5: <http://127.0.0.1:8889>
- API 상태 확인: <http://127.0.0.1:5000/api/health>

## 문서 및 커뮤니티

- [영문 전체 README](README.md) / [중국어 간체 README](README_CN.md)
- [API 문서](docs/api/README.md) / [AI Agent 및 MCP](docs/agent/README.md)
- [전략 개발 가이드](docs/trading/STRATEGY_DEV_GUIDE.md) / [설치 문제 해결](docs/deployment/INSTALL_TROUBLESHOOTING.md)
- [Telegram](https://t.me/quantdinger) / [Discord](https://discord.com/invite/tyx5B6TChr) / [YouTube](https://youtube.com/@quantdinger)

## 라이선스

백엔드는 [Apache License 2.0](LICENSE)을 따릅니다. Web 프론트엔드와 모바일 클라이언트에는 각각 별도의 소스 공개 라이선스가 적용됩니다. 상표 및 브랜드 사용 조건은 [TRADEMARKS.md](TRADEMARKS.md)를 확인하세요.
