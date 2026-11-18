import os
import re
import json
import base64
import traceback
import io
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
COOKIE_GROUP_ID = os.getenv("COOKIE_GROUP_ID", "")
COOKIE_GROUP_ID = COOKIE_GROUP_ID or "85264352282-1512209129@g.us"
TMS_SHEET_ID = os.getenv("TMS_SHEET_ID", "")
TMS_SHEET_ID = TMS_SHEET_ID or "15X06a1oOFeta-ETz-CiLbB-5TUWS1rWKGi35HmBNNVE"
OCR_KEY = os.getenv("OCR_SPACE_API_KEY", "helloworld")
SHOPS = ["TSTN", "TSTG", "TST", "MK", "MPMK", "MOKO"]
SHOPS = SHOPS + ["CENT", "HSP", "CPE", "CWB", "CYW", "AT1"]
SHOPS = SHOPS + ["AT2", "ATRA", "APM", "KLN", "TM", "SH"]
app = FastAPI()
logs = []
cookie_photos = []
jobs_snapshot = {}

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
    sc = ["https://www.googleapis.com/auth/spreadsheets"]
    sc = sc + ["https://www.googleapis.com/auth/drive"]
    return Credentials.from_service_account_info(info, scopes=sc)

def sheet():
    import gspread
    return gspread.authorize(creds())

def send_text(n, t):
    try:
        url = EVOLUTION_URL + "/message/sendText/"
        url = url + quote(INSTANCE)
        head = {}
        head["apikey"] = EVOLUTION_API_KEY
        payload = {}
        payload["number"] = n
        payload["text"] = t
        requests.post(url, json=payload, headers=head, timeout=20)
    except Exception as e:
        log(e)

def send_xls(n, b, fn, cap):
    try:
        url = EVOLUTION_URL + "/message/sendMedia/"
        url = url + quote(INSTANCE)
        head = {}
        head["apikey"] = EVOLUTION_API_KEY
        b64 = base64.b64encode(b).decode()
        payload = {}
        payload["number"] = n
        payload["mediatype"] = "document"
        payload["mimetype"] = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        payload["caption"] = cap
        payload["media"] = b64
        payload["fileName"] = fn
        requests.post(url, json=payload, headers=head, timeout=60)
    except Exception as e:
        log(e)

def dl_img(data):
    try:
        url = EVOLUTION_URL + "/chat/getBase64FromMediaMessage/"
        url = url + quote(INSTANCE)
        key = data.get("key", {})
        msg = data.get("message", {})
        payload = {}
        payload["message"] = {}
        payload["message"]["key"] = key
        payload["message"]["message"] = msg
        head = {}
        head["apikey"] = EVOLUTION_API_KEY
        r = requests.post(url, json=payload, headers=head, timeout=60)
        j = r.json()
        b64 = j.get("base64") or ""
        if not b64:
            d2 = j.get("data", {})
            b64 = d2.get("base64", "")
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
                url = "https://api.ocr.space/parse/image"
                files = {}
                files["file"] = ("s.jpg", data, "image/jpeg")
                dd = {}
                dd["apikey"] = OCR_KEY
                dd["language"] = "eng"
                dd["OCREngine"] = eng
                dd["scale"] = "true"
                dd["isTable"] = "true"
                r = requests.post(url, files=files, data=dd, timeout=60)
                j = r.json()
                log("OCR raw " + str(j)[:600])
                t = ""
                try:
                    pr = j["ParsedResults"]
                    t = pr[0]["ParsedText"]
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
        return ""

def is_sgl(t):
    if not t:
        return False
    u = t.upper()
    if "SGL" in u:
        return True
    if "SCAN" in u:
        return True
    if "LOGISTICS" in u:
        return True
    if "出貨" in t:
        return True
    if "送貨單" in t:
        return True
    f = [s for s in SHOPS if s in u]
    n = re.findall(r"\b\d{1,3}\b", t)
    return len(f) >= 3 and len(n) >= 5

