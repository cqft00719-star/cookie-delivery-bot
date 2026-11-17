import os
import re
import json
import base64
import traceback
import io
import time
from datetime import datetime, timedelta
from urllib.parse import quote
import requests
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

EVOLUTION_URL = "https://evolution-api-production-5d91de27.up.railway.app"
EVOLUTION_API_KEY = os.getenv("EVOLUTION_API_KEY", "")
INSTANCE = "my WhatsApps"
BOT_URL = "https://cookie-delivery-bot-production.up.railway.app"
GCP_JSON = os.getenv("GCP_JSON", "")
MY_WHATSAPP = os.getenv("MY_WHATSAPP", "")
TMS_GROUP_ID = os.getenv("TMS_GROUP_ID", "")
COOKIE_GROUP_ID = os.getenv("COOKIE_GROUP_ID", "85264352282-1512209129@g.us")
TMS_SHEET_ID = os.getenv("TMS_SHEET_ID", "15X06a1oOFeta-ETz-CiLbB-5TUWS1rWKGi35HmBNNVE")
OCR_SPACE_API_KEY = os.getenv("OCR_SPACE_API_KEY", "helloworld")

ALLOWED_GROUPS = [g for g in [TMS_GROUP_ID, COOKIE_GROUP_ID] if g]
SHOPS = ["TSTN", "TSTG", "TST", "MK", "MPMK", "MOKO", "CENT", "HSP", "CPE", "CWB", "CYW", "AT1", "AT2", "ATRA", "APM", "KLN", "TM", "SH"]

app = FastAPI()
logs_cache = []
pending = {}
recent_photos = {}

def log(m):
    print("[" + datetime.now().strftime("%H:%M:%S") + "] " + str(m), flush=True)
    logs_cache.append(str(m))
    if len(logs_cache) > 200:
        logs_cache.pop(0)

def get_gcp_creds():
    from google.oauth2.service_account import Credentials
    s = GCP_JSON.strip()
    if not s.startswith("{"):
        s = base64.b64decode(s).decode()
    info = json.loads(s)
    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    return Credentials.from_service_account_info(info, scopes=scopes), info

def get_sheet_client():
    import gspread
    creds, _ = get_gcp_creds()
    return gspread.authorize(creds)

def send_text(number, text):
    try:
        url = EVOLUTION_URL + "/message/sendText/" + quote(INSTANCE)
        requests.post(url, json={"number": number, "text": text}, headers={"apikey": EVOLUTION_API_KEY}, timeout=20)
    except Exception as e:
        log("SEND fail " + str(e))

def send_excel_bytes(number, file_bytes, filename, caption):
    try:
        b64 = base64.b64encode(file_bytes).decode()
        url = EVOLUTION_URL + "/message/sendMedia/" + quote(INSTANCE)
        r = requests.post(url, json={"number": number, "mediatype": "document", "mimetype": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "caption": caption, "media": b64, "fileName": filename}, headers={"apikey": EVOLUTION_API_KEY}, timeout=60)
        log("SEND_EXCEL " + str(number) + " " + str(r.status_code))
        return r.status_code in [200, 201]
    except Exception as e:
        log("SEND_EXCEL fail " + str(e))
        return False

def download_image(data):
    try:
        url = EVOLUTION_URL + "/chat/getBase64FromMediaMessage/" + quote(INSTANCE)
        r = requests.post(url, json={"message": {"key": data.get("key", {}), "message": data.get("message", {})}}, headers={"apikey": EVOLUTION_API_KEY}, timeout=60)
        j = r.json()
        b64 = j.get("base64") or j.get("data", {}).get("base64") or ""
        if not b64:
            return None
        if "," in b64:
            b64 = b64.split(",", 1)[1]
        return base64.b64decode(b64)
    except Exception as e:
        log("DL fail " + str(e))
        return None

def ocr_image(img_bytes):
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        im.thumbnail((1200, 1200))
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=75)
        small = buf.getvalue()
        r = requests.post("https://api.ocr.space/parse/image", files={"file": ("sgl.jpg", small, "image/jpeg")}, data={"apikey": OCR_SPACE_API_KEY, "language": "eng", "OCREngine": "2", "scale": "true", "isTable": "true"}, timeout=60)
        j = r.json()
        txt = ""
        try:
            txt = j["ParsedResults"][0]["ParsedText"]
        except Exception:
            txt = ""
        log("OCR: " + txt[:800])
        return txt
    except Exception as e:
        log("OCR fail " + str(e))
        return ""

def is_sgl_logistics(txt):
    if not txt or not txt.strip():
        return False
    t = txt.upper()
    if "SGL" in t:
        return True
    if "SCAN GLOBAL" in t:
        return True
    if "SCAN" in t and "LOGISTICS" in t:
        return True
    if "出貨" in txt or "送貨單" in txt or "出貨清單" in txt:
        return True
    found = [s for s in SHOPS if s in t]
    nums = re.findall(r"\b\d{1,3}\b", txt)
    if len(found) >= 3 and len(nums) >= 5:
        return True
    return False

