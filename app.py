import os, re, json, base64, traceback, io
from datetime import datetime
from urllib.parse import quote
import requests
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

# === 你 Railway 而家嗰 6 個 Variables，一個都唔使再加 ===
EVOLUTION_URL = "https://evolution-api-production-5d91de27.up.railway.app"
EVOLUTION_API_KEY = os.getenv("EVOLUTION_API_KEY", "")
INSTANCE = "my WhatsApps"
BOT_URL = "https://cookie-delivery-bot-production.up.railway.app"
GCP_JSON = os.getenv("GCP_JSON", "")
MY_WHATSAPP = os.getenv("MY_WHATSAPP", "")
TMS_GROUP_ID = os.getenv("TMS_GROUP_ID", "")
COOKIE_GROUP_ID = os.getenv("COOKIE_GROUP_ID", "85264352282-1512209129@g.us")
TMS_SHEET_ID = os.getenv("TMS_SHEET_ID", "15X06a1oOFeta-ETz-CiLbB-5TUWS1rWKGi35HmBNNVE")

ALLOWED_GROUPS = [g for g in [TMS_GROUP_ID, COOKIE_GROUP_ID] if g]
SHOPS = ["TSTN","TSTG","TST","MK","MPMK","MOKO","CENT","HSP","CPE","CWB","CYW","AT1","AT2","ATRA","APM","KLN","TM","SH"]

app = FastAPI()
logs_cache = []
pending = {}

def log(m):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {m}", flush=True)
    logs_cache.append(m)
    if len(logs_cache)>200: logs_cache.pop(0)

def get_gcp_creds():
    from google.oauth2.service_account import Credentials
    s = GCP_JSON.strip()
    if not s.startswith("{"):
        s = base64.b64decode(s).decode()
    info = json.loads(s)
    scopes = ["https://www.googleapis.com/auth/spreadsheets","https://www.googleapis.com/auth/drive","https://www.googleapis.com/auth/cloud-vision"]
    return Credentials.from_service_account_info(info, scopes=scopes), info

def send_text(number, text):
    try:
        url = f"{EVOLUTION_URL}/message/sendText/{quote(INSTANCE)}"
        requests.post(url, json={"number": number, "text": text}, headers={"apikey": EVOLUTION_API_KEY}, timeout=20)
    except Exception as e: log(f"SEND fail {e}")

def download_image(msg_id, remote_jid):
    try:
        url = f"{EVOLUTION_URL}/chat/fetchMedia/{quote(INSTANCE)}"
        r = requests.post(url, json={"messageId": msg_id, "remoteJid": remote_jid}, headers={"apikey": EVOLUTION_API_KEY}, timeout=30)
        j = r.json()
        b64 = j.get("base64") or j.get("data", {}).get("base64") or ""
        if b64:
            if "," in b64: b64 = b64.split(",",1)[1]
            return base64.b64decode(b64)
    except Exception as e: log(f"DL fail {e}")
    return None

def ocr_with_gcp_vision(img_bytes):
    # 用返你 GCP_JSON 嗰個 Service Account，唔使另外開 Gemini / OpenAI Key
    try:
        from google.cloud import vision
        creds, _ = get_gcp_creds()
        client = vision.ImageAnnotatorClient(credentials=creds)
        image = vision.Image(content=img_bytes)
        resp = client.document_text_detection(image=image)
        txt = resp.full_text_annotation.text if resp.full_text_annotation else ""
        log(f"VISION OCR: {txt[:1000]}")
        return txt
    except Exception as e:
        log(f"VISION fail {e} {traceback.format_exc()}")
        return ""

def parse_sgl_text(txt):
    # 搵日期 15-11-2026 / 15/11/2026
    m = re.search(r"(\d{1,2})[-/](\d{1,2})[-/](202\d)", txt)
    date_str = f"{int(m.group(1)):02d}-{int(m.group(2)):02d}-{m.group(3)}" if m else datetime.now().strftime("%d-%m-%Y")
    # 搵總數: 最後一行大數字
    nums = [int(x) for x in re.findall(r"\b(\d{2,3})\b", txt)]
    total = max(nums) if nums else 0
    # 用行嚟搵每間舖: e.g. TSTN 37 / 37 喺 TSTN 隔離
    boxes = {}
    lines = txt.replace(",", " ").splitlines()
    for shop in SHOPS:
        boxes[shop] = 0
        for line in lines:
            if shop in line.upper():
                ns = [int(x) for x in re.findall(r"\b(\d{1,3})\b", line)]
                if ns: boxes[shop] = ns[-1]
    return {"date": date_str, "total": total, "boxes": boxes, "raw": txt}

