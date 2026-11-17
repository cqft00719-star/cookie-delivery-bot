import os
import re
import json
import base64
import traceback
from datetime import datetime, date
from urllib.parse import quote

import requests
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

# ================== Env ==================
EVOLUTION_URL = os.getenv("EVOLUTION_URL", "https://evolution-api-production-5d91de27.up.railway.app").rstrip("/")
EVOLUTION_API_KEY = os.getenv("AUTHENTICATION_API_KEY", os.getenv("EVOLUTION_API_KEY", ""))
INSTANCE = os.getenv("EVOLUTION_INSTANCE", "my WhatsApps")
BOT_URL = os.getenv("BOT_URL", "https://cookie-delivery-bot-production.up.railway.app")

GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID", "")
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON", "")

app = FastAPI()

scan_dates = []
logs_cache = []

def log(msg: str):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)
    logs_cache.append(msg)
    if len(logs_cache) > 200:
        logs_cache.pop(0)

# ================== Google Sheets ==================
def get_gspread_client():
    try:
        import gspread
        from google.oauth2.service_account import Credentials
        if not GOOGLE_CREDENTIALS_JSON:
            log("No GOOGLE_CREDENTIALS_JSON, skip sheets")
            return None
        creds_str = GOOGLE_CREDENTIALS_JSON.strip()
        try:
            if not creds_str.startswith("{"):
                creds_str = base64.b64decode(creds_str).decode("utf-8")
        except Exception:
            pass
        info = json.loads(creds_str)
        scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        creds = Credentials.from_service_account_info(info, scopes=scopes)
        return gspread.authorize(creds)
    except Exception as e:
        log(f"gspread init failed: {e}")
        return None

def load_scan_dates():
    global scan_dates
    scan_dates = []
    if not GOOGLE_SHEET_ID:
        log("No GOOGLE_SHEET_ID")
        return scan_dates
    try:
        gc = get_gspread_client()
        if not gc:
            return scan_dates
        sh = gc.open_by_key(GOOGLE_SHEET_ID)
        try:
            ws = sh.worksheet("運輸總表")
            rows = ws.get_all_values()
            if len(rows) <= 1:
                log("運輸總表 only header, skip load")
            else:
                log(f"運輸總表 loaded {len(rows)-1} rows")
                for r in rows[1:]:
                    if r and r[0]:
                        if r[0] not in scan_dates:
                            scan_dates.append(r[0])
        except Exception as e:
            log(f"運輸總表 read failed: {e}")
        try:
            for ws in sh.worksheets():
                title = ws.title
                if re.match(r"^\d{4}-\d{2}-\d{2}$", title) or re.match(r"^\d{2}-\d{2}$", title):
                    if title not in scan_dates:
                        scan_dates.append(title)
        except Exception as e:
            log(f"list sheets failed: {e}")
        scan_dates = sorted(scan_dates)
        log(f"Loaded scan_dates: {scan_dates}")
    except Exception as e:
        log(f"load_scan_dates failed: {e} {traceback.format_exc()}")
    return scan_dates

@app.on_event("startup")
def on_startup():
    log("=== cookie-delivery-bot starting ===")
    load_scan_dates()

# ================== 主頁 / Health ==================
@app.get("/")
def root():
    return {
        "status": "running",
        "time": datetime.now().isoformat(),
        "instance": INSTANCE,
        "scan_dates": scan_dates,
        "webhook_url": f"{BOT_URL}/webhook",
        "recent_logs": logs_cache[-20:]
    }

@app.get("/health")
def health():
    return {"ok": True}

@app.get("/debug-sheets")
def debug_sheets():
    if not GOOGLE_SHEET_ID:
        return {"ok": False, "error": "No GOOGLE_SHEET_ID"}
    try:
        gc = get_gspread_client()
        if not gc:
            return {"ok": False, "error": "gspread client failed"}
        sh = gc.open_by_key(GOOGLE_SHEET_ID)
        out = []
        for ws in sh.worksheets():
            try:
                vals = ws.get_all_values()
                out.append({"title": ws.title, "rows": len(vals), "header": vals[0] if vals else []})
            except Exception as e:
                out.append({"title": ws.title, "error": str(e)})
        return {"ok": True, "sheets": out, "scan_dates": scan_dates}
    except Exception as e:
        return {"ok": False, "error": str(e)}

