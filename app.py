import asyncio
import datetime
import logging
import os
import re
from typing import List, Optional
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel
from scheduler import scheduler
from storage import storage
from telegram_notifier import check_and_send_alerts, test_telegram_connection
from we_api import WEApiClient

try:
  import psutil
except ImportError:
  psutil = None

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("we_app")

app = FastAPI(title="WE Landline Quota Monitor", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")


# --- Auth Dependency ---
def get_current_user(request: Request) -> str:
  token = request.cookies.get("we_session")
  if not token:
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
      token = auth_header.split(" ")[1]

  if not token:
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="غير مصرح، يرجى تسجيل الدخول",
    )

  username = storage.validate_session(token)
  if not username:
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="انتهت صلاحية الجلسة، يرجى إعادة تسجيل الدخول",
    )
  return username


# --- Models ---
class LoginRequest(BaseModel):
  username: str
  password: str


class ChangePasswordRequest(BaseModel):
  current_password: str
  new_password: str
  new_username: Optional[str] = None


class BulkDeleteRequest(BaseModel):
  ids: List[str]


class BulkImportRequest(BaseModel):
  lines_text: str
  password: str
  default_label: Optional[str] = ""
  notes: Optional[str] = ""
  fetch_now: Optional[bool] = True


class LineCreate(BaseModel):
  landline: str
  password: str
  label: Optional[str] = ""
  customer_name: Optional[str] = ""
  balance: Optional[float] = None
  points: Optional[int] = None
  notes: Optional[str] = ""
  fetch_now: Optional[bool] = True


class LineUpdate(BaseModel):
  label: Optional[str] = None
  customer_name: Optional[str] = None
  balance: Optional[float] = None
  points: Optional[int] = None
  password: Optional[str] = None
  notes: Optional[str] = None
  is_active: Optional[bool] = None


class SettingsUpdate(BaseModel):
  telegram_bot_token: Optional[str] = None
  telegram_chat_id: Optional[str] = None
  telegram_enabled: Optional[bool] = None
  alert_threshold_percent: Optional[int] = None
  alert_threshold_gb: Optional[int] = None
  alert_days_left: Optional[int] = None
  auto_refresh_hours: Optional[int] = None
  auto_refresh_minutes: Optional[int] = None


class TelegramTest(BaseModel):
  bot_token: str
  chat_id: str


# --- Lifecycle Events ---
@app.on_event("startup")
def startup_event():
  scheduler.start()
  logger.info("WE Quota Monitor Server started.")


@app.on_event("shutdown")
def shutdown_event():
  scheduler.stop()


# --- HTML Page Views ---
@app.get("/", response_class=HTMLResponse)
def index_page(request: Request):
  token = request.cookies.get("we_session")
  if not token or not storage.validate_session(token):
    return RedirectResponse(url="/login")

  index_file = os.path.join(TEMPLATES_DIR, "index.html")
  if os.path.exists(index_file):
    with open(index_file, "r", encoding="utf-8") as f:
      return f.read()
  return "<h1>Error: templates/index.html not found</h1>"


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
  token = request.cookies.get("we_session")
  if token and storage.validate_session(token):
    return RedirectResponse(url="/")

  login_file = os.path.join(TEMPLATES_DIR, "login.html")
  if os.path.exists(login_file):
    with open(login_file, "r", encoding="utf-8") as f:
      return f.read()
  return "<h1>Error: templates/login.html not found</h1>"


# --- Authentication Endpoints ---
@app.post("/api/auth/login")
def auth_login(payload: LoginRequest, response: Response):
  if not storage.verify_credentials(payload.username, payload.password):
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="اسم المستخدم أو كلمة المرور غير صحيحة",
    )

  token = storage.create_session(payload.username.strip())
  response.set_cookie(
      key="we_session",
      value=token,
      max_age=86400 * 30,  # 30 days
      httponly=True,
      samesite="lax",
      path="/",
  )
  return {
      "success": True,
      "message": "تم تسجيل الدخول بنجاح",
      "token": token,
      "username": payload.username,
  }


@app.post("/api/auth/logout")
def auth_logout(request: Request, response: Response):
  token = request.cookies.get("we_session")
  if token:
    storage.delete_session(token)
  response.delete_cookie(key="we_session", path="/")
  return {"success": True, "message": "تم تسجيل الخروج بنجاح"}