def parse(t):
    m = re.search(r"(\d{1,2})[-/. ](\d{1,2})[-/. ](202\d)", t)
    if m:
        d1 = str(int(m.group(1))).zfill(2)
        d2 = str(int(m.group(2))).zfill(2)
        ds = d1 + "-" + d2 + "-" + m.group(3)
    else:
        ds = datetime.now().strftime("%d-%m-%Y")
    m2 = re.search(r"總箱數[:：\s]*(\d+)", t)
    total = 0
    if m2:
        total = int(m2.group(1))
    if not total:
        ns = [int(x) for x in re.findall(r"\b(\d{2,3})\b", t)]
        if ns:
            total = max(ns)
    boxes = {}
    lines = t.replace(",", " ").splitlines()
    for s in SHOPS:
        boxes[s] = 0
        for line in lines:
            if s in line.upper():
                ns = [int(x) for x in re.findall(r"\b(\d{1,3})\b", line)]
                if ns:
                    boxes[s] = ns[-1]
    out = {}
    out["date"] = ds
    out["total"] = total
    out["boxes"] = boxes
    out["raw"] = t
    return out

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
    rv = []
    for s in SHOPS:
        v = p["boxes"].get(s, 0)
        if v == 0:
            rv.append("")
        else:
            rv.append(v)
    ws.update("B" + str(ri) + ":S" + str(ri), [rv])
    ws.update_cell(ri, 20, p["total"])
    return sd, ri

