#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
═══════════════════════════════════════════════════════════
ShipBoard Pro — فاحص الوصولات القادمة وإرسال تنبيهات واتساب
═══════════════════════════════════════════════════════════
يعمل تلقائياً عبر GitHub Actions كل ساعة.
يقرأ Google Sheet، يفحص الحاويات المتبقي عليها ٧ أيام بالضبط،
ويرسل تنبيه WhatsApp عبر CallMeBot لكل رقم مُسجَّل.
"""

import csv
import io
import os
import sys
import urllib.request
import urllib.parse
from datetime import datetime, date

# ══════════════════════════════════════════
# الإعدادات
# ══════════════════════════════════════════
SHEET_CSV_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vSY5DZRuPmHL3NJsJAYPMOYgwQQnAcGjWT4WpMaXgiUgG8iHJg2u3ZAsOTChXyQ0g/pub?gid=428520260&single=true&output=csv"

# عدد الأيام المتبقية التي تُرسل عندها التنبيهات (يمكن تعديل القائمة)
ALERT_DAYS = [7]  # تنبيه عند ٧ أيام بالضبط

# قائمة الأرقام وAPI Keys — كل رقم له مفتاحه الخاص من CallMeBot
WHATSAPP_RECIPIENTS = [
    # {"phone": "967775949489", "apikey": "8984853"},
]

# ══════════════════════════════════════════
# تحميل المتلقين من متغيرات البيئة (GitHub Secrets)
# ══════════════════════════════════════════
def load_recipients_from_env():
    """
    يقرأ المتلقين من متغير بيئة بصيغة:
    WHATSAPP_RECIPIENTS = "phone1:apikey1,phone2:apikey2,phone3:apikey3"
    """
    recipients = []
    env_val = os.environ.get("WHATSAPP_RECIPIENTS", "")
    if env_val:
        for pair in env_val.split(","):
            pair = pair.strip()
            if ":" in pair:
                phone, apikey = pair.split(":", 1)
                recipients.append({"phone": phone.strip(), "apikey": apikey.strip()})
    return recipients or WHATSAPP_RECIPIENTS


# ══════════════════════════════════════════
# تحميل بيانات الجدول
# ══════════════════════════════════════════
def fetch_sheet_data(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        raw = resp.read().decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(raw)))


# ══════════════════════════════════════════
# استخراج القيمة من صف بحسب اسم العمود (مع مرونة)
# ══════════════════════════════════════════
def get_field(row, *names):
    for name in names:
        for key in row.keys():
            if key.strip() == name.strip():
                val = row[key].strip()
                if val:
                    return val
    return ""


# ══════════════════════════════════════════
# تطبيع حالة الشحنة (لتجاهل الحاويات الواصلة)
# ══════════════════════════════════════════
ARRIVED_KEYWORDS = ["وصلت", "تم الاستلام", "استلمت", "مستلمة", "arrived", "delivered", "received"]

def is_arrived(status):
    s = (status or "").strip().lower()
    return any(k in s for k in ARRIVED_KEYWORDS)


# ══════════════════════════════════════════
# حساب الأيام المتبقية من تاريخ الوصول المتوقع
# ══════════════════════════════════════════
def days_remaining_from_date(eta_str):
    """يحسب الأيام المتبقية من تاريخ الوصول المتوقع."""
    if not eta_str:
        return None
    eta_str = eta_str.strip()
    fmt_list = ["%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%Y/%m/%d", "%d.%m.%Y"]
    for fmt in fmt_list:
        try:
            eta_date = datetime.strptime(eta_str, fmt).date()
            return (eta_date - date.today()).days
        except ValueError:
            continue
    return None


def get_remaining_days(row):
    """
    يحدد الأيام المتبقية بأولوية:
    1. عمود "الأيام المتبقية" إن وُجد رقم صحيح فيه
    2. حساب من "تاريخ الوصول المتوقع"
    """
    raw = get_field(row, "الأيام المتبقية")
    if raw:
        try:
            # تنظيف من أي رموز غير رقمية (مثل "٧" عربي أو فراغات)
            cleaned = raw.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
            cleaned = "".join(ch for ch in cleaned if ch.isdigit() or ch == "-")
            if cleaned:
                return int(cleaned)
        except (ValueError, TypeError):
            pass

    eta = get_field(row, "تاريخ الوصول المتوقع")
    return days_remaining_from_date(eta)


# ══════════════════════════════════════════
# إرسال رسالة واتساب عبر CallMeBot
# ══════════════════════════════════════════
def send_whatsapp(phone, apikey, message):
    base_url = "https://api.callmebot.com/whatsapp.php"
    params = {"phone": phone, "text": message, "apikey": apikey}
    url = base_url + "?" + urllib.parse.urlencode(params)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            result = resp.read().decode("utf-8", errors="ignore")
            print(f"  ✓ أُرسلت إلى {phone}: {result[:80]}")
            return True
    except Exception as e:
        print(f"  ✗ فشل الإرسال إلى {phone}: {e}")
        return False


# ══════════════════════════════════════════
# بناء نص رسالة التنبيه (بدون السعر/قيمة الفاتورة)
# ══════════════════════════════════════════
def build_message(row, remaining):
    invoice   = get_field(row, "رقم الفاتورة")
    supplier  = get_field(row, "المورد")
    currency  = get_field(row, "العملة")  # لن تُستخدم — تجاهل
    bl        = get_field(row, "رقم البوليسة BL")
    container = get_field(row, "رقم الحاوية")
    eta       = get_field(row, "تاريخ الوصول المتوقع")
    status    = get_field(row, "حالة الشحنة")
    notes     = get_field(row, "ملاحظات")
    entry     = get_field(row, "تاريخ الإدخال")

    lines = []
    lines.append("🚢 *تنبيه: حاوية تصل بعد 7 أيام*")
    lines.append("─────────────────")
    lines.append(f"📄 رقم الفاتورة: {invoice or '—'}")
    lines.append(f"🏢 المورد: {supplier or '—'}")
    lines.append(f"📦 رقم الحاوية: {container or '—'}")
    lines.append(f"🧾 رقم البوليصة BL: {bl or '—'}")
    lines.append(f"📅 تاريخ الإدخال: {entry or '—'}")
    lines.append(f"🛳️ تاريخ الوصول المتوقع: {eta or '—'}")
    lines.append(f"⏳ الأيام المتبقية: {remaining} يوم")
    lines.append(f"📍 الحالة الحالية: {status or '—'}")
    if notes:
        lines.append(f"📝 ملاحظات: {notes}")
    lines.append("─────────────────")
    lines.append("ShipBoard Pro 🔔")

    return "\n".join(lines)


# ══════════════════════════════════════════
# البرنامج الرئيسي
# ══════════════════════════════════════════
def main():
    print("═══════════════════════════════════════")
    print("ShipBoard Pro — فاحص الوصولات")
    print(f"التاريخ: {date.today().isoformat()}")
    print("═══════════════════════════════════════")

    recipients = load_recipients_from_env()
    if not recipients:
        print("⚠️ لا يوجد متلقين مُعرَّفين. أضف WHATSAPP_RECIPIENTS في GitHub Secrets.")
        sys.exit(0)

    print(f"عدد المتلقين: {len(recipients)}")
    print(f"أيام التنبيه: {ALERT_DAYS}")
    print("جارٍ تحميل بيانات الجدول...")

    try:
        rows = fetch_sheet_data(SHEET_CSV_URL)
    except Exception as e:
        print(f"✗ فشل تحميل الجدول: {e}")
        sys.exit(1)

    print(f"✓ تم تحميل {len(rows)} سجل")

    alerts_sent = 0
    debug = os.environ.get("DEBUG", "").lower() in ("1", "true", "yes")

    for row in rows:
        status = get_field(row, "حالة الشحنة")
        container = get_field(row, "رقم الحاوية") or get_field(row, "رقم الفاتورة")

        if is_arrived(status):
            if debug:
                print(f"  ⏭️  {container}: تم تجاهلها (الحالة: {status})")
            continue  # تجاهل الحاويات الواصلة

        remaining = get_remaining_days(row)

        if debug:
            raw_remaining_col = get_field(row, "الأيام المتبقية")
            raw_eta = get_field(row, "تاريخ الوصول المتوقع")
            print(f"  📦 {container}: الحالة='{status}' | عمود الأيام المتبقية='{raw_remaining_col}' | تاريخ الوصول='{raw_eta}' | المحسوب={remaining}")

        if remaining is None:
            continue

        if remaining in ALERT_DAYS:
            print(f"\n🔔 تنبيه: حاوية {container} متبقي عليها {remaining} يوم")

            message = build_message(row, remaining)

            for r in recipients:
                send_whatsapp(r["phone"], r["apikey"], message)
                alerts_sent += 1

    print("\n═══════════════════════════════════════")
    print(f"اكتمل الفحص. عدد التنبيهات المُرسلة: {alerts_sent}")
    print("═══════════════════════════════════════")


if __name__ == "__main__":
    main()
