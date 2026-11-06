"""Golden Music 3.0 — i18n infrastructure (English + Persian).

Minimal tr() layer: ``tr("Library")`` looks up the active language table and
falls back to the key itself. Persian strings render RTL; the app sets the
QWidget.layoutDirection globally when fa is active.

Language tables intentionally cover the primary chrome (sidebar, pages,
player bar, common dialogs). Anything untranslated falls back to English —
extend FA_STRINGS as the UI grows.
"""
from __future__ import annotations

SUPPORTED = ("en", "fa")
_current = "en"


def set_language(lang: str) -> str:
    global _current
    lang = (lang or "en").lower()
    if lang not in SUPPORTED:
        lang = "en"
    _current = lang
    return _current


def current_language() -> str:
    return _current


def is_rtl() -> bool:
    return _current == "fa"


def tr(key: str) -> str:
    """Translate ``key`` (plain English string) into the active language."""
    if _current == "fa":
        return FA_STRINGS.get(key, key)
    return key


FA_STRINGS: dict[str, str] = {
    # navigation / sidebar
    "Library": "کتابخانه",
    "Favorites": "علاقه‌مندی‌ها",
    "Albums": "آلبوم‌ها",
    "Artists": "هنرمندان",
    "Genres": "سبک‌ها",
    "Playlists": "پلی‌لیست‌ها",
    "Radio": "رادیو",
    "Stats": "آمار",
    "Add Folder": "افزودن پوشه",
    "Rescan": "به‌روزرسانی",
    "Theme": "پوسته",
    "Settings": "تنظیمات",
    "Mini Player": "پخش‌کننده کوچک",
    "Command Palette": "پالت فرمان",
    "More": "بیشتر",
    "Duplicates": "موارد تکراری",
    "Backup / Restore": "پشتیبان‌گیری / بازیابی",
    "Export Library (M3U)": "خروجی کتابخانه (M3U)",
    # player bar / playback
    "Playing": "در حال پخش",
    "Paused": "متوقف",
    "Next": "بعدی",
    "Previous": "قبلی",
    "Play": "پخش",
    "Pause": "توقف",
    "Shuffle": "پخش تصادفی",
    "Repeat": "تکرار",
    "Volume": "بلندی صدا",
    "Now Playing": "در حال پخش",
    # search / lists
    "Search": "جستجو",
    "Search tracks…": "جستجوی آهنگ…",
    "Sort by": "مرتب‌سازی",
    "Title": "عنوان",
    "Artist": "هنرمند",
    "Album": "آلبوم",
    "Duration": "مدت",
    "Genre": "سبک",
    "Rating": "امتیاز",
    "Tracks": "آهنگ‌ها",
    "tracks": "آهنگ",
    # library actions
    "Add to Favorites": "افزودن به علاقه‌مندی‌ها",
    "Remove from Favorites": "حذف از علاقه‌مندی‌ها",
    "Add to Playlist": "افزودن به پلی‌لیست",
    "Play Next": "پخش بعدی",
    "Send to Playlist": "ارسال به پلی‌لیست",
    "Edit Tags": "ویرایش تگ‌ها",
    "Track Info": "اطلاعات آهنگ",
    "Show in Folder": "نمایش در پوشه",
    "Remove from Library": "حذف از کتابخانه",
    "Rate": "امتیازدهی",
    # lyrics
    "Lyrics": "متن آهنگ",
    "Find better": "یافتن بهتر",
    "Keep anyway": "همین بماند",
    "Edit Lyrics": "ویرایش متن آهنگ",
    "Save": "ذخیره",
    "Cancel": "انصراف",
    "Translation": "ترجمه",
    # settings categories
    "Appearance": "ظاهر",
    "Playback": "پخش",
    "Audio & EQ": "صدا و اکولایزر",
    "Integrations": "یکپارچه‌سازی‌ها",
    "Shortcuts": "کلیدهای میان‌بر",
    "Advanced": "پیشرفته",
    "About": "درباره",
    "OK": "تأیید",
    # stats
    "Your listening, visualized": "شنیدن شما، به تصویر کشیده شده",
    "Total listening time": "کل زمان شنیدن",
    "Sessions": "نشست‌ها",
    "Wrapped": "سالنامه",
    # misc
    "Loading library…": "بارگذاری کتابخانه…",
    "Empty library": "کتابخانه خالی",
    "No tracks yet": "هنوز آهنگی نیست",
    "Unknown Artist": "هنرمند ناشناس",
}