@app.get("/api/auth/me")
def auth_me(user: str = Depends(get_current_user)):
  settings = storage.get_settings()
  current_user = settings.get("admin_username", user)
  return {"success": True, "username": current_user}


@app.post("/api/auth/change-password")
def change_password(
    payload: ChangePasswordRequest, user: str = Depends(get_current_user)
):
  if not storage.verify_credentials(user, payload.current_password):
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="كلمة المرور الحالية غير صحيحة",
    )

  if len(payload.new_password) < 4:
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="كلمة المرور الجديدة يجب ألا تقل عن 4 أحرف",
    )

  new_user = payload.new_username.strip() if payload.new_username else user
  storage.update_credentials(new_user, payload.new_password)
  return {"success": True, "message": "تم تحديث بيانات الدخول بنجاح"}


# --- Lines API Endpoints ---
@app.get("/api/lines")
def list_lines(user: str = Depends(get_current_user)):
  lines = storage.get_lines()
  safe_lines = []
  for l in lines:
    safe = l.copy()
    safe["has_password"] = bool(safe.pop("password", None))
    safe_lines.append(safe)
  return {"success": True, "lines": safe_lines}


@app.post("/api/lines")
def add_line(
    item: LineCreate,
    background_tasks: BackgroundTasks,
    user: str = Depends(get_current_user),
):
  if not item.landline or not item.password:
    raise HTTPException(
        status_code=400, detail="رقم التليفون الأرضي وكلمة المرور مطلوبان"
    )

  clean_num = "".join(filter(str.isdigit, item.landline.strip()))
  if len(clean_num) < 6:
    raise HTTPException(status_code=400, detail="رقم التليفون الأرضي غير صالح")

  existing = [l for l in storage.get_lines() if l.get("landline") == clean_num]
  if existing:
    raise HTTPException(
        status_code=400, detail="هذا الرقم الأرضي مسجل بالفعل في القائمة"
    )

  new_line = storage.add_line(
      landline=clean_num,
      password=item.password,
      label=item.label,
      customer_name=item.customer_name or "",
      balance=item.balance,
      points=item.points,
      notes=item.notes,
  )

  if item.fetch_now:

    def fetch_task():
      client = WEApiClient(clean_num, item.password)
      data = client.fetch_quota()
      storage.update_line_quota(new_line["id"], data)
      settings = storage.get_settings()
      line = storage.get_line(new_line["id"])
      if line and data.get("success"):
        check_and_send_alerts(line, settings)

    background_tasks.add_task(fetch_task)

  return {
      "success": True,
      "message": "تم إضافة الخط بنجاح، جاري جلب الباقة...",
      "line": new_line,
  }


@app.put("/api/lines/{line_id}")
def update_line(
    line_id: str, updates: LineUpdate, user: str = Depends(get_current_user)
):
  line = storage.get_line(line_id)
  if not line:
    raise HTTPException(status_code=404, detail="الخط غير موجود")

  up_dict = {}
  if updates.label is not None:
    up_dict["label"] = updates.label
  if updates.customer_name is not None:
    up_dict["customer_name"] = updates.customer_name
  if updates.balance is not None:
    up_dict["balance"] = updates.balance
  if updates.points is not None:
    up_dict["points"] = updates.points
  if updates.notes is not None:
    up_dict["notes"] = updates.notes
  if updates.password:
    up_dict["password"] = updates.password
  if updates.is_active is not None:
    up_dict["is_active"] = updates.is_active

  if storage.update_line(line_id, up_dict):
    return {"success": True, "message": "تم تحديث بيانات الخط بنجاح"}
  raise HTTPException(status_code=500, detail="فشل تحديث الخط")


@app.delete("/api/lines/{line_id}")
def delete_line(line_id: str, user: str = Depends(get_current_user)):
  if storage.delete_line(line_id):
    return {"success": True, "message": "تم حذف الخط بنجاح"}
  raise HTTPException(status_code=404, detail="الخط غير موجود")


@app.post("/api/lines/bulk-delete")
def bulk_delete_lines(
    payload: BulkDeleteRequest, user: str = Depends(get_current_user)
):
  if not payload.ids:
    raise HTTPException(status_code=400, detail="يرجى تحديد خط واحد على الأقل")
  deleted_count = storage.delete_lines(payload.ids)
  return {
      "success": True,
      "message": f"تم حذف {deleted_count} خط بنجاح",
      "deleted_count": deleted_count,
  }