def parse_sgl_text(txt):
    m = re.search(r"(\d{1,2})[-/](\d{1,2})[-/](202\d)", txt)
    if m:
        date_str = str(int(m.group(1))).zfill(2) + "-" + str(int(m.group(2))).zfill(2) + "-" + m.group(3)
    else:
        date_str = datetime.now().strftime("%d-%m-%Y")
    total = 0
    m2 = re.search(r"總箱數[:：\s]*(\d+)", txt)
    if m2:
        total = int(m2.group(1))
    else:
        nums = [int(x) for x in re.findall(r"\b(\d{2,3})\b", txt)]
        if nums:
            total = max(nums)
    boxes = {}
    lines = txt.replace(",", " ").splitlines()
    for shop in SHOPS:
        boxes[shop] = 0
        for line in lines:
            if shop in line.upper():
                ns = [int(x) for x in re.findall(r"\b(\d{1,3})\b", line)]
                if ns:
                    boxes[shop] = ns[-1]
    return {"date": date_str, "total": total, "boxes": boxes, "raw": txt}

def write_to_sheet(parsed):
    gc = get_sheet_client()
    sh = gc.open_by_key(TMS_SHEET_ID)
    ws = sh.worksheet("運輸總表")
    dt = datetime.strptime(parsed["date"], "%d-%m-%Y")
    sheet_date = dt.strftime("%d-%b")
    values = ws.get_all_values()
    row_idx = None
    for i, r in enumerate(values[1:], start=2):
        if r and r[0].strip() == sheet_date:
            row_idx = i
            break
    if not row_idx:
        row_idx = len(values) + 1
        ws.update_cell(row_idx, 1, sheet_date)
    row_values = [parsed["boxes"].get(s, 0) if parsed["boxes"].get(s, 0) != 0 else "" for s in SHOPS]
    ws.update("B" + str(row_idx) + ":S" + str(row_idx), [row_values])
    ws.update_cell(row_idx, 20, parsed["total"])
    return sheet_date, row_idx

def extract_text(body):
    msg = body.get("data", {}).get("message", {}) or {}
    if msg.get("conversation"):
        return msg["conversation"]
    if msg.get("extendedTextMessage", {}).get("text"):
        return msg["extendedTextMessage"]["text"]
    if msg.get("imageMessage", {}).get("caption"):
        return msg["imageMessage"]["caption"]
    return ""

@app.post("/webhook")
async def webhook(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        if body.get("event") and "upsert" not in str(body.get("event")).lower():
            return {"ok": True}
        data = body.get("data") or {}
        remote_jid = data.get("key", {}).get("remoteJid") or ""
        msg = data.get("message", {}) or {}
        is_image = bool(msg.get("imageMessage"))
        text = extract_text(body)
        if remote_jid not in ALLOWED_GROUPS:
            return {"ok": True}
        if remote_jid == TMS_GROUP_ID:
            return {"ok": True}
        low = text.strip().lower()
        if low in ["啱", "確認", "ok", "yes", "confirm", "寫入"] and pending:
            last = list(pending.keys())[-1]
            parsed = pending[last]
            sd, row = write_to_sheet(parsed)
            send_text(remote_jid, "已寫入運輸總表：" + sd + " 第 " + str(row) + " 行，總箱數 " + str(parsed["total"]))
            if MY_WHATSAPP:
                send_text(MY_WHATSAPP, "已寫入運輸總表：" + sd + " 總箱數 " + str(parsed["total"]))
            pending.clear()
            return {"ok": True}
        if low in ["唔啱", "錯", "no"] and pending:
            pending.clear()
            send_text(remote_jid, "已取消，請重新 Send 一張清楚啲嘅 SGL 單。")
            return {"ok": True}
        if not is_image:
            return {"ok": True}
        sender = data.get("key", {}).get("participant") or remote_jid
        now_ts = time.time()
        lst = [t for t in recent_photos.get(sender, []) if now_ts - t < 30]
        if len(lst) >= 3:
            log("SKIP burst " + str(sender))
            return {"ok": True}
        lst.append(now_ts)
        recent_photos[sender] = lst
        img = download_image(data)
        if not img:
            log("DL fail silent")
            return {"ok": True}
        txt = ocr_image(img)
        if not is_sgl_logistics(txt):
            log("IGNORE not SGL")
            return {"ok": True}
        parsed = parse_sgl_text(txt)
        calc = sum(parsed["boxes"].values())
        log("SGL FOUND " + str(parsed) + " calc=" + str(calc))
        if calc != parsed["total"]:
            send_text(remote_jid, "讀到 " + parsed["date"] + "，加埋 " + str(calc) + "，但單上總數 " + str(parsed["total"]) + "，驗算唔啱，唔會自動寫入。")
            return {"ok": True}
        pending[parsed["date"]] = parsed
        box_str = ", ".join([k + ":" + str(v) for k, v in parsed["boxes"].items() if v])
        send_text(remote_jid, "讀到 " + parsed["date"] + " SGL單：\n" + box_str + "\n總箱數：" + str(parsed["total"]) + "\n\n啱請覆「啱」就會寫入運輸總表，錯請覆「唔啱」。")
        return {"ok": True}
    except Exception as e:
        log("ERR " + str(e) + " " + traceback.format_exc())
        return JSONResponse({"ok": False}, status
