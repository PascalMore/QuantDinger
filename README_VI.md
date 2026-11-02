<div align="center">
  <a href="https://github.com/OpenByteInc/QuantDinger">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/logo-dark.png">
      <img src="docs/screenshots/logo.png" alt="Logo QuantDinger" width="420">
    </picture>
  </a>

  <h1>QuantDinger</h1>
  <p><strong>Hệ điều hành giao dịch AI mã nguồn mở</strong></p>
  <p>Nền tảng tự lưu trữ giúp chuyển ý tưởng giao dịch thành chiến lược Python, backtest, giao dịch mô phỏng, khớp lệnh thật và giám sát.</p>
  <p><em>Nghiên cứu AI → Mã chiến lược → Backtest → Giao dịch mô phỏng/thật → Giám sát</em></p>

  <p>
    <a href="README.md">English</a> · <a href="README_CN.md">简体中文</a> · <a href="README_TW.md">繁體中文</a> ·
    <a href="README_JA.md">日本語</a> · <a href="README_KO.md">한국어</a> · <a href="README_DE.md">Deutsch</a> ·
    <a href="README_FR.md">Français</a> · <a href="README_RU.md">Русский</a> · <a href="README_AR.md">العربية</a> ·
    <a href="README_TH.md">ไทย</a> · <strong>Tiếng Việt</strong>
  </p>

  <p>
    <a href="https://ai.quantdinger.com"><strong>Ứng dụng trực tuyến</strong></a> ·
    <a href="https://www.quantdinger.com"><strong>Trang web chính thức</strong></a> ·
    <a href="README.md"><strong>Tài liệu tiếng Anh đầy đủ</strong></a>
  </p>

  <p>
    <a href="LICENSE"><img src="https://img.shields.io/badge/Backend-Apache%202.0-blue.svg?style=flat-square" alt="Apache 2.0"></a>
    <img src="docs/badges/python-3.12.svg" alt="Python 3.12">
    <img src="docs/badges/docker-compose.svg" alt="Docker Compose">
    <a href="https://github.com/OpenByteInc/QuantDinger/releases/latest"><img src="docs/badges/latest-release.svg" alt="Bản phát hành mới nhất"></a>
  </p>
</div>

> Khi giao dịch thật được bật rõ ràng, QuantDinger có thể gửi lệnh thật. Hãy bắt đầu bằng giao dịch mô phỏng, giới hạn quyền của API Key và kiểm tra các yêu cầu pháp lý cũng như rủi ro tại nơi bạn sinh sống. Dự án này không cung cấp lời khuyên đầu tư.

## Bản trình diễn

<p align="center">
  <img src="docs/screenshots/quantdinger-v5-2x.gif" alt="Bản trình diễn QuantDinger" width="800">
</p>

## QuantDinger là gì?

QuantDinger là **hệ điều hành giao dịch AI mã nguồn mở** dành cho nhà giao dịch độc lập, nhà phát triển chiến lược Python và các nhóm nhỏ. Thiết kế local-first, tự lưu trữ giúp dữ liệu thị trường, mã chiến lược, thông tin đăng nhập môi giới và việc triển khai luôn nằm dưới quyền kiểm soát của người vận hành.

## Khả năng cốt lõi

- Nghiên cứu thị trường với nhiều nhà cung cấp AI, báo cáo chuyên nghiệp và danh mục tài sản tái sử dụng
- Strategy API V2 cho chỉ báo Python, chiến lược CTA và danh mục đầu tư
- Backtest phía máy chủ, nghiên cứu nhân tố, tiến hóa chiến lược và phân tích độ bền
- Khớp lệnh mô phỏng và thật trên sàn tiền mã hóa lẫn môi giới truyền thống
- Web, Mobile H5, Human API, Agent Gateway và MCP
- Lưu trữ PostgreSQL, worker phân tán, nhật ký kiểm toán và giám sát tùy chọn

QuantDinger không phải dịch vụ tín hiệu hộp đen. Người vận hành kiểm soát mã chiến lược, cài đặt rủi ro, thông tin đăng nhập và cách triển khai.

## Bắt đầu nhanh

Yêu cầu: Docker với Compose v2. Khi dùng image dựng sẵn, bạn không cần cài Node.js hay môi trường Python cục bộ.

Linux / macOS:

```bash
curl -fsSL https://raw.githubusercontent.com/OpenByteInc/QuantDinger/main/install.sh | bash
```

Windows PowerShell:

```powershell
irm https://raw.githubusercontent.com/OpenByteInc/QuantDinger/main/install.ps1 | iex
```

Sau khi cài đặt:

- Web: <http://127.0.0.1:8888>
- Mobile H5: <http://127.0.0.1:8889>
- Kiểm tra API: <http://127.0.0.1:5000/api/health>

## Tài liệu và cộng đồng

- [README tiếng Anh đầy đủ](README.md) / [README tiếng Trung](README_CN.md)
- [Tài liệu API](docs/api/README.md) / [AI Agent và MCP](docs/agent/README.md)
- [Hướng dẫn phát triển chiến lược](docs/trading/STRATEGY_DEV_GUIDE.md) / [Khắc phục lỗi cài đặt](docs/deployment/INSTALL_TROUBLESHOOTING.md)
- [Telegram](https://t.me/quantdinger) / [Discord](https://discord.com/invite/tyx5B6TChr) / [YouTube](https://youtube.com/@quantdinger)

## Giấy phép

Backend sử dụng [Apache License 2.0](LICENSE). Web frontend và mobile client có giấy phép source-available riêng. Xem [TRADEMARKS.md](TRADEMARKS.md) để biết điều kiện sử dụng nhãn hiệu và thương hiệu.
