import datetime
import hashlib
import json
import logging
import os
import secrets
import uuid
from typing import Any, Dict, List, Optional

logger = logging.getLogger("storage")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
LINES_FILE = os.path.join(DATA_DIR, "lines.json")
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")
SESSIONS_FILE = os.path.join(DATA_DIR, "sessions.json")


def hash_password(password: str) -> str:
  return hashlib.sha256(password.encode("utf-8")).hexdigest()


DEFAULT_SETTINGS = {
    "telegram_bot_token": "",
    "telegram_chat_id": "",
    "telegram_enabled": False,
    "alert_threshold_percent": 20,
    "alert_threshold_gb": 15,
    "alert_days_left": 3,
    "auto_refresh_hours": 1,
    "auto_refresh_minutes": 30,
    "dark_mode": True,
    "last_alerts_sent": {},
    "admin_username": "admin",
    "admin_password_hash": hash_password("admin"),
}


class Storage:

  def __init__(self):
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(LINES_FILE):
      self._save_json(LINES_FILE, [])
    if not os.path.exists(SETTINGS_FILE):
      self._save_json(SETTINGS_FILE, DEFAULT_SETTINGS)
    if not os.path.exists(SESSIONS_FILE):
      self._save_json(SESSIONS_FILE, {})

  def _read_json(self, filepath: str) -> Any:
    try:
      with open(filepath, "r", encoding="utf-8-sig") as f:
        return json.load(f)
    except Exception:
      return None

  def _save_json(self, filepath: str, data: Any):
    tmp_path = filepath + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
      json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, filepath)

  # --- Sessions & Authentication ---
  def get_sessions(self) -> Dict[str, Any]:
    sessions = self._read_json(SESSIONS_FILE)
    return sessions if isinstance(sessions, dict) else {}

  def create_session(self, username: str) -> str:
    token = secrets.token_hex(24)
    sessions = self.get_sessions()
    sessions[token] = {
        "username": username,
        "created_at": datetime.datetime.now().isoformat(),
    }
    self._save_json(SESSIONS_FILE, sessions)
    return token

  def validate_session(self, token: str) -> Optional[str]:
    if not token:
      return None
    sessions = self.get_sessions()
    sess = sessions.get(token)
    if sess and isinstance(sess, dict):
      return sess.get("username")
    return None

  def delete_session(self, token: str):
    sessions = self.get_sessions()
    if token in sessions:
      del sessions[token]
      self._save_json(SESSIONS_FILE, sessions)

  def verify_credentials(self, username: str, password: str) -> bool:
    settings = self.get_settings()
    stored_user = settings.get("admin_username", "admin").strip().lower()
    stored_hash = settings.get("admin_password_hash")
    if not stored_hash:
      stored_hash = hash_password("admin")

    input_user = username.strip().lower()
    input_hash = hash_password(password)

    # Direct match with current settings
    if input_user == stored_user and input_hash == stored_hash:
      return True

    # Fallback initial credentials if password hasn't been changed yet
    if stored_hash == hash_password("admin"):
      if input_user in ("admin", "haitham") and password in ("admin", "0106923040"):
        return True

    return False

  def update_credentials(self, new_username: str, new_password: str) -> bool:
    settings = self.get_settings()
    settings["admin_username"] = new_username.strip()
    settings["admin_password_hash"] = hash_password(new_password)
    self.update_settings(settings)
    return True

  # --- Lines Management ---
  def get_lines(self) -> List[Dict[str, Any]]:
    lines = self._read_json(LINES_FILE)
    return lines if isinstance(lines, list) else []

  def get_line(self, line_id: str) -> Optional[Dict[str, Any]]:
    for line in self.get_lines():
      if line.get("id") == line_id:
        return line
    return None

  def add_line(
      self,
      landline: str,
      password: str,
      label: str = "",
      customer_name: str = "",
      notes: str = "",
      balance: Optional[float] = None,
      points: Optional[int] = None,
      whatsapp_enabled: bool = True,
      whatsapp_recipient: str = "",
  ) -> Dict[str, Any]:
    lines = self.get_lines()

    # Clean landline
    clean_num = "".join(filter(str.isdigit, landline.strip()))
    if not clean_num.startswith("0") and len(clean_num) >= 8:
      clean_num = "0" + clean_num

    new_line = {
        "id": str(uuid.uuid4())[:8],
        "landline": clean_num,
        "password": password,
        "label": label.strip() or f"خط {clean_num}",
        "customer_name": customer_name.strip(),
        "balance": balance,
        "points": points,
        "notes": notes.strip(),
        "whatsapp_enabled": whatsapp_enabled,
        "whatsapp_recipient": whatsapp_recipient.strip(),
        "is_active": True,
        "last_data": None,
        "last_error": None,
        "last_checked": None,
    }
    lines.append(new_line)
    self._save_json(LINES_FILE, lines)
    return new_line

  def update_line(self, line_id: str, updates: Dict[str, Any]) -> bool:
    lines = self.get_lines()
    for line in lines:
      if line.get("id") == line_id:
        line.update(updates)
        if isinstance(line.get("last_data"), dict):
          if "balance" in updates and updates["balance"] is not None:
            line["last_data"]["balance"] = updates["balance"]
          if "points" in updates and updates["points"] is not None:
            line["last_data"]["points"] = updates["points"]
          if "customer_name" in updates and updates["customer_name"]:
            line["last_data"]["customer_name"] = updates["customer_name"]
        self._save_json(LINES_FILE, lines)
        return True
    return False

  def update_line_quota(
      self, line_id: str, quota_data: Dict[str, Any]
  ) -> bool:
    lines = self.get_lines()
    for line in lines:
      if line.get("id") == line_id:
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %I:%M %p")
        if quota_data.get("success"):
          line["last_data"] = quota_data
          line["last_error"] = None
          detected_name = quota_data.get("customer_name")
          if detected_name and detected_name not in ["مشترك وي", "مشترك الخط الأرضي"]:
            line["customer_name"] = detected_name
            quota_data["customer_name"] = detected_name

          # Sync balance from WE
          if quota_data.get("balance") is not None:
            line["balance"] = quota_data["balance"]

          # Sync points from WE
          if quota_data.get("points") is not None:
            line["points"] = quota_data["points"]
        else:
          line["last_error"] = quota_data.get("error", "فشل الاستعلام")
        line["last_checked"] = quota_data.get("last_updated") or now_str
        self._save_json(LINES_FILE, lines)
        return True
    return False

  def delete_line(self, line_id: str) -> bool:
    lines = self.get_lines()
    orig_len = len(lines)
    lines = [l for l in lines if l.get("id") != line_id]
    if len(lines) < orig_len:
      self._save_json(LINES_FILE, lines)
      return True
    return False

  def delete_lines(self, line_ids: List[str]) -> int:
    lines = self.get_lines()
    orig_len = len(lines)
    id_set = set(line_ids)
    lines = [l for l in lines if l.get("id") not in id_set]
    deleted_count = orig_len - len(lines)
    if deleted_count > 0:
      self._save_json(LINES_FILE, lines)
    return deleted_count

  def bulk_add_lines(
      self,
      landlines: List[str],
      password: str,
      label_prefix: str = "",
      notes: str = "",
  ) -> Dict[str, Any]:
    lines = self.get_lines()
    existing_numbers = {l.get("landline") for l in lines}
    added_lines = []
    skipped_count = 0
    seen_in_batch = set()

    for raw_num in landlines:
      clean_num = "".join(filter(str.isdigit, raw_num.strip()))
      if not clean_num:
        continue
      if not clean_num.startswith("0") and len(clean_num) >= 7:
        clean_num = "0" + clean_num

      if len(clean_num) < 6 or clean_num in existing_numbers or clean_num in seen_in_batch:
        skipped_count += 1
        continue

      seen_in_batch.add(clean_num)
      existing_numbers.add(clean_num)

      label = f"{label_prefix} {clean_num}".strip() if label_prefix else f"خط {clean_num}"

      new_line = {
          "id": str(uuid.uuid4())[:8],
          "landline": clean_num,
          "password": password,
          "label": label,
          "customer_name": "",
          "balance": None,
          "points": None,
          "notes": notes.strip(),
          "is_active": True,
          "last_data": None,
          "last_error": None,
          "last_checked": None,
      }
      lines.append(new_line)
      added_lines.append(new_line)

    if added_lines:
      self._save_json(LINES_FILE, lines)

    return {
        "added_lines": added_lines,
        "added_count": len(added_lines),
        "skipped_count": skipped_count,
    }

  # --- Settings Management ---
  def get_settings(self) -> Dict[str, Any]:
    data = self._read_json(SETTINGS_FILE)
    if isinstance(data, dict):
      settings = DEFAULT_SETTINGS.copy()
      settings.update(data)
      return settings
    return DEFAULT_SETTINGS.copy()

  def update_settings(self, new_settings: Dict[str, Any]) -> Dict[str, Any]:
    current = self.get_settings()
    current.update(new_settings)
    self._save_json(SETTINGS_FILE, current)
    return current


# Global singleton storage instance
storage = Storage()
