"""
WE (Telecom Egypt) API Client
Handles communication with My WE portal via headless browser engine to satisfy
WebAssembly request signing (whiteReqBodySign/whiteReqHeaderSign) and service-type selection.
"""

import json
import logging
import subprocess
import os
import sys
from datetime import datetime
from typing import Dict, Any, Optional

logger = logging.getLogger("we_api")


class WEApiClient:
    def __init__(self, landline_number: str, password: str):
        self.landline = landline_number.strip()
        self.password = password.strip()
        self.base_dir = os.path.dirname(os.path.abspath(__file__))
        self.fetch_script = os.path.join(self.base_dir, "fetch_line.js")

    def fetch_quota(self) -> Dict[str, Any]:
        """
        Executes fetch_line.js via node to authenticate with Telecom Egypt
        and fetch quota telemetry.
        """
        logger.info(f"Connecting to WE Portal for line {self.landline}...")

        if not os.path.exists(self.fetch_script):
            return {
                "success": False,
                "error": "ملف الربط fetch_line.js غير موجود"
            }

        node_bin = "node"
        if sys.platform == "win32" and os.path.exists("C:\\Program Files\\nodejs\\node.exe"):
            node_bin = "C:\\Program Files\\nodejs\\node.exe"

        def _kill_proc(p):
            if not p or not p.pid:
                return
            try:
                if sys.platform == "win32":
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True)
                else:
                    subprocess.run(f"pkill -P {p.pid} 2>/dev/null || true; kill -9 {p.pid} 2>/dev/null || true", shell=True, capture_output=True)
            except Exception:
                pass
            try:
                p.kill()
            except Exception:
                pass

        proc = None
        try:
            cmd = [node_bin, self.fetch_script, self.landline, self.password]
            proc = subprocess.Popen(
                cmd,
                cwd=self.base_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="ignore"
            )

            try:
                stdout, stderr = proc.communicate(timeout=60)
            except subprocess.TimeoutExpired:
                logger.error("Node fetch process timed out after 60s")
                _kill_proc(proc)
                return {
                    "success": False,
                    "error": "استغرقت عملية الاستعلام وقتاً أطول من المتوقع، يرجى إعادة المحاولة"
                }

            output = stdout.strip()
            if not output:
                err = stderr.strip()
                logger.error(f"Node execution returned empty output: {err}")
                return {
                    "success": False,
                    "error": f"فشل تشغيل محرك الاستعلام: {err[:150]}"
                }

            # Find the JSON line in the output
            json_line = None
            for line in output.splitlines():
                line = line.strip()
                if line.startswith("{") and line.endswith("}"):
                    json_line = line
                    break

            if not json_line:
                return {
                    "success": False,
                    "error": f"استجابة غير متوقعة من المحرك: {output[:150]}"
                }

            result = json.loads(json_line)
            if not result.get("success"):
                return {
                    "success": False,
                    "error": result.get("error", "خطأ في تسجيل الدخول أو بيانات الباقة")
                }

            raw_body = result.get("data", [])
            parsed = self._parse_quota_data(raw_body)
            if result.get("customer_name"):
                parsed["customer_name"] = result["customer_name"]
            if result.get("balance") is not None:
                parsed["balance"] = result["balance"]
            if result.get("points") is not None:
                parsed["points"] = result["points"]
            return parsed

        except Exception as e:
            logger.exception(f"Unexpected error in fetch_quota: {e}")
            _kill_proc(proc)
            return {
                "success": False,
                "error": f"حدث خطأ غير متوقع: {str(e)}"
            }

    def _parse_quota_data(self, body_list: list) -> Dict[str, Any]:
        """
        Parses WE queryFreeUnit response payload into normalized flat structure.
        """
        if not body_list or not isinstance(body_list, list):
            return {
                "success": False,
                "error": "لم يتم العثور على باقات مفعلة لهذا الخط"
            }

        # Find the Home Internet usage unit
        home_unit = body_list[0]
        total_gb = float(home_unit.get("total", 0.0))
        used_gb = float(home_unit.get("used", 0.0))
        remain_gb = float(home_unit.get("remain", 0.0))
        offer_name = home_unit.get("offerName", "باقة وي إنترنت")

        # Parse expiration and renewal
        expire_ts = home_unit.get("expireTime")
        renewal_date_str = "غير محدد"
        days_remaining = 0
        hours_remaining = 0

        if expire_ts:
            try:
                # Milliseconds timestamp
                exp_dt = datetime.fromtimestamp(int(expire_ts) / 1000.0)
                renewal_date_str = exp_dt.strftime("%Y-%m-%d %I:%M %p")
                now_dt = datetime.now()
                diff_seconds = (exp_dt - now_dt).total_seconds()
                if diff_seconds > 0:
                    days_remaining = int(diff_seconds // 86400)
                    hours_remaining = int((diff_seconds % 86400) // 3600)
                else:
                    days_remaining = 0
                    hours_remaining = 0
            except Exception as e:
                logger.warning(f"Failed to parse expireTime: {e}")

        # Usage percentage
        if total_gb > 0:
            usage_pct = round((used_gb / total_gb) * 100.0, 1)
            remain_pct = round((remain_gb / total_gb) * 100.0, 1)
        else:
            usage_pct = 0.0
            remain_pct = 0.0

        # Determine health status
        if days_remaining <= 2 or remain_pct <= 10:
            status_tag = "critical"
            status_text = "حرج (موعد التجديد غداً)" if days_remaining <= 1 else ("حرج (قرب موعد التجديد)" if days_remaining <= 2 else "حرج (باقة منخفضة)")
        elif remain_pct <= 25 or days_remaining <= 5:
            status_tag = "warning"
            status_text = "تحذير (قرب الانتهاء)"
        else:
            status_tag = "good"
            status_text = "ممتاز"

        # Detailed breakdown of bundles (Main Quota vs Topups)
        bundles = []
        main_bundle = None
        topup_bundles = []

        raw_details = home_unit.get("freeUnitBeanDetailList", [])
        if not raw_details:
            main_bundle = {
                "name": offer_name,
                "type": "Main Quota",
                "initial_amount": round(total_gb, 2),
                "current_amount": round(remain_gb, 2),
                "used_amount": round(used_gb, 2),
                "remain_percent": remain_pct,
                "used_percent": usage_pct,
                "expire_date": renewal_date_str,
                "days_left": days_remaining,
                "hours_left": hours_remaining
            }
        else:
            for detail in raw_details:
                b_initial = float(detail.get("initialAmount", 0.0))
                b_current = float(detail.get("currentAmount", 0.0))
                b_used = max(0.0, round(b_initial - b_current, 2))
                b_remain_pct = round((b_current / b_initial) * 100.0, 1) if b_initial > 0 else 0.0
                b_used_pct = round((b_used / b_initial) * 100.0, 1) if b_initial > 0 else 0.0
                b_type = (detail.get("originType") or "").strip()
                b_name = detail.get("offeringName") or offer_name

                # Expiration calculation for this specific bundle
                b_expire_ts = detail.get("expireTime")
                b_expire_str = "غير محدد"
                b_days = detail.get("remainingDaysForRenewal")
                b_hours = 0
                if b_expire_ts:
                    try:
                        b_dt = datetime.fromtimestamp(int(b_expire_ts) / 1000.0)
                        b_expire_str = b_dt.strftime("%Y-%m-%d %I:%M %p")
                        diff = (b_dt - datetime.now()).total_seconds()
                        if diff > 0:
                            calc_days = int(diff // 86400)
                            b_hours = int((diff % 86400) // 3600)
                            if b_days is None:
                                b_days = calc_days
                        else:
                            b_days = 0
                            b_hours = 0
                    except Exception as e:
                        logger.warning(f"Failed to parse bundle expireTime: {e}")

                # Fallback to main renewal date and days if bundle specific expiration is missing
                if (b_days is None or int(b_days) <= 0) and days_remaining > 0:
                    b_days = days_remaining
                    b_hours = hours_remaining

                if b_expire_str == "غير محدد":
                    if renewal_date_str != "غير محدد":
                        b_expire_str = renewal_date_str
                    elif b_days and int(b_days) > 0:
                        from datetime import timedelta
                        calc_dt = datetime.now() + timedelta(days=int(b_days))
                        b_expire_str = calc_dt.strftime("%Y-%m-%d 12:00 AM")

                bundle_obj = {
                    "name": b_name,
                    "type": b_type,
                    "initial_amount": round(b_initial, 2),
                    "current_amount": round(b_current, 2),
                    "used_amount": b_used,
                    "remain_percent": b_remain_pct,
                    "used_percent": b_used_pct,
                    "expire_date": b_expire_str,
                    "days_left": int(b_days) if b_days is not None else 0,
                    "hours_left": b_hours
                }
                bundles.append(bundle_obj)

                if "main" in b_type.lower():
                    if not main_bundle:
                        main_bundle = bundle_obj
                    else:
                        is_curr_promo = any(w in (main_bundle.get("name") or "").lower() for w in ["promo", "flag", "extra"])
                        is_new_promo = any(w in (bundle_obj.get("name") or "").lower() for w in ["promo", "flag", "extra"])
                        if is_curr_promo and not is_new_promo:
                            main_bundle = bundle_obj
                        elif not is_new_promo and bundle_obj.get("initial_amount", 0) > main_bundle.get("initial_amount", 0):
                            main_bundle = bundle_obj

            # If no bundle specifically tagged as 'main', pick the first or one with smallest/primary code
            if not main_bundle and bundles:
                main_bundle = bundles[0]

            # Topups are active bundles other than main_bundle (exclude finished/expired topups where current_amount <= 0)
            topup_bundles = [
                b for b in bundles
                if b != main_bundle and float(b.get("current_amount", 0.0)) > 0
            ]

            # Keep only main_bundle and active topup bundles (remove finished extra quotas completely)
            bundles = [b for b in bundles if b == main_bundle or float(b.get("current_amount", 0.0)) > 0]

            # Recalculate total_gb and used_gb so expired topups are NOT added to the total capacity
            if main_bundle or topup_bundles:
                active_initial = (main_bundle.get("initial_amount", 0.0) if main_bundle else 0.0) + sum(b.get("initial_amount", 0.0) for b in topup_bundles)
                if active_initial > 0:
                    total_gb = round(active_initial, 2)
                    used_gb = max(0.0, round(total_gb - remain_gb, 2))
                    remain_pct = round((remain_gb / total_gb) * 100.0, 1) if total_gb > 0 else 0.0
                    usage_pct = round((used_gb / total_gb) * 100.0, 1) if total_gb > 0 else 0.0

            # Re-evaluate status based on active quota
            if days_remaining <= 2 or remain_pct <= 10:
                status_tag = "critical"
                status_text = "حرج (موعد التجديد غداً)" if days_remaining <= 1 else ("حرج (قرب موعد التجديد)" if days_remaining <= 2 else "حرج (باقة منخفضة)")
            elif remain_pct <= 25 or days_remaining <= 5:
                status_tag = "warning"
                status_text = "تحذير (قرب الانتهاء)"
            else:
                status_tag = "good"
                status_text = "ممتاز"

        balance = None
        points = None

        return {
            "success": True,
            "line_number": self.landline,
            "customer_name": "مشترك وي",
            "offer_name": offer_name,
            "total_gb": round(total_gb, 2),
            "used_gb": round(used_gb, 2),
            "remain_gb": round(remain_gb, 2),
            "remain_percent": remain_pct,
            "used_percent": usage_pct,
            "expire_date": renewal_date_str,
            "days_left": days_remaining,
            "hours_left": hours_remaining,
            "balance": balance,
            "points": points,
            "status_tag": status_tag,
            "status_text": status_text,
            "main_bundle": main_bundle,
            "topup_bundles": topup_bundles,
            "bundles": bundles,
            "last_updated": datetime.now().strftime("%Y-%m-%d %I:%M %p")
        }