@app.post("/api/lines/bulk-import")
def bulk_import_lines(
    payload: BulkImportRequest,
    background_tasks: BackgroundTasks,
    user: str = Depends(get_current_user),
):
  if not payload.password:
    raise HTTPException(status_code=400, detail="كلمة المرور الموحدة مطلوبة")

  if not payload.lines_text or not payload.lines_text.strip():
    raise HTTPException(
        status_code=400,
        detail="يرجى إدخال أرقام الخطوط أو رفع ملف التكست",
    )

  # Split by newline, comma, semicolon, space, or tab
  raw_items = re.split(r"[\r\n,;]+", payload.lines_text)
  landlines = [item.strip() for item in raw_items if item.strip()]

  if not landlines:
    raise HTTPException(
        status_code=400,
        detail="لم يتم العثور على أي أرقام صالحة في النص المدخل",
    )

  result = storage.bulk_add_lines(
      landlines=landlines,
      password=payload.password,
      label_prefix=payload.default_label or "",
      notes=payload.notes or "",
  )

  added_lines = result["added_lines"]

  if payload.fetch_now and added_lines:

    def fetch_batch_task():
      settings = storage.get_settings()
      for line in added_lines:
        try:
          client = WEApiClient(line["landline"], line["password"])
          data = client.fetch_quota()
          storage.update_line_quota(line["id"], data)
          updated = storage.get_line(line["id"])
          if updated and data.get("success"):
            check_and_send_alerts(updated, settings)
        except Exception as e:
          logger.error(
              f"Error fetching imported line {line.get('landline')}: {e}"
          )

    background_tasks.add_task(fetch_batch_task)

  msg = f"تمت إضافة {result['added_count']} خط بنجاح"
  if result["skipped_count"] > 0:
    msg += f" (تم تخطي {result['skipped_count']} رقم مكرر أو غير صالح)"

  return {
      "success": True,
      "message": msg,
      "added_count": result["added_count"],
      "skipped_count": result["skipped_count"],
  }


@app.post("/api/lines/{line_id}/refresh")
def refresh_line(line_id: str, user: str = Depends(get_current_user)):
  line = storage.get_line(line_id)
  if not line:
    raise HTTPException(status_code=404, detail="الخط غير موجود")

  client = WEApiClient(line["landline"], line["password"])
  data = client.fetch_quota()
  storage.update_line_quota(line_id, data)

  settings = storage.get_settings()
  updated_line = storage.get_line(line_id)
  if updated_line and data.get("success"):
    check_and_send_alerts(updated_line, settings)

  return {"success": True, "data": data}


@app.post("/api/lines/refresh-all")
def refresh_all_lines(
    background_tasks: BackgroundTasks, user: str = Depends(get_current_user)
):
  background_tasks.add_task(scheduler.refresh_all_lines)
  return {
      "success": True,
      "message": "تم بدء تحديث جميع الخطوط في الخلفية بنجاح.",
  }


@app.get("/api/settings")
def get_settings(user: str = Depends(get_current_user)):
  settings = storage.get_settings()
  safe = settings.copy()
  safe.pop("last_alerts_sent", None)
  safe.pop("admin_password_hash", None)
  return {"success": True, "settings": safe}


@app.post("/api/settings")
def save_settings(
    data: SettingsUpdate, user: str = Depends(get_current_user)
):
  updates = {k: v for k, v in data.dict().items() if v is not None}
  updated = storage.update_settings(updates)
  safe = updated.copy()
  safe.pop("last_alerts_sent", None)
  safe.pop("admin_password_hash", None)
  return {
      "success": True,
      "message": "تم حفظ الإعدادات بنجاح",
      "settings": safe,
  }


@app.post("/api/telegram/test")
def test_telegram(
    payload: TelegramTest, user: str = Depends(get_current_user)
):
  result = test_telegram_connection(payload.bot_token, payload.chat_id)
  return result


