<div align="center" dir="rtl">
  <a href="https://github.com/OpenByteInc/QuantDinger">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/logo-dark.png">
      <img src="docs/screenshots/logo.png" alt="شعار QuantDinger" width="420">
    </picture>
  </a>

  <h1>QuantDinger</h1>
  <p><strong>نظام تداول بالذكاء الاصطناعي مفتوح المصدر</strong></p>
  <p>منصة ذاتية الاستضافة لتحويل أفكار التداول إلى استراتيجيات Python واختبارات تاريخية وتداول تجريبي وتنفيذ حي ومراقبة.</p>
  <p><em>بحث بالذكاء الاصطناعي ← كود الاستراتيجية ← اختبار تاريخي ← تنفيذ تجريبي/حي ← مراقبة</em></p>

  <p dir="ltr">
    <a href="README.md">English</a> · <a href="README_CN.md">简体中文</a> · <a href="README_TW.md">繁體中文</a> ·
    <a href="README_JA.md">日本語</a> · <a href="README_KO.md">한국어</a> · <a href="README_DE.md">Deutsch</a> ·
    <a href="README_FR.md">Français</a> · <a href="README_RU.md">Русский</a> · <strong>العربية</strong> ·
    <a href="README_TH.md">ไทย</a> · <a href="README_VI.md">Tiếng Việt</a>
  </p>

  <p>
    <a href="https://ai.quantdinger.com"><strong>التطبيق المباشر</strong></a> ·
    <a href="https://www.quantdinger.com"><strong>الموقع الرسمي</strong></a> ·
    <a href="README.md"><strong>التوثيق الإنجليزي الكامل</strong></a>
  </p>

  <p dir="ltr">
    <a href="LICENSE"><img src="https://img.shields.io/badge/Backend-Apache%202.0-blue.svg?style=flat-square" alt="Apache 2.0"></a>
    <img src="docs/badges/python-3.12.svg" alt="Python 3.12">
    <img src="docs/badges/docker-compose.svg" alt="Docker Compose">
    <a href="https://github.com/OpenByteInc/QuantDinger/releases/latest"><img src="docs/badges/latest-release.svg" alt="أحدث إصدار"></a>
  </p>
</div>

> بعد تفعيل التداول الحي صراحةً، يستطيع QuantDinger إرسال أوامر حقيقية. ابدأ بالتداول التجريبي، وقيّد صلاحيات مفاتيح API، وراجع المتطلبات القانونية والتنظيمية في بلدك. لا يقدم هذا المشروع مشورة استثمارية.

## عرض المنتج

<p align="center">
  <img src="docs/screenshots/quantdinger-v5-2x.gif" alt="عرض QuantDinger" width="800">
</p>

## ما هو QuantDinger؟

QuantDinger هو **نظام تداول بالذكاء الاصطناعي مفتوح المصدر** للمتداولين المستقلين ومطوري استراتيجيات Python والفرق الصغيرة. يتيح التصميم المحلي وذاتي الاستضافة إبقاء بيانات السوق وكود الاستراتيجية وبيانات اعتماد الوسطاء وعملية النشر تحت سيطرة المشغّل.

## القدرات الأساسية

- أبحاث سوق عبر عدة مزودي ذكاء اصطناعي، وتقارير احترافية، ومجموعات أصول قابلة لإعادة الاستخدام
- Strategy API V2 لمؤشرات Python واستراتيجيات CTA والمحافظ
- اختبارات تاريخية على الخادم، وأبحاث عوامل، وتطوير الاستراتيجيات، وتحليل المتانة
- تنفيذ تجريبي وحي عبر منصات العملات الرقمية والوسطاء التقليديين
- Web وMobile H5 وHuman API وAgent Gateway وMCP
- تخزين دائم في PostgreSQL، وعمال موزعون، وسجلات تدقيق، ومراقبة اختيارية

QuantDinger ليس خدمة إشارات مغلقة؛ يظل كود الاستراتيجية وإعدادات المخاطر وبيانات الاعتماد والنشر تحت سيطرة المشغّل.

## البدء السريع

المتطلبات: Docker مع Compose v2. عند استخدام الصور الجاهزة لا تحتاج إلى تثبيت Node.js أو بيئة Python محلية.

Linux / macOS:

```bash
curl -fsSL https://raw.githubusercontent.com/OpenByteInc/QuantDinger/main/install.sh | bash
```

Windows PowerShell:

```powershell
irm https://raw.githubusercontent.com/OpenByteInc/QuantDinger/main/install.ps1 | iex
```

بعد التثبيت:

- Web: <http://127.0.0.1:8888>
- Mobile H5: <http://127.0.0.1:8889>
- فحص API: <http://127.0.0.1:5000/api/health>

## التوثيق والمجتمع

- [README الإنجليزي الكامل](README.md) / [README بالصينية المبسطة](README_CN.md)
- [توثيق API](docs/api/README.md) / [وكلاء الذكاء الاصطناعي وMCP](docs/agent/README.md)
- [دليل تطوير الاستراتيجيات](docs/trading/STRATEGY_DEV_GUIDE.md) / [حل مشكلات التثبيت](docs/deployment/INSTALL_TROUBLESHOOTING.md)
- [Telegram](https://t.me/quantdinger) / [Discord](https://discord.com/invite/tyx5B6TChr) / [YouTube](https://youtube.com/@quantdinger)

## الترخيص

الواجهة الخلفية مرخّصة وفق [Apache License 2.0](LICENSE). للواجهة Web والعميل المحمول تراخيص منفصلة تتيح الاطلاع على المصدر. راجع [TRADEMARKS.md](TRADEMARKS.md) لشروط استخدام العلامة التجارية والهوية البصرية.