@app.post("/webhook")
async def webhook(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        ev = str(body.get("event", ""))
        if ev and "upsert" not in ev.lower():
            return {"ok": True}
        data = body.get("data") or {}
        key = data.get("key", {})
        jid = key.get("remoteJid") or ""
        msg = data.get("message", {}) or {}
        if jid != COOKIE_GROUP_ID:
            return {"ok": True}
        if not msg.get("imageMessage"):
            return {"ok": True}
        img = dl_img(data)
        if not img:
            return {"ok": True}
        cookie_photos.append({"img": img})
        log("COOKIE photo saved")
        log("COOKIE total " + str(len(cookie_photos)))
        return {"ok": True}
    except Exception as e:
        log("ERR " + str(e))
        log(traceback.format_exc())
        return JSONResponse({"ok": False}, status_code=200)

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
        d = None
        try:
            txt = r[date_idx].split(" ")[0]
            d = datetime.strptime(txt, "%Y-%m-%d").date()
        except Exception:
            try:
                txt = r[date_idx].split(" ")[0]
                d = datetime.strptime(txt, "%d-%m-%Y").date()
            except Exception:
                continue
        if d in [tomorrow, day_after]:
            filtered.append(r)
    if not filtered:
        txt2 = str(tomorrow) + "~" + str(day_after) + " 冇單"
        return None, txt2
    wb = openpyxl.Workbook()
    w2 = wb.active
    w2.title = "TMS Orders"
    want = ["InternalNo", "JobID", "Date", "Client", "Zone"]
    want = want + ["From", "To", "CargoDetail", "DeliveryTime"]
    want = want + ["Notes", "Status"]
    idxs = []
    for i, h in enumerate(headers):
        if h in want:
            idxs.append(i)
    head2 = []
    for i in idxs:
        head2.append(headers[i])
    w2.append(head2)
    for r in filtered:
        row2 = []
        for i in idxs:
            if i < len(r):
                row2.append(r[i])
            else:
                row2.append("")
        w2.append(row2)
    bio = io.BytesIO()
    wb.save(bio)
    bio.seek(0)
    info = str(tomorrow) + " to " + str(day_after)
    info = info + " 共 " + str(len(filtered)) + " 單"
    return bio.getvalue(), info

def check_jobs():
    try:
        ws = sheet().open_by_key(TMS_SHEET_ID).worksheet("Jobs")
        vals = ws.get_all_values()
        if len(vals) < 2:
            return
        headers = [h.strip() for h in vals[0]]
        id_idx = 0
        for c in ["InternalNo", "JobID", "單號"]:
            if c in headers:
                id_idx = headers.index(c)
                break
        st_idx = None
        for c in ["Status", "狀態"]:
            if c in headers:
                st_idx = headers.index(c)
                break
        first = len(jobs_snapshot) == 0
        for r in vals[1:]:
            if not r:
                continue
            if len(r) <= id_idx:
                continue
            if not r[id_idx].strip():
                continue
            jid = r[id_idx].strip()
            st = ""
            if st_idx is not None:
                if st_idx < len(r):
                    st = r[st_idx]
            line = " | ".join([x for x in r if x.strip()])
            if jid not in jobs_snapshot:
                jobs_snapshot[jid] = st
                if not first:
                    if TMS_GROUP_ID:
                        send_text(TMS_GROUP_ID, "TMS 新單\n" + line)
            elif jobs_snapshot[jid] != st:
                jobs_snapshot[jid] = st
                if TMS_GROUP_ID:
                    send_text(TMS_GROUP_ID, "TMS 狀態變更\n" + line)
    except Exception as e:
        log("check_jobs fail " + str(e))

def send17():
    if not TMS_GROUP_ID:
        return
    b, info = tms_daily_xls()
    if not b:
        txt = "TMS每日提醒 " + datetime.now().strftime("%Y-%m-%d")
        send_text(TMS_GROUP_ID, txt + " " + info)
    else:
        fn = "TMS_" + datetime.now().strftime("%Y-%m-%d") + ".xlsx"
        cap = "TMS每日提醒 " + datetime.now().strftime("%Y-%m-%d %H:%M")
        cap = cap + "\n" + info
        send_xls(TMS_GROUP_ID, b, fn, cap)

def send22():
    if not MY_WHATSAPP:
        log("No MY_WHATSAPP")
        return
    if not cookie_photos:
        send_text(MY_WHATSAPP, "22:00 曲奇：今日曲奇群組冇收到 SGL 相，冇嘢寫入。")
        return
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
        warn = "\n驗算唔啱：加埋=" + str(calc)
        warn = warn + "，單上總數=" + str(p["total"])
        warn = warn + "，已照單上總數寫入。"
    try:
        sd, row = write_sheet(p)
        b = cookie_xls(p)
        parts = []
        for k, v in p["boxes"].items():
            if v:
                parts.append(k + ":" + str(v))
        bs = ", ".join(parts)
        cap = p["date"] + " 曲奇送貨總結\n" + bs
        cap = cap + "\n總箱數:" + str(p["total"])
        cap = cap + "\n已寫入運輸總表 " + sd
        cap = cap + " 第" + str(row) + "行" + warn
        fn = "cookie-" + p["date"] + ".xlsx"
        send_xls(MY_WHATSAPP, b, fn, cap)
    except Exception as e:
        send_text(MY_WHATSAPP, "22:00 曲奇寫入失敗：" + str(e) + "\nOCR：" + t[:500])
    cookie_photos.clear()

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
sc = BackgroundScheduler(timezone="Asia/Hong_Kong")
sc.add_job(send17, CronTrigger(hour=17, minute=0))
sc.add_job(send22, CronTrigger(hour=22, minute=0))
sc.add_job(check_jobs, CronTrigger(minute="*/5"))
sc.start()

@app.get("/")
def root():
    out = {}
    out["status"] = "running"
    out["cookie_photos"] = len(cookie_photos)
    out["logs"] = logs[-20:]
    return out

@app.get("/fix-webhook")
def fix():
    url = EVOLUTION_URL + "/webhook/set/" + quote(INSTANCE)
    head = {}
    head["apikey"] = EVOLUTION_API_KEY
    payload = {}
    payload["webhook"] = {}
    payload["webhook"]["enabled"] = True
    payload["webhook"]["url"] = BOT_URL + "/webhook"
    payload["webhook"]["webhookByEvents"] = False
    payload["webhook"]["webhookBase64"] = False
    payload["webhook"]["events"] = ["MESSAGES_UPSERT"]
    r = requests.post(url, json=payload, headers=head, timeout=20)
    out = {}
    out["ok"] = True
    out["status"] = r.status_code
    out["result"] = r.text[:500]
    return out

@app.get("/test-22")
def test22():
    send22()
    out = {}
    out["ok"] = True
    out["logs"] = logs[-10:]
    return out

@app.get("/test-17")
def test17():
    send17()
    out = {}
    out["ok"] = True
    out["logs"] = logs[-10:]
    return out

@app.get("/test-jobs")
def testjobs():
    check_jobs()
    out = {}
    out["ok"] = True
    out["snapshot"] = jobs_snapshot
    out["logs"] = logs[-10:]
    return out
