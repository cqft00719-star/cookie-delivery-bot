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
SHOPS = ["TSTN","TSTG","TST","MK","MPMK","MOKO","CENT","HSP","CPE","CWB","CYW","AT1","AT2","ATRA","APM","KLN","TM","SH"]
app = FastAPI()
logs = []
# 曲奇群組收到嘅相，淨係收起，唔覆群組
cookie_photos = []

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
        data = img
        try:
            from PIL import Image
            im = Image.open(io.BytesIO(img)).convert("RGB")
            im.thumbnail((1600, 1600))
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=85)
            data = buf.getvalue()
        except Exception as e:
            log("PIL skip " + str(e))
        for eng in ["2", "1"]:
            try:
                r = requests.post("https://api.ocr.space/parse/image", files={"file": ("s.jpg", data, "image/jpeg")}, data={"apikey": OCR_KEY, "language": "eng", "OCREngine": eng, "scale": "true", "isTable": "true"}, timeout=60)
                j = r.json()
                log("OCR raw " + str(j)[:600])
                t = ""
                try:
                    t = j["ParsedResults"][0]["ParsedText"]
                except Exception:
                    t = ""
                if t and t.strip():
                    log("OCR:" + t[:1000])
                    return t
            except Exception as e:
                log("OCR fail " + str(e))
        return ""
    except Exception as e:
        log(e)
        return {"ok": True, "status": r.status_code, "result": r.text[:500]}
        def is_sgl(t):
    if not t:
        return False
    u = t.upper()
    if "SGL" in u:
        return True
    if "SCAN" in u:
        return True
    if "GLOBAL" in u and "LOGISTICS" in u:
        return True
    if "LOGISTICS" in u:
        return True
    if "出貨" in t or "送貨單" in t:
        return True
    f = [s for s in SHOPS if s in u]
    n = re.findall(r"\b\d{1,3}\b", t)
    return len(f) >= 3 and len(n) >= 5

def parse(t):
    # 日期：優先捉 17-11-2026 呢種，喺張相右下角手寫日期
    m = re.search(r"(\d{1,2})[-/. ](\d{1,2})[-/. ](202\d)", t)
    ds = (str(int(m.group(1))).zfill(2) + "-" + str(int(m.group(2))).zfill(2) + "-" + m.group(3)) if m else datetime.now().strftime("%d-%m-%Y")
    m2 = re.search(r"總箱數[:：\s]*(\d+)", t)
    total = int(m2.group(1)) if m2 else 0
    if not total:
        # 搵 187 呢種 3 位數最大嗰個當總數
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
    return {"date": ds, "total": total, "boxes": boxes, "raw": t}

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

# ===== 1. WhatsApp Webhook：曲奇群組只收相，唔覆群組 =====
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
        # 只處理曲奇送貨群組嘅相，其他群組唔理
        if jid != COOKIE_GROUP_ID:
            return {"ok": True}
        if not msg.get("imageMessage"):
            return {"ok": True}
        img = dl_img(data)
        if not img:
            return {"ok": True}
        # 收起張相，等 22:00 先一次過處理，唔喺群組出聲
        cookie_photos.append({"time": datetime.now().strftime("%H:%M"), "img": img})
        log("COOKIE photo saved, total=" + str(len(cookie_photos)))
        return {"ok": True}
    except Exception as e:
        log("ERR " + str(e) + " " + traceback.format_exc())
        return JSONResponse({"ok": False}, status_code=200)

