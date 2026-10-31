import datetime
import logging
import os
import subprocess
import urllib.parse
from typing import Any, Dict, Optional
import requests

logger = logging.getLogger("whatsapp_notifier")

WA_SERVICE_URL = os.environ.get("WA_SERVICE_URL", "http://127.0.0.1:3005")


def is_service_alive() -> bool:
  try:
    r = requests.get(f"{WA_SERVICE_URL}/api/status", timeout=1.5)
    return r.status_code == 200
  except Exception:
    return False


def ensure_whatsapp_service():
  """Checks if local Node.js Baileys WhatsApp service is running; if not, attempts to spawn it."""
  if is_service_alive():
    return True

  service_path = os.path.join(
      os.path.dirname(os.path.abspath(__file__)), "whatsapp_service.js"
  )
  if not os.path.exists(service_path):
    logger.warning(f"whatsapp_service.js not found at {service_path}")
    return False

  try:
    logger.info("Attempting to auto-start whatsapp_service.js in background...")
    # Spawn detached process
    if os.name == "nt":
      subprocess.Popen(
          ["node", service_path],
          creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
          | subprocess.DETACHED_PROCESS,
          shell=False,
          close_fds=True,
      )
    else:
      subprocess.Popen(
          ["node", service_path],
          stdout=subprocess.DEVNULL,
          stderr=subprocess.DEVNULL,
          start_new_session=True,
          close_fds=True,
      )
    return True
  except Exception as e:
    logger.warning(f"Failed to auto-spawn whatsapp_service.js: {e}")
    return False