# ================== 一鍵修 Webhook - 已修正格式 ==================
@app.get("/fix-webhook")
def fix_webhook():
    try:
        url = f"{EVOLUTION_URL}/webhook/set/{quote(INSTANCE)}"
        # Evolution 新版要包一層 webhook: { ... }，唔係直接 Send
        payload = {
            "webhook": {
                "enabled": True,
                "url": f"{BOT_URL}/webhook",
                "webhookByEvents": False,
                "webhookBase64": False,
                "events": ["MESSAGES_UPSERT", "MESSAGES_UPDATE", "CONNECTION_UPDATE"]
            }
        }
        log(f"FIX-WEBHOOK POST {url} payload={payload}")
        r = requests.post(url, json=payload, headers={"apikey": EVOLUTION_API_KEY}, timeout=20)
        log(f"FIX-WEBHOOK set_status={r.status_code} body={r.text[:1000]}")
        try:
            r2 = requests.get(f"{EVOLUTION_URL}/webhook/find/{quote(INSTANCE)}", headers={"apikey": EVOLUTION_API_KEY}, timeout=20)
            current = r2.text[:3000]
        except Exception as e2:
            current = f"find failed: {e2}"
        return {"ok": True, "set_status": r.status_code, "set_result": r.text[:2000], "current": current}
    except Exception as e:
        log(f"FIX-WEBHOOK failed: {e}")
        return {"ok": False, "error": str(e)}

# ================== Webhook 主邏輯 ==================
def extract_text(data: dict) -> str:
    try:
        msg = data.get("data", {}).get("message", {}) or data.get("message", {}) or {}
        if msg.get("conversation"):
            return msg["conversation"]
        if msg.get("extendedTextMessage", {}).get("text"):
            return msg["extendedTextMessage"]["text"]
        if msg.get("imageMessage", {}).get("caption"):
            return msg["imageMessage"]["caption"]
        if msg.get("videoMessage", {}).get("caption"):
            return msg["videoMessage"]["caption"]
        if msg.get("documentMessage", {}).get("caption"):
            return msg["documentMessage"]["caption"]
    except Exception:
        pass
    return ""

def has_image(data: dict) -> bool:
    try:
        msg = data.get("data", {}).get("message", {}) or data.get("message", {}) or {}
        return bool(msg.get("imageMessage") or msg.get("videoMessage") or msg.get("documentMessage"))
    except Exception:
        return False

@app.post("/webhook")
async def webhook(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        event = body.get("event") or body.get("type") or ""
        data = body.get("data") or body or {}
        remote_jid = (
            data.get("key", {}).get("remoteJid")
            or data.get("key", {}).get("remoteJidAlt")
            or data.get("remoteJid")
            or body.get("remoteJid")
            or ""
        )
        push_name = data.get("pushName") or data.get("pushname") or ""
        from_me = data.get("key", {}).get("fromMe", False)
        msg_id = data.get("key", {}).get("id", "")

        if event and "upsert" not in str(event).lower():
            return {"ok": True, "ignored_event": event}

        text = extract_text(body)
        is_image = has_image(body)
        log(f"WEBHOOK event={event} remoteJid: {remote_jid} fromMe={from_me} pushName={push_name} text={text[:200]} hasImage={is_image} msgId={msg_id}")

        if from_me:
            return {"ok": True, "ignored": "fromMe"}

        if not remote_jid:
            log("WEBHOOK no remoteJid, skip but logged")
            return {"ok": True, "warn": "no remoteJid"}

        is_sgl = False
        if is_image:
            is_sgl = True
        if any(k in text for k in ["SGL", "sgl", "曲奇", "送貨", "11-", "12-", "2026-"]):
            is_sgl = True

        if is_sgl:
            log(f"SGL_DETECTED remoteJid={remote_jid} text={text} hasImage={is_image}")
            today = date.today().isoformat()
            if today not in scan_dates:
                scan_dates.append(today)
                scan_dates.sort()
        else:
            log(f"WEBHOOK ignored non-SGL text={text[:100]}")

        return {"ok": True, "remoteJid": remote_jid, "text": text[:100], "hasImage": is_image, "is_sgl": is_sgl}

    except Exception as e:
        log(f"WEBHOOK error: {e} {traceback.format_exc()}")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=200)

@app.post("/webhook/")
async def webhook_slash(request: Request):
    return await webhook(request)
