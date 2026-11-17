import os, re, json, base64, traceback, io, time
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
OCR_KEY = os.getenv("OCR_SPACE_API_KEY", "helloworld")
ALLOWED = [g for g in [TMS_GROUP_ID, COOKIE_GROUP_ID] if g]
SHOPS = ["TSTN","TSTG","TST","MK","MPMK","MOKO","CENT","HSP","CPE","CWB","CYW","AT1","AT2","ATRA","APM","KLN","TM","SH"]
app = FastAPI()
logs = []
pending = {}
recent = {}

def log(m):
    print(m, flush=True)
    logs.append(str(m))
    if len(logs) > 100:
        logs.pop(0)

def creds():
    from google.oauth2.service_account import Credentials
    s = GCP_JSON.strip()
    if not s.startswith("{"):
        s = base64.b64decode(s).decode()
    info = json.loads(s)
    return Credentials.from_service_account_info(info, scopes=["https://www.googleapis.com/auth/spreadsheets","https://www.googleapis.com/auth/drive"])

def sheet():
    import gspread
    return gspread.authorize(creds())

def send_text(n, t):
    try:
        requests.post(EVOLUTION_URL + "/message/sendText/" + quote(INSTANCE), json={"number": n, "text": t}, headers={"apikey": EVOLUTION_API_KEY}, timeout=20)
    except Exception as e:
        log(e)

def send_xls(n, b, fn, cap):
    try:
        b64 = base64.b64encode(b).decode()
        requests.post(EVOLUTION_URL + "/message/sendMedia/" + quote(INSTANCE), json={"number": n, "mediatype": "document", "mimetype": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "caption": cap, "media": b64, "fileName": fn}, headers={"apikey": EVOLUTION_API_KEY}, timeout=60)
    except Exception as e:
        log(e)

def dl_img(data):
    try:
        r = requests.post(EVOLUTION_URL + "/chat/getBase64FromMediaMessage/" + quote(INSTANCE), json={"message": {"key": data.get("key", {}), "message": data.get("message", {})}}, headers={"apikey": EVOLUTION_API_KEY}, timeout=60)
        j = r.json()
        b64 = j.get("base64") or j.get("data", {}).get("base64") or ""
        if not b64:
            return None
        if "," in b64:
            b64 = b64.split(",", 1)[1]
        return base64.b64decode(b64)
    except Exception as e:
        log(e)
        return None

def ocr(img):
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(img)).convert("RGB")
        im.thumbnail((1200, 1200))
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=75)
        r = requests.post("https://api.ocr.space/parse/image", files={"file": ("s.jpg", buf.getvalue(), "image/jpeg")}, data={"apikey": OCR_KEY, "language": "eng", "OCREngine": "2", "scale": "true"}, timeout=60)
        j = r.json()
        t = ""
        try:
            t = j["ParsedResults"][0]["ParsedText"]
        except Exception:
            t = ""
        log("OCR:" + t[:500])
        return t
    except Exception as e:
        log(e)
        return ""

def is_sgl(t):
    if not t:
        return False
    u = t.upper()
    if "SGL" in u:
        return True
    if "SCAN GLOBAL" in u:
        return True
    if "出貨" in t or "送貨單" in t:
        return True
    f = [s for s in SHOPS if s in u]
    n = re.findall(r"\b\d{1,3}\b", t)
    return len(f) >= 3 and len(n) >= 5

def parse(t):
    m = re.search(r"(\d{1,2})[-/](\d{1,2})[-/](202\d)", t)
    ds = (str(int(m.group(1))).zfill(2) + "-" + str(int(m.group(2))).zfill(2) + "-" + m.group(3)) if m else datetime.now().strftime("%d-%m-%Y")
    m2 = re.search(r"總箱數[:：\s]*(\d+)", t)
    total = int(m2.group(1)) if m2 else 0
    if not total:
        ns = [int(x) for x in re.findall(r"\b(\d{2,3})\b", t)]
        total = max(ns) if ns else 0
    boxes = {}
    lines = t.replace(",", " ").splitlines()
    for s in SHOPS:
        boxes[s] = 0
        for line in lines:
            if s in line.upper():
                ns = [int(x) for x in re.findall(r"\b(\d{1,3})\b", line)]
                if ns:
                    boxes[s] = ns[-1]
    return {"date": ds, "total": total, "boxes": boxes}