def get_whatsapp_status(settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
  """Returns current connection status of WhatsApp microservice or gateway."""
  provider = (settings or {}).get("whatsapp_provider", "baileys")
  default_phone = (settings or {}).get("whatsapp_default_phone", "")

  if provider == "callmebot":
    has_key = bool((settings or {}).get("whatsapp_callmebot_apikey"))
    return {
        "success": True,
        "provider": "callmebot",
        "connected": has_key,
        "status": "connected" if has_key else "not_configured",
        "phone": (settings or {}).get("whatsapp_callmebot_phone", default_phone),
        "message": "بوابة CallMeBot جاهزة للعمل" if has_key else "يرجى إدخال API Key",
    }

  # Baileys native microservice
  try:
    r = requests.get(f"{WA_SERVICE_URL}/api/status", timeout=2.0)
    data = r.json()
    data["provider"] = "baileys"
    return data
  except Exception as e:
    # Try auto-starting if service is down
    ensure_whatsapp_service()
    return {
        "success": False,
        "provider": "baileys",
        "connected": False,
        "status": "service_down",
        "error": f"خدمة واتساب المحلية غير نشطة حالياً: {e}",
    }


def get_whatsapp_qr() -> Dict[str, Any]:
  """Gets current QR code Data URL for linking."""
  ensure_whatsapp_service()
  try:
    r = requests.get(f"{WA_SERVICE_URL}/api/qr", timeout=3.0)
    return r.json()
  except Exception as e:
    return {"success": False, "error": f"فشل جلب رمز QR: {e}"}


def request_whatsapp_pairing(phone: str) -> Dict[str, Any]:
  """Requests an 8-character pairing code for linking WhatsApp without camera."""
  ensure_whatsapp_service()
  try:
    r = requests.post(
        f"{WA_SERVICE_URL}/api/pair", json={"phone": phone}, timeout=15.0
    )
    return r.json()
  except Exception as e:
    return {"success": False, "error": f"فشل طلب كود الاقتران: {e}"}


def disconnect_whatsapp() -> Dict[str, Any]:
  """Logs out and wipes saved credentials."""
  try:
    r = requests.post(f"{WA_SERVICE_URL}/api/logout", timeout=5.0)
    return r.json()
  except Exception as e:
    return {"success": False, "error": f"فشل تسجيل الخروج: {e}"}


def send_whatsapp_message(
    to: str, message: str, settings: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
  """Sends a WhatsApp text message using Baileys or CallMeBot."""
  s = settings or {}
  provider = s.get("whatsapp_provider", "baileys")

  clean_to = str(to).strip().replace(" ", "").replace("-", "")
  if not clean_to:
    return {"success": False, "error": "يرجى تحديد رقم الهاتف المستلم"}

  # Mode 1: CallMeBot
  if provider == "callmebot":
    phone = clean_to or s.get("whatsapp_callmebot_phone")
    apikey = s.get("whatsapp_callmebot_apikey")
    if not phone or not apikey:
      return {
          "success": False,
          "error": "يرجى ضبط رقم الهاتف و API Key الخاص بـ CallMeBot",
      }

    encoded_text = urllib.parse.quote(message)
    url = f"https://api.callmebot.com/whatsapp.php?phone={phone}&text={encoded_text}&apikey={apikey}"
    try:
      resp = requests.get(url, timeout=12)
      if resp.status_code == 200:
        return {"success": True, "message": "تم إرسال الرسالة عبر CallMeBot"}
      else:
        return {
            "success": False,
            "error": f"خطأ من CallMeBot ({resp.status_code}): {resp.text}",
        }
    except Exception as e:
      return {"success": False, "error": f"فشل الاتصال بـ CallMeBot: {e}"}

  # Mode 2: Baileys Microservice (Default)
  ensure_whatsapp_service()
  try:
    resp = requests.post(
        f"{WA_SERVICE_URL}/api/send",
        json={"to": clean_to, "message": message},
        timeout=15,
    )
    data = resp.json()
    if resp.status_code == 200 and data.get("success"):
      return {"success": True, "message": "تم إرسال الرسالة بنجاح عبر واتساب"}
    else:
      return {
          "success": False,
          "error": data.get("error", "فشل إرسال رسالة الواتساب"),
      }
  except requests.exceptions.ConnectionError:
    return {
        "success": False,
        "error": (
            "خدمة واتساب المحلية غير قيد التشغيل. يرجى التأكد من تشغيلها وربط"
            " الحساب."
        ),
    }
  except Exception as e:
    return {"success": False, "error": f"خطأ أثناء الإرسال: {e}"}


def test_whatsapp_connection(
    to: str, settings: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
  """Sends a test WhatsApp message to verify integration."""
  now_str = datetime.datetime.now().strftime("%Y/%m/%d %I:%M %p")
  test_msg = (
      "🔔 *تجربة اتصال واتساب - نظام متابعة خطوط WE*\n\n"
      "✅ *تم ربط خدمة الواتساب بنجاح مع لوحة المتابعة!*\n"
      f"📅 *التاريخ:* {now_str}\n\n"
      "سيتم إرسال إشعارات تلقائية فورية لحالة الخطوط عند اقتراب انتهاء الجيجات"
      " أو موعد تجديد الباقة."
  )
  return send_whatsapp_message(to, test_msg, settings)


def build_line_whatsapp_summary(line: Dict[str, Any]) -> str:
  """Builds a comprehensive, nicely-formatted WhatsApp report message for a single line."""
  raw = line.get("last_data")
  q = (raw.get("data") if (raw and isinstance(raw, dict)) else None) or raw or {}

  landline = line.get("landline", "غير معروف")
  customer_name = line.get("customer_name") or q.get(
      "customer_name", "مشترك وي"
  )
  label = line.get("label", "بدون مسمى")

  remain_gb = q.get("remain_gb", 0)
  total_gb = q.get("total_gb", 0)
  used_gb = q.get("used_gb", 0)
  remain_pct = q.get("remain_percent", 0)
  expire_date = q.get("expire_date", "غير محدد")
  days_left = q.get("days_left", 0)
  offer_name = q.get("offer_name", "باقة وي إنترنت")
  last_updated = q.get(
      "last_updated",
      datetime.datetime.now().strftime("%Y/%m/%d %I:%M %p"),
  )

  # Countdown text
  if days_left <= 0:
    cd_text = "تجديد اليوم (مستحق الآن) ⚠️"
  elif days_left == 1:
    cd_text = "ينتهي غداً ⏳"
  elif days_left == 2:
    cd_text = "متبقي يومان"
  else:
    cd_text = f"متبقي {days_left} يوم"

  # Balance & Points
  balance = line.get("balance") or q.get("balance")
  points = line.get("points") or q.get("points")
  extra_info = ""
  if balance is not None and balance != "":
    extra_info += f"💰 *الرصيد الحالي:* {balance} ج.م\n"
  if points is not None and points != "":
    extra_info += f"🎁 *نقاط 4U:* {points} نقطة\n"

  # Topup Bundles
  topups = q.get("topup_bundles") or []
  topup_section = ""
  if topups:
    topup_section = f"📦 *باقات إضافية نشطة ({len(topups)}):*\n"
    for idx, tb in enumerate(topups, 1):
      t_rem = tb.get("current_amount", 0)
      t_tot = tb.get("initial_amount", 0)
      topup_section += (
          f"  ▪️ باقة {idx}: {t_rem} / {t_tot} GB (تنتهي {tb.get('expire_date')})\n"
      )

  msg = (
      f"📋 *تقرير استهلاك خط WE الأرضي*\n\n"
      f"👤 *صاحب الخط:* {customer_name}\n"
      f"🏢 *المسمى / الفرع:* {label}\n"
      f"☎️ *رقم التليفون:* `{landline}`\n"
      f"📦 *الباقة الأساسية:* {offer_name}\n\n"
      f"📊 *الاستهلاك والمتبقي:*\n"
      f"▪️ المتبقي: *{remain_gb} GB* ({remain_pct}%)\n"
      f"▪️ المستهلك: *{used_gb} GB*\n"
      f"▪️ السعة الكلية: *{total_gb} GB*\n\n"
      f"⏳ *موعد التجديد:* {expire_date}\n"
      f"⏱️ *حالة الصلاحية:* *{cd_text}*\n"
      f"{extra_info}"
      f"{topup_section}\n"
      f"⏰ _تاريخ الفحص: {last_updated}_\n"
      f"_تم الإرسال تلقائياً من نظام WE Quota Monitor_"
  )
  return msg


def check_and_send_whatsapp_alerts(
    line: Dict[str, Any], settings: Dict[str, Any]
) -> Optional[str]:
  """Checks line against alert rules and sends WhatsApp alert if threshold crossed."""
  if not settings.get("whatsapp_enabled"):
    return None

  # Check if line specifically disabled WhatsApp notifications
  if line.get("whatsapp_enabled") is False:
    return None

  # Determine recipient phone
  recipient = line.get("whatsapp_recipient") or settings.get(
      "whatsapp_default_phone"
  )
  if not recipient:
    return None

  last_data = line.get("last_data")
  if not last_data or not last_data.get("success"):
    return None

  line_id = line["id"]
  label = line.get("label", line.get("landline"))
  landline = line.get("landline")
  remain_gb = last_data.get("remain_gb", 0)
  total_gb = last_data.get("total_gb", 0)
  remain_percent = last_data.get("remain_percent", 0)
  days_left = last_data.get("days_left", 999)
  expire_date = last_data.get("expire_date", "")

  thresh_percent = settings.get("alert_threshold_percent", 20)
  thresh_gb = settings.get("alert_threshold_gb", 15)
  thresh_days = settings.get("alert_days_left", 3)

  alert_on_low_quota = settings.get("whatsapp_alert_on_low_quota", True)
  alert_on_renewal = settings.get("whatsapp_alert_on_renewal", True)

  # Anti-spam check (send at most once every 18 hours per alert type per line)
  last_alerts = settings.get("last_whatsapp_alerts_sent", {})
  now_ts = datetime.datetime.now().timestamp()

  alert_type = None
  alert_title = ""
  alert_details = ""

  # 1. Critical Low Quota Check
  if alert_on_low_quota and (
      remain_percent <= thresh_percent or remain_gb <= thresh_gb
  ):
    alert_type = "low_quota"
    alert_title = "⚠️ *تحذير: باقة الإنترنت أوشكت على الانتهاء!*"
    alert_details = (
        f"المتبقي فقط: *{remain_gb} GB* من أصل {total_gb} GB"
        f" ({remain_percent}%)\n"
    )

  # 2. Expiry Date Check
  elif alert_on_renewal and (days_left <= thresh_days):
    alert_type = "expiring_soon"
    alert_title = "📅 *تنبيه: اقتراب موعد تجديد باقة الخط!*"
    if days_left <= 0:
      time_msg = "*اليوم (مستحق الآن)*"
    elif days_left == 1:
      time_msg = "*غداً*"
    else:
      time_msg = f"*{days_left} يوم فقط*"
    alert_details = (
        f"المتبقي على التجديد: {time_msg}\nتاريخ التجديد: *{expire_date}*\n"
    )

  if alert_type:
    alert_key = f"{line_id}_{alert_type}"
    last_sent = last_alerts.get(alert_key, 0)
    # 18 hours cooldown (64800 seconds)
    if now_ts - last_sent > 64800:
      message = (
          f"{alert_title}\n\n"
          f"🏢 *الخط / الفرع:* {label}\n"
          f"☎️ *رقم التليفون:* `{landline}`\n"
          f"{alert_details}"
          f"📊 *الاستهلاك:* {last_data.get('used_gb', 0)} GB مستهلكة\n"
          f"📦 *المتبقي الكلي:* {remain_gb} GB\n"
          f"⏰ *تاريخ الفحص:* {last_data.get('last_updated')}\n\n"
          f"_تم إرسال هذا الإشعار تلقائياً من نظام WE Quota Monitor_"
      )
      res = send_whatsapp_message(recipient, message, settings)
      if res.get("success"):
        last_alerts[alert_key] = now_ts
        from storage import storage

        storage.update_settings({"last_whatsapp_alerts_sent": last_alerts})
        logger.info(
            f"WhatsApp alert [{alert_type}] sent successfully to {recipient} for"
            f" line {landline}"
        )
        return alert_type
      else:
        logger.error(
            f"Failed to send WhatsApp alert for line {landline}:"
            f" {res.get('error')}"
        )

  return None