# ===== 2. TMS Apps Webhook：新單 / 狀態變更，即刻通知運輸群組 =====
@app.post("/tms-webhook")
async def tms_webhook(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    log("TMS webhook " + str(body)[:1000])
    # TMS Apps 唔同系統格式唔同，盡量攞晒常見欄位
    job = body.get("job") or body.get("order") or body.get("data") or body
    if not isinstance(job, dict):
        job = {"text": str(body)}
    job_id = job.get("JobID") or job.get("InternalNo") or job.get("id") or job.get("order_no") or ""
    status = job.get("Status") or job.get("status") or job.get("state") or "更新"
    date = job.get("Date") or job.get("date") or job.get("DeliveryDate") or ""
    client = job.get("Client") or job.get("customer") or ""
    frm = job.get("From") or job.get("from") or ""
    to = job.get("To") or job.get("to") or ""
    notes = job.get("Notes") or job.get("notes") or job.get("CargoDetail") or ""
    text = "【TMS 更新】" + str(status) + "\n"
    if job_id:
        text += "單號: " + str(job_id) + "\n"
    if date:
        text += "日期: " + str(date) + "\n"
    if client:
        text += "客戶: " + str(client) + "\n"
    if frm or to:
        text += "路線: " + str(frm) + " -> " + str(to) + "\n"
    if notes:
        text += "備註: " + str(notes) + "\n"
    if TMS_GROUP_ID:
        send_text(TMS_GROUP_ID, text)
    return {"ok": True}

# ===== 3. 曲奇 Excel：俾你一個人，唔係群組 =====
def cookie_xls(p):
    import openpyxl
    wb = openpyxl.Workbook()
    w2 = wb.active
    w2.title = "今日送貨"
    w2.append(["日期", "舖頭", "箱數"])
    for s in SHOPS:
        v = p["boxes"].get(s, 0)
        if v:
            w2.append([p["date"], s, int(v)])
    w2.append([p["date"], "總數", p["total"]])
    bio = io.BytesIO()
    wb.save(bio)
    bio.seek(0)
    return bio.getvalue()

# ===== 4. TMS 每日 17:00 Excel：聽日 + 後日 =====
def tms_daily_xls():
    import openpyxl
    ws = sheet().open_by_key(TMS_SHEET_ID).worksheet("Jobs")
    vals = ws.get_all_values()
    if len(vals) < 2:
        return None, "Jobs 冇資料"
    headers = [h.strip() for h in vals[0]]
    date_idx = None
    for c in ["Date", "送貨日期"]:
        if c in headers:
            date_idx = headers.index(c)
            break
    if date_idx is None:
        return None, "Jobs 冇 Date 欄"
    tomorrow = (datetime.now() + timedelta(days=1)).date()
    day_after = tomorrow + timedelta(days=1)
    filtered = []
    for r in vals[1:]:
        try:
            d = datetime.strptime(r[date_idx].split(" ")[0], "%Y-%m-%d").date()
        except Exception:
            try:
                d = datetime.strptime(r[date_idx].split(" ")[0], "%d-%m-%Y").date()
            except Exception:
                continue
        if d in [tomorrow, day_after]:
            filtered.append(r)
    if not filtered:
        return None, str(tomorrow) + "~" + str(day_after) + " 冇單"
    wb = openpyxl.Workbook()
    w2 = wb.active
    w2.title = "TMS Orders"
    idxs = [i for i, h in enumerate(headers) if h in ["InternalNo", "JobID", "Date", "Client", "Zone", "From", "To", "CargoDetail", "DeliveryTime", "Notes", "Status"]]
    w2.append([headers[i] for i in idxs])
    for r in filtered:
        w2.append([r[i] if i < len(r) else "" for i in idxs])
    bio = io.BytesIO()
    wb.save(bio)
    bio.seek(0)
    return bio.getvalue(), str(tomorrow) + " to " + str(day_after) + " 共 " + str(len(filtered)) + " 單"

def send17():
    if not TMS_GROUP_ID:
        return
    b, info = tms_daily_xls()
    if not b:
        send_text(TMS_GROUP_ID, "TMS每日提醒 " + datetime.now().strftime("%Y-%m-%d") + " " + info)
    else:
        send_xls(TMS_GROUP_ID, b, "TMS_" + datetime.now().strftime("%Y-%m-%d") + ".xlsx", "TMS每日提醒 " + datetime.now().strftime("%Y-%m-%d %H:%M") + "\n" + info)

# ===== 5. 每晚 22:00：讀曲奇相 -> 寫 Sheet -> 私人 Send Excel 俾你 =====
def send22():
    if not MY_WHATSAPP:
        log("No MY_WHATSAPP")
        return
    if not cookie_photos:
        send_text(MY_WHATSAPP, "22:00 曲奇：今日曲奇群組冇收到 SGL 相，冇嘢寫入。")
        return
    # 用最尾嗰張當今日嗰張
    last = cookie_photos[-1]
    img = last["img"]
    t = ocr(img)
    if not is_sgl(t):
        send_text(MY_WHATSAPP, "22:00 曲奇：今日嗰張相 OCR 讀唔到 SCAN GLOBAL LOGISTICS，冇寫入。OCR 原文：\n" + t[:500])
        cookie_photos.clear()
        return
    p = parse(t)
    calc = sum(p["boxes"].values())
    warn = ""
    if calc != p["total"]:
        warn = "\n⚠️ 驗算唔啱：加埋=" + str(calc) + "，單上總數=" + str(p["total"]) + "，已照單上總數寫入，請自己對一次。"
    try:
        sd, row = write_sheet(p)
        b = cookie_xls(p)
        bs = ", ".join([k + ":" + str(v) for k, v in p["boxes"].items() if v])
        send_xls(MY_WHATSAPP, b, "cookie-" + p["date"] + ".xlsx", p["date"] + " 曲奇送貨總結\n" + bs + "\n總箱數:" + str(p["total"]) + "\n已寫入運輸總表 " + sd + " 第" + str(row) + "行" + warn)
    except Exception as e:
        send_text(MY_WHATSAPP, "22:00 曲奇寫入失敗：" + str(e) + "\nOCR：" + t[:500])
    cookie_photos.clear()

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
sc = BackgroundScheduler(timezone="Asia/Hong_Kong")
sc.add_job(send17, CronTrigger(hour=17, minute=0))
sc.add_job(send22, CronTrigger(hour=22, minute=0))
sc.start()

@app.get("/")
def root():
    return {"status": "running", "cookie_photos": len(cookie_photos), "logs": logs[-20:]}

@app.get("/fix-webhook")
def fix():
    r = requests.post(EVOLUTION_URL + "/webhook/set/" + quote(INSTANCE), json={"webhook": {"enabled": True, "url": BOT_URL + "/webhook", "webhookByEvents": False, "webhookBase64": False, "events": ["MESSAGES_UPSERT"]}}, headers={"apikey": EVOLUTION_API_KEY}, timeout=20)
    return {"ok": True, "status": r.status_code, "result": r.text[:500]}

@app.get("/test-22")
def test22():
    send22()
    return {"ok": True, "logs": logs[-10:]}

@app.get("/test-17")
def test17():
    send17()
    return {"ok": True, "logs": logs[-10:]}
