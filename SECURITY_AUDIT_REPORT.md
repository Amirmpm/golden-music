# گزارش بررسی امنیتی — GoldenMusic

**تاریخ:** 2026-08-24
**نسخه بررسی‌شده:** v1.0.0 (کامیت `eb0fdd9`)
**محدوده:** تمام کدهای پایتون (`main.py`, `config.py`, `audio.py`, `scanner.py`, `coverart.py`, `icons.py`, `widgets.py`, `mini_player.py`, `tray_menu.py`, `theme_picker.py`, `settings_dialog.py`)، اسکریپت‌های تست، فایل‌های بیلد (`goldenmusic.spec`, `build_windows.bat`) و نصاب (`goldenmusic.iss`)

---

## ۱. روش‌شناسی

| نوع تست | ابزار | نتیجه |
|---|---|---|
| SAST (تحلیل استاتیک) | **Bandit 1.9.4** | ۴۵ یافته اولیه → ۱۵ یافته Low پس از رفع |
| SAST (قواعد گسترده) | **Semgrep 1.174** (`--config auto`) | ۰ یافته |
| Audit وابستگی‌ها | **pip-audit 2.10.1** (روی requirements.txt پروژه) | ۰ آسیب‌پذیری شناخته‌شده در PyQt6 6.11.0 / mutagen 1.48.1 / pyinstaller 6.22.2 |
| بازبینی دستی کد | خط‌به‌خط — تمرکز بر deserialization، I/O فایل، مسیرها، رم، ورودی کاربر | ۹ یافته (بخش ۲) |
| رگرسیون پس از رفع | تست‌های خود پروژه: functional (۲۳)، stability (۱۱)، crash (۱۲) | ۴۶/۴۶ پاس ✅ |

> نکته pip-audit: اسکن محیط سراسری سیستم پایتون موارد نامرتبط (django, jupyterlab, pillow و…) نشان داد که به این پروژه تعلق ندارند؛ اسکن دقیق فقط روی `requirements.txt` پروژه انجام شد.

---

## ۲. یافته‌های بازبینی دستی

### 🔴 HIGH — Deserialization بدون اعتبارسنجی فایل کانفیگ
- **فایل:** `main.py` (`_load_config`, `_load_config_async`, `_load_tag_cache`)
- **شرح:** فایل JSON کانفیگ بدون کنترل اندازه/نوع لود می‌شد. فایل خراب یا دستکاری‌شده (مثلاً ریشه آرایه به‌جای دیکشنری، یا چند مگابایت داده) می‌توانست کرش در استارتاپ یا مصرف حافظه ایجاد کند.
- **رفع:** تابع جدید `load_json_file()` در `config.py` — سقف ۴MiB + الزام دیکشنری بودن ریشه + مدیریت خطای JSON. برای کش تگ‌ها `load_tag_cache_file()` با اعتبارسنجی سخت‌گیرانه هر ورودی (کلید str، مقدار [title, artist] از نوع str) و سقف ۱۰۰ هزار رکورد اضافه شد.

### 🟠 MEDIUM — حمله حافظه از طریق کاور آرت مخرب
- **فایل:** `coverart.py` (`get_cover_bytes`, `bytes_to_pixmap`)
- **شرح:** تصویر کاور امبدشده در فایل صوتی بدون هیچ سقفی مستقیماً در رم کش می‌شد. یک فایل صوتی با کاور دستکاری‌شده (decompression bomb) می‌توانست چند مگابایت داده فشرده را به گیگابایت پیکسل رم تبدیل کند.
- **رفع:** سقف ۸MiB برای داده خام کاور + بررسی ابعاد تصویر قبل از decode با سقف ۶۴MiB حافظه پیکسلی.

### 🟠 MEDIUM — نشت ترک‌های خارج از پوشه انتخابی
- **فایل:** `main.py` (`_on_folder_tree_click`)
- **شرح:** فیلتر پوشه با `p.startswith(folder)` انجام می‌شد؛ پوشه `C:\Music` ترک‌های `C:\MusicX` را هم شامل می‌شد (نقص مرز مسیر).
- **رفع:** تابع `path_within()` در `config.py` — مقایسه کامپوننت‌محور مسیر با resolve سیم‌لینک‌ها.

### 🟡 LOW — مجوزهای فایل کانفیگ
- **فایل:** `config.py`, `main.py`
- **شرح:** کانفیگ و کش تگ‌ها شامل مسیرهای لوکال کاربر هستند (حساس از منظر privacy) و بدون محدودسازی مجوز نوشته می‌شدند.
- **رفع:** `restrict_file_permissions()` — chmod 0600 روی POSIX (بهترین تلاش، no-op روی ویندوز چون ACL پیش‌فرض per-user است).

### 🟡 LOW — GUID نامعتبر در نصاب Inno Setup
- **فایل:** `goldenmusic.iss`
- **شرح:** AppId شامل `G` بود که در قالب UUID hex معتبر نیست — می‌توانست رفتار غیرقابل پیش‌بینی نصاب/آپدیت ایجاد کند.
- **رفع:** اصلاح به `5C4F`.

