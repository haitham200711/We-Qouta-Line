import logging
import threading
import time
from storage import storage
from telegram_notifier import check_and_send_alerts
from whatsapp_notifier import check_and_send_whatsapp_alerts
from we_api import WEApiClient

logger = logging.getLogger("scheduler")


class QuotaScheduler:

  def __init__(self):
    self._stop_event = threading.Event()
    self._thread = None
    self._is_running = False

  def refresh_all_lines(self) -> dict:
    """Refreshes all active lines synchronously or concurrently."""
    lines = storage.get_lines()
    settings = storage.get_settings()
    results = {"total": len(lines), "success": 0, "failed": 0, "details": []}

    for line in lines:
      if not line.get("is_active", True):
        continue

      landline = line.get("landline")
      password = line.get("password")
      line_id = line.get("id")

      try:
        client = WEApiClient(landline, password)
        data = client.fetch_quota()
        storage.update_line_quota(line_id, data)

        if data.get("success"):
          results["success"] += 1
          # Check thresholds & send alerts
          updated_line = storage.get_line(line_id)
          if updated_line:
            check_and_send_alerts(updated_line, settings)
            check_and_send_whatsapp_alerts(updated_line, settings)
        else:
          results["failed"] += 1

        results["details"].append({
            "id": line_id,
            "landline": landline,
            "success": data.get("success", False),
            "error": data.get("error"),
        })

      except Exception as e:
        logger.exception(f"Error refreshing line {landline}: {e}")
        storage.update_line_quota(
            line_id, {"success": False, "error": f"خطأ في الاتصال: {str(e)}"}
        )
        results["failed"] += 1

      # Small sleep between accounts to avoid rate limits
      time.sleep(1)

    return results

  def _run_loop(self):
    logger.info("Background quota refresh scheduler started.")
    # Wait 10 seconds before the very first initial background run
    if self._stop_event.wait(10):
      return

    while not self._stop_event.is_set():
      settings = storage.get_settings()
      interval_mins = settings.get("auto_refresh_minutes")
      if interval_mins is None:
        interval_mins = int(settings.get("auto_refresh_hours", 1)) * 60
      interval_mins = max(10, int(interval_mins))
      interval_seconds = interval_mins * 60

      logger.info(
          f"Executing background quota update (Interval: {interval_mins}m)..."
      )
      try:
        self.refresh_all_lines()
      except Exception as e:
        logger.error(f"Error during periodic refresh: {e}")

      # Wait for next interval or until stopped
      if self._stop_event.wait(interval_seconds):
        break

  def start(self):
    if not self._is_running:
      self._is_running = True
      self._stop_event.clear()
      self._thread = threading.Thread(target=self._run_loop, daemon=True)
      self._thread.start()

  def stop(self):
    if self._is_running:
      self._stop_event.set()
      self._is_running = False


scheduler = QuotaScheduler()