@app.get("/api/stats")
def get_stats(user: str = Depends(get_current_user)):
  lines = storage.get_lines()
  total_lines = len(lines)
  active_lines = sum(1 for l in lines if l.get("is_active", True))

  total_quota_gb = 0.0
  total_remain_gb = 0.0
  total_used_gb = 0.0
  critical_lines_count = 0
  nearest_renewal_days = None

  for l in lines:
    raw_data = l.get("last_data")
    if raw_data and raw_data.get("success"):
      inner = (
          raw_data.get("data")
          if isinstance(raw_data.get("data"), dict)
          else raw_data
      )
      tot = float(inner.get("total_gb") or inner.get("total") or 0.0)
      rem = float(inner.get("remain_gb") or inner.get("remain") or 0.0)
      usd = float(inner.get("used_gb") or inner.get("used") or 0.0)
      days = (
          inner.get("days_left")
          if inner.get("days_left") is not None
          else inner.get("days_remaining", 999)
      )
      if days is None:
        days = 999

      total_quota_gb += tot
      total_remain_gb += rem
      total_used_gb += usd

      status_val = inner.get("status_tag") or inner.get("status")
      rem_pct = inner.get("remain_percent") or inner.get("remain_percentage")
      if rem_pct is None:
        rem_pct = (rem / tot * 100.0) if tot > 0 else 100.0

      if status_val in ("critical", "warning") or rem_pct <= 20:
        critical_lines_count += 1

      if nearest_renewal_days is None or days < nearest_renewal_days:
        nearest_renewal_days = days

  if nearest_renewal_days is not None:
    if nearest_renewal_days <= 0:
      nearest_renewal_text = "اليوم ⚠️"
    elif nearest_renewal_days == 1:
      nearest_renewal_text = "غداً ⏳"
    elif nearest_renewal_days == 2:
      nearest_renewal_text = "خلال يومين ⏳"
    else:
      nearest_renewal_text = f"خلال {nearest_renewal_days} يوم"
  else:
    nearest_renewal_text = "-"

  return {
      "success": True,
      "stats": {
          "total_lines": total_lines,
          "active_lines": active_lines,
          "total_quota_gb": round(total_quota_gb, 1),
          "total_remain_gb": round(total_remain_gb, 1),
          "total_used_gb": round(total_used_gb, 1),
          "critical_lines_count": critical_lines_count,
          "nearest_renewal_days": nearest_renewal_days,
          "nearest_renewal_text": nearest_renewal_text,
          "last_checked_time": datetime.datetime.now().strftime(
              "%Y/%m/%d %I:%M %p"
          ),
      },
  }


@app.get("/api/system/status")
def get_system_status(user: str = Depends(get_current_user)):
  try:
    if psutil:
      cpu_pct = round(psutil.cpu_percent(interval=None), 1)
      mem = psutil.virtual_memory()
      total_ram_gb = round(mem.total / (1024**3), 2)
      used_ram_gb = round(mem.used / (1024**3), 2)
      ram_pct = round(mem.percent, 1)
      cores = psutil.cpu_count(logical=True) or 1
      boot_time = psutil.boot_time()
      uptime_sec = int(datetime.datetime.now().timestamp() - boot_time)
    else:
      cores = os.cpu_count() or 1
      load1 = 0.0
      try:
        with open("/proc/loadavg") as f:
          load1 = float(f.read().split()[0])
      except Exception:
        pass
      cpu_pct = min(100.0, round((load1 / cores) * 100.0, 1))

      total_ram_gb, used_ram_gb, ram_pct = 8.0, 1.0, 12.5
      try:
        with open("/proc/meminfo") as f:
          m = {}
          for line in f:
            p = line.split(":")
            if len(p) == 2:
              m[p[0].strip()] = int(p[1].split()[0])
          tot = m.get("MemTotal", 0) / (1024 * 1024)
          avail = m.get("MemAvailable", 0) / (1024 * 1024)
          total_ram_gb = round(tot, 2)
          used_ram_gb = round(tot - avail, 2)
          ram_pct = round((used_ram_gb / tot * 100.0), 1) if tot > 0 else 0
      except Exception:
        pass
      uptime_sec = 3600

    hours = uptime_sec // 3600
    mins = (uptime_sec % 3600) // 60
    uptime_str = f"{hours} س و {mins} د" if hours > 0 else f"{mins} دقيقة"

    return {
        "success": True,
        "cpu": {
            "percent": cpu_pct,
            "cores": cores,
        },
        "ram": {
            "percent": ram_pct,
            "used_gb": used_ram_gb,
            "total_gb": total_ram_gb,
        },
        "uptime": uptime_str,
    }
  except Exception as e:
    return {"success": False, "error": str(e)}


if __name__ == "__main__":
  import uvicorn

  print(
      "\n=========================================================================="
  )
  print("🚀 WE Landline Quota Monitor is running!")
  print("🌐 Open in browser: http://localhost:8000")
  print(
      "==========================================================================\n"
  )
  uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=False)