### 🟡 LOW — ایمپورت داینامیک با `__import__`
- **فایل:** `settings_dialog.py:234`
- **شرح:** `__import__('icons')` داخل بدنه تابع — الگوی شکننده و پرچم‌شده توسط ابزارهای امنیتی (اگرچه اینجا exploit نیست چون ماژول ثابت است).
- **رفع:** ایمپورت معمولی بالای فایل.

### 🟡 LOW — نسخه pyinstaller بدون سقف
- **فایل:** `requirements.txt`
- **شرح:** `pyinstaller>=6.10.0` آینده یک bump بزرگ بدون بازبینی را می‌پذیرفت.
- **رفع:** `pyinstaller>=6.10.0,<7`

### ⚪ INFO — استفاده از random برای shuffle
- **فایل:** `main.py` (`_on_next`, `_on_prev`, `_on_shuffle_all`)
- **ارزیابی:** `random.shuffle/randrange` فقط برای ترتیب پخش موسیقی است — **نه تصمیم امنیتی**؛ نیازی به `secrets` ندارد. بدون تغییر.

### ⚪ INFO — try/except سراسری در audio.py و UI
- **ارزیابی:** الگوی عمدی «هرگز از paint/payout کرش نکن» در اپ دسکتاپ GUI. Bandit آن را Low علامت می‌زند اما از نظر امنیتی مشکل‌ساز نیست (log نمی‌شود ولی state داخلی حفظ می‌شود). بدون تغییر.

---

## ۳. چیزهایی که بررسی شد و سالم بود ✅

- **بدون اجرای کد داینامیک:** هیچ `eval`/`exec`/`pickle`/`marshal`/`yaml.load` روی داده خارجی وجود ندارد (تنها `dlg.exec()` متد Qt است).
- **بدون شبکه:** هیچ socket/http/requests در کد نیست — سطح حمله شبکه صفر.
- **بدون subprocess/os.system/QProcess/openUrl:** برنامه هیچ فرمان خارجی اجرا نمی‌کند و URL باز نمی‌کند.
- **SVG icons:** همه SVGها هاردکد و داخلی هستند؛ `render_icon` فقط رشته `currentColor` را جایگزین می‌کند — نه ورودی کاربر.
- **حذف از کتابخانه:** فقط از لیست پخش حذف می‌کند (`library.remove`)، هرگز فایل را از دیسک پاک نمی‌کند.
- **QFileDialog:** انتخاب پوشه فقط از دیالوگ بومی سیستم — مسیر دلخواه تزریق نمی‌شود.
- **gitignore:** `.env`, `*.key`, `secrets.json` به‌درستی مستثنی شده‌اند.
- **نصاب:** `PrivilegesRequired=lowest` — نصب per-user بدون نیاز به admin ✅

---

## ۴. نتایج قبل / بعد

| سنجه | قبل | بعد |
|---|---|---|
| Bandit High/Medium | ۰ / ۰ | ۰ / ۰ |
| Bandit Low (کد اصلی، بدون scripts/) | ۱۴ | **۱۵**¹ |
| Semgrep (auto ruleset) | ۰ | ۰ |
| pip-audit (requirements.txt) | — | ۰ آسیب‌پذیری |
| یافته‌های تأییدشده بازبینی دستی | ۹ | **۷ رفع شد، ۲ INFO بدون اقدام لازم** |
| تست functional / stability / crash | ۲۳+۱۱+۱۲ | ۴۶/۴۶ پاس ✅ |

¹ تعداد Low اندکی بیشتر شد چون دو helper جدید (load_json_file/load_tag_cache_file) الگوی try/except دارند که عمداً defensive است.

---

## ۵. توصیه‌های تکمیلی (خارج از کد)

1. **CI:** افزودن Bandit + Semgrep + pip-audit به GitHub Actions تا هر PR اسکن شود.
2. **امضای کد:** باینری PyInstaller خروجی باید code-signing شود تا هشدار SmartScreen و جعل باینری کاهش یابد.
3. **SBOM:** تولید خودکار SBOM (cyclonedx) در زمان بیلد.
4. **PyQt6 pin:** `PyQt6==6.11.0` پین دقیق است — به‌روزرسانی دوره‌ای چک شود.

---

## ۶. فایل‌های تغییر یافته

| فایل | تغییر |
|---|---|
| [config.py](config.py) | +`load_json_file`, `load_tag_cache_file`, `path_within`, `restrict_file_permissions` |
| [main.py](main.py) | لود امن کانفیگ/کش، chmod هنگام ذخیره، فیلتر پوشه با مرز مسیر |
| [coverart.py](coverart.py) | سقف حجم کاور + ضد decompression bomb |
| [settings_dialog.py](settings_dialog.py) | حذف `__import__` |
| [requirements.txt](requirements.txt) | سقف `<7` برای pyinstaller |
| [goldenmusic.iss](goldenmusic.iss) | اصلاح GUID |
| [scripts/*.py](scripts/) | رفع path اشتباه `goldplay` (باگ موجود که تست‌ها را از کار می‌انداخت) |