def write_sheet(p):
    sh = sheet().open_by_key(TMS_SHEET_ID)
    ws = sh.worksheet("運輸總表")
    dt = datetime.strptime(p["date"], "%d-%m-%Y")
    sd = dt.strftime("%d-%b")
    vals = ws.get_all_values()
    ri = None
    for i, r in enumerate(vals[1:], start=2):
        if r and r[0].strip() == sd:
            ri = i
            break
    if not ri:
        ri = len(vals) + 1
        ws.update_cell(ri, 1, sd)
    rv = [p["boxes"].get(s, 0) if p["boxes"].get(s, 0) != 0 else "" for s in SHOPS]
    ws.update("B" + str(ri) + ":S" + str(ri), [rv])
    ws.update_cell(ri, 20, p["total"])
    return sd, ri

def ex_text(body):
    m = body.get("data", {}).get("message", {}) or {}
    return m.get("conversation") or m.get("extendedTextMessage", {}).get("text") or m.get("imageMessage", {}).get("caption") or ""

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
        jid = data.get("key", {}).get("remoteJid") or ""
        msg = data.get("message", {}) or {}
        if jid not in ALLOWED:
            return {"ok": True}
        if jid == TMS_GROUP_ID:
            return {"ok": True}
        txt = ex_text(body)
        low = txt.strip().lower()
        if low in ["啱", "確認", "ok", "yes", "confirm"] and pending:
            k = list(pending.keys())[-1]
            p = pending[k]
            sd, row = write_sheet(p)
            send_text(jid, "已寫入 " + sd + " 第" + str(row) + "行 總箱數" + str(p["total"]))
            pending.clear()
            return {"ok": True}
        if low in ["唔啱", "錯", "no"] and pending:
            pending.clear()
            send_text(jid, "已取消")
            return {"ok": True}
        if not msg.get("imageMessage"):
            return {"ok": True}
        sender = data.get("key", {}).get("participant") or jid
        now = time.time()
        lst = [t for t in recent.get(sender, []) if now - t < 30]
        if len(lst) >= 3:
            return {"ok": True}
        lst.append(now)
        recent[sender] = lst
        img = dl_img(data)
        if not img:
            return {"ok": True}
        t = ocr(img)
        if not is_sgl(t):
            log("IGNORE not SGL")
            return {"ok": True}
        p = parse(t)
        calc = sum(p["boxes"].values())
        if calc != p["total"]:
            send_text(jid, "讀到 " + p["date"] + " 加埋" + str(calc) + " 但總數" + str(p["total"]) + " 唔啱")
            return {"ok": True}
        pending[p["date"]] = p
        bs = ", ".join([k + ":" + str(v) for k, v in p["boxes"].items() if v])
        send_text(jid, "讀到 " + p["date"] + " SGL單:\n" + bs + "\n總箱數:" + str(p["total"]) + "\n啱請覆啱")
        return {"ok": True}
    except Exception as e:
        log("ERR " + str(e) + " " + traceback.format_exc())
        return JSONResponse({"ok": False}, status_code=200)

def cookie_xls():
    import openpyxl
    ws = sheet().open_by_key(TMS_SHEET_ID).worksheet("運輸總表")
    vals = ws.get_all_values()
    today = datetime.now().strftime("%d-%b")
    row = None
    for r in vals[1:]:
        if r and r[0].strip() == today:
            row = r
            break
    if not row:
        return None, today
    wb = openpyxl.Workbook()
    w2 = wb.active
    w2.append(["日期", "舖頭", "箱數"])
    for s, v in zip(SHOPS, row[1:19]):
        if v:
            w2.append([today, s, int(v)])
    w2.append([today, "總數", row[19] if len(row) > 19 else ""])
    bio = io.BytesIO()
    wb.save(bio)
    bio.seek(0)
    return bio
