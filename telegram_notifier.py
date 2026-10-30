import datetime
import logging
from typing import Any, Dict, Optional
import requests

logger = logging.getLogger("telegram_notifier")


def send_telegram_message(bot_token: str, chat_id: str, text: str) -> bool:
  if not bot_token or not chat_id:
    return False
  url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
  payload = {
      "chat_id": chat_id,
      "text": text,
      "parse_mode": "HTML",
      "disable_web_page_preview": True,
  }
  try:
    resp = requests.post(url, json=payload, timeout=10)
    data = resp.json()
    if resp.status_code == 200 and data.get("ok"):
      logger.info(f"Telegram message sent to {chat_id}")
      return True
    else:
      logger.error(f"Failed to send Telegram message: {data}")
      return False
  except Exception as e:
    logger.error(f"Telegram network error: {e}")
    return False


def test_telegram_connection(bot_token: str, chat_id: str) -> Dict[str, Any]:
  if not bot_token or not chat_id:
    return {
        "success": False,
        "error": "يرجى إدخال Bot Token و Chat ID أولاً في الإعدادات",
    }
  msg = (
      "<b>🔔 تجربة اتصال بوت تيليجرام - نظام متابعة خطوط WE</b>\n\n"
      "✅ تم ربط البوت بنجاح مع لوحة المتابعة!\n"
      f"📅 التاريخ: {datetime.datetime.now().strftime('%Y/%m/%d %I:%M %p')}\n\n"
      "سيتم إرسال إشعارات تلقائية فورية عند انخفاض الجيجات أو اقتراب موعد"
      " التجديد."
  )
  ok = send_telegram_message(bot_token, chat_id, msg)
  if ok:
    return {
        "success": True,
        "message": "تم إرسال رسالة التجربة بنجاح إلى حساب التيليجرام!",
    }
  else:
    return {
        "success": False,
        "error": (
            "فشل إرسال الرسالة. يرجى التأكد من صحة Bot Token و Chat ID، وأنك"
            " بدأت المحادثة مع البوت عبر إرسال /start."
        ),
    }


def check_and_send_alerts(
    line: Dict[str, Any], settings: Dict[str, Any]
) -> Optional[str]:
  """Checks if a line quota has crossed thresholds and sends Telegram alerts if enabled."""
  if not settings.get("telegram_enabled"):
    return None

  bot_token = settings.get("telegram_bot_token")
  chat_id = settings.get("telegram_chat_id")
  if not bot_token or not chat_id:
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

  # Anti-spam check (send at most once every 18 hours per alert type)
  last_alerts = settings.get("last_alerts_sent", {})
  now_ts = datetime.datetime.now().timestamp()

  alert_type = None
  alert_title = ""
  alert_details = ""

  # 1. Critical Low Quota Check
  if remain_percent <= thresh_percent or remain_gb <= thresh_gb:
    alert_type = "low_quota"
    alert_title = f"⚠️ <b>تحذير: باقة الإنترنت أوشكت على الانتهاء!</b>"
    alert_details = (
        f"المتبقي فقط: <b>{remain_gb} جيجا</b> من أصل {total_gb} جيجا"
        f" ({remain_percent}%)\n"
    )

  # 2. Expiry Date Check
  elif days_left <= thresh_days:
    alert_type = "expiring_soon"
    alert_title = f"📅 <b>تنبيه: اقتراب موعد تجديد باقة الخط!</b>"
    alert_details = (
        f"المتبقي على التجديد: <b>{days_left} يوم فقط</b>\nتاريخ التجديد:"
        f" <b>{expire_date}</b>\n"
    )

  if alert_type:
    alert_key = f"{line_id}_{alert_type}"
    last_sent = last_alerts.get(alert_key, 0)
    # 18 hours cooldown (64800 seconds)
    if now_ts - last_sent > 64800:
      message = (
          f"{alert_title}\n\n"
          f"🏢 <b>الخط / الفرع:</b> {label}\n"
          f"☎️ <b>رقم التليفون:</b> <code>{landline}</code>\n"
          f"{alert_details}"
          f"📊 <b>الاستهلاك:</b> {last_data.get('used_gb')} جيجا مستهلكة\n"
          f"⏰ <b>تاريخ الفحص:</b> {last_data.get('last_updated')}\n"
      )
      if send_telegram_message(bot_token, chat_id, message):
        last_alerts[alert_key] = now_ts
        from storage import storage

        storage.update_settings({"last_alerts_sent": last_alerts})
        return alert_type

  return None