def write_to_sheet(parsed):
    import gspread
    creds, _ = get_gcp_creds()
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(TMS_SHEET_ID)
    ws = sh.worksheet("運輸總表")
    dt = datetime.strptime(parsed["date"], "%d-%m-%Y")
    sheet_date = dt.strftime("%d-%b")  # 15-Nov
    values = ws.get_all_values()
    row_idx = None
    for i, r in enumerate(values[1:], start=2):
        if r and r[0].strip() == sheet_date:
            row_idx = i; break
    if not row_idx:
        row_idx = len(values)+1
        ws.update_cell(row_idx, 1, sheet_date)
    row_values = [parsed["boxes"].get(s,0) if parsed["boxes"].get(s,0)!=0 else "" for s in SHOPS]
    ws.update(f"B{row_idx}:S{row_idx}", [row_values])
    ws.update_cell(row_idx, 20, parsed["total"])
    log(f"SHEET_WRITTEN {sheet_date} row={row_idx} total={parsed['total']}")
    return sheet_date, row_idx

def extract_text(body):
    msg = body.get("data", {}).get("message", {}) or {}
    if msg.get("conversation"): return msg["conversation"]
    if msg.get("extendedTextMessage", {}).get("text"): return msg["extendedTextMessage"]["text"]
    if msg.get("imageMessage", {}).get("caption"): return msg["imageMessage"]["caption"]
    return ""

@app.post("/webhook")
async def webhook(request: Request):
    try: body = await request.json()
    except: body = {}
    try:
        if body.get("event") and "upsert" not in str(body.get("event")).lower():
            return {"ok": True}
        data = body.get("data") or {}
        remote_jid = data.get("key", {}).get("remoteJid") or ""
        msg_id = data.get("key", {}).get("id", "")
        text = extract_text(body)
        msg = data.get("message", {}) or {}
        is_image = bool(msg.get("imageMessage"))
        log(f"WEBHOOK jid={remote_jid} text={text[:80]} img={is_image}")

        if remote_jid not in ALLOWED_GROUPS:
            return {"ok": True, "ignored": remote_jid}

        low = text.strip().lower()
        if low in ["啱","確認","ok","yes","confirm","寫入"] and pending:
            last = list(pending.keys())[-1]
            parsed = pending[last]
            sd, row = write_to_sheet(parsed)
            send_text(remote_jid, f"✅ 已寫入運輸總表：{sd} 第 {row} 行，總箱數 {parsed['total']}")
            if MY_WHATSAPP:
                send_text(MY_WHATSAPP, f"✅ 已寫入運輸總表：{sd} 總箱數 {parsed['total']}")
            pending.clear()
            return {"ok": True}

        if low in ["唔啱","錯","no"] and pending:
            pending.clear()
            send_text(remote_jid, "❌ 已取消，請重新 Send 清楚啲嘅相。")
            return {"ok": True}

        if not is_image:
            return {"ok": True}

        img = download_image(msg_id, remote_jid)
        if not img:
            send_text(remote_jid, "⚠️ 下載唔到張相，請重新 Send 一次。")
            return {"ok": True}

        txt = ocr_with_gcp_vision(img)
        parsed = parse_sgl_text(txt)
        calc = sum(parsed["boxes"].values())
        log(f"PARSED {parsed} calc={calc}")

        # 如果 Vision 讀到嘅加埋唔啱總數，唔好自動寫，要俾你睇
        if calc != parsed["total"]:
            send_text(remote_jid, f"⚠️ 讀到 {parsed['date']}，加埋 {calc}，但單上總數 {parsed['total']}，驗算唔啱，唔會自動寫入。\nOCR 原文：{txt[:500]}")
            return {"ok": True, "mismatch": True}

        pending[parsed["date"]] = parsed
        box_str = ", ".join([f"{k}:{v}" for k,v in parsed["boxes"].items() if v])
        send_text(remote_jid, f"📋 讀到 {parsed['date']} SGL單：\n{box_str}\n總箱數：{parsed['total']}\n\n啱請覆「啱」就會寫入運輸總表，錯請覆「唔啱」。")
        return {"ok": True}

    except Exception as e:
        log(f"ERR {e} {traceback.format_exc()}")
        return JSONResponse({"ok": False}, status_code=200)

@app.get("/")
def root():
    return {"status":"running","allowed_groups":ALLOWED_GROUPS,"pending":list(pending.keys()),"logs":logs_cache[-20:]}

@app.get("/fix-webhook")
def fix_webhook():
    url = f"{EVOLUTION_URL}/webhook/set/{quote(INSTANCE)}"
    payload = {"webhook":{"enabled":True,"url":f"{BOT_URL}/webhook","webhookByEvents":False,"webhookBase64":False,"events":["MESSAGES_UPSERT"]}}
    r = requests.post(url, json=payload, headers={"apikey": EVOLUTION_API_KEY}, timeout=20)
    return {"ok":True,"status":r.status_code,"result":r.text[:1000]}
