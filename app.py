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
TMS_GROUP_ID = TMS_GROUP_ID or "120363413020795341@g.us"
COOKIE_GROUP_ID = os.getenv("COOKIE_GROUP_ID", "")
COOKIE_GROUP_ID = COOKIE_GROUP_ID or "85264352282-1512209129@g.us"
TMS_SHEET_ID = os.getenv("TMS_SHEET_ID", "")
TMS_SHEET_ID = TMS_SHEET_ID or "15X06a1oOFeta-ETz-CiLbB-5TUWS1rWKGi35HmBNNVE"
OCR_KEY = os.getenv("OCR_SPACE_API_KEY", "helloworld")
SHOPS = ["TSTN", "TSTG", "TST", "MK", "MPMK", "MOKO"]
SHOPS = SHOPS + ["CENT", "HSP", "CPE", "CWB", "CYW", "AT1"]
SHOPS = SHOPS + ["AT2", "ATRA", "APM", "KLN", "TM", "SH"]
FEES = {}
FEES["TSTN"] = 200
FEES["TSTG"] = 200
FEES["TST"] = 200
FEES["MK"] = 200
FEES["MPMK"] = 200
FEES["MOKO"] = 200
FEES["CENT"] = 380
FEES["HSP"] = 380
FEES["CPE"] = 380
FEES["CWB"] = 380
FEES["CYW"] = 380
FEES["AT1"] = 500
FEES["AT2"] = 500
FEES["ATRA"] = 500
FEES["APM"] = 200
FEES["KLN"] = 200
FEES["TM"] = 250
FEES["SH"] = 250
SCAN_PER_BOX = 5
# FIX: 通行計法要同 tms.py 一模一樣, 唔好再寫固定收費
def scan_cost_for(shop, boxes):
    if shop not in SHOPS:
        return 0
    mc = FEES.get(shop, 0)
    included = mc // SCAN_PER_BOX
    if boxes <= included:
        return mc
    return mc + (boxes - included) * SCAN_PER_BOX
app = FastAPI()
logs = []
cookie_photos = []
pod_photos = []
jobs_snapshot = {}
last_webhook = {}

def log(m):
    print(m, flush=True)
    logs.append(str(m))
    if len(logs) > 200:
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
        r = requests.post(url, json=payload, headers=head, timeout=20)
        log("send_text to " + str(n) + " " + str(r.status_code) + " " + r.text[:200])
        return r.status_code in [200, 201]
    except Exception as e:
        log("send_text fail " + str(e))
        return False

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
        r = requests.post(url, json=payload, headers=head, timeout=60)
        log("send_xls to " + str(n) + " " + str(r.status_code) + " " + r.text[:200])
        return r.status_code in [200, 201]
    except Exception as e:
        log(e)
        return False

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
        log("dl_img status " + str(r.status_code))
        j = r.json()
        b64 = j.get("base64") or ""
        if not b64:
            d2 = j.get("data", {})
            b64 = d2.get("base64", "")
        if not b64:
            log("dl_img no base64 keys=" + str(list(j.keys())[:10]))
            return None
        if "," in b64:
            b64 = b64.split(",", 1)[1]
        return base64.b64decode(b64)
    except Exception as e:
        log("dl_img fail " + str(e))
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
    txt = t.replace("\r", "\n")
    ds = None
    m = re.search(r"(\d{1,2})[-/. ](\d{1,2})[-/. ](202\d)", txt)
    if m:
        d1 = int(m.group(1))
        d2 = int(m.group(2))
        y = int(m.group(3))
        try:
            cand = datetime(y, d2, d1)
            diff = abs((datetime.now() - cand).days)
            if diff > 60 and d2 == 1:
                cand2 = datetime(y, 11, d1)
                if abs((datetime.now() - cand2).days) < 60:
                    cand = cand2
                else:
                    cand = datetime.now() - timedelta(days=1)
            elif diff > 60:
                cand = datetime.now() - timedelta(days=1)
            ds = cand.strftime("%d-%m-%Y")
        except:
            ds = (datetime.now() - timedelta(days=1)).strftime("%d-%m-%Y")
    else:
        ds = (datetime.now() - timedelta(days=1)).strftime("%d-%m-%Y")
    total = 0
    m2 = re.search(r"總箱數[^0-9]{0,10}(\d{2,3})", txt)
    if m2:
        total = int(m2.group(1))
    boxes = {}
    for s in SHOPS:
        boxes[s] = 0
    tokens = re.findall(r"[A-Z0-9]+", txt.upper())
    log("parse tokens=" + str(tokens[:60]))
    for i, tok in enumerate(tokens):
        if tok in SHOPS:
            for j in range(i + 1, min(i + 4, len(tokens))):
                nt = tokens[j]
                if nt.isdigit():
                    v = int(nt)
                    if 0 < v <= 100:
                        if boxes[tok] == 0:
                            boxes[tok] = v
                    break
                if nt in SHOPS:
                    break
    if not total:
        total = sum(boxes.values())
    out = {}
    out["date"] = ds
    out["total"] = total
    out["boxes"] = boxes
    out["raw"] = t
    log("parse result date=" + ds + " total=" + str(total) + " boxes=" + str(boxes))
    return out

def yellow(ws, ri, cols):
    try:
        for c in cols:
            col = chr(64 + c)
            cell = col + str(ri)
            ws.format(cell, {"backgroundColor": {"red": 1, "green": 1, "blue": 0}})
    except Exception as e:
        log("yellow fail " + str(e))

def write_both(p, hl):
    sh = sheet().open_by_key(TMS_SHEET_ID)
    dt = datetime.strptime(p["date"], "%d-%m-%Y")
    sd = dt.strftime("%d-%b")
    ws1 = sh.worksheet("運輸總表")
    vals1 = ws1.get_all_values()
    ri = None
    for i, r in enumerate(vals1[1:], start=2):
        if r and r[0].strip() == sd:
            ri = i
            break
    if not ri:
        ri = len(vals1) + 1
        ws1.update_cell(ri, 1, sd)
    rv = []
    for s in SHOPS:
        v = p["boxes"].get(s, 0)
        if v == 0:
            rv.append("")
        else:
            rv.append(v)
    ws1.update("B" + str(ri) + ":S" + str(ri), [rv])
    ws1.update_cell(ri, 20, p["total"])
    ws2 = sh.worksheet("通行")
    vals2 = ws2.get_all_values()
    ri2 = None
    for i, r in enumerate(vals2[1:], start=2):
        if r and r[0].strip() == sd:
            ri2 = i
            break
    if not ri2:
        ri2 = len(vals2) + 1
        ws2.update_cell(ri2, 1, sd)
    rv2 = []
    fee_total = 0
    for s in SHOPS:
        v = p["boxes"].get(s, 0)
        if v == 0:
            rv2.append("")
        else:
            # FIX: 唔好再寫固定收費, 要用 scan_cost_for() 計埋超額, 同 tms.py 一樣
            c = scan_cost_for(s, v)
            rv2.append(c)
            fee_total = fee_total + c
    ws2.update("B" + str(ri2) + ":S" + str(ri2), [rv2])
    ws2.update_cell(ri2, 20, fee_total)
    if hl:
        cols1 = []
        for idx, s in enumerate(SHOPS, start=2):
            if p["boxes"].get(s, 0) > 0:
                cols1.append(idx)
        yellow(ws1, ri, cols1)
        yellow(ws1, ri, [20])
        yellow(ws2, ri2, cols1)
        yellow(ws2, ri2, [20])
    return sd, ri, fee_total

def wa_text(p, sd, fee_total):
    lines = []
    lines.append(p["date"] + " 曲奇送貨已寫入")
    for s in SHOPS:
        v = p["boxes"].get(s, 0)
        if v:
            lines.append(s + " " + str(v))
    lines.append("總箱數：" + str(p["total"]))
    lines.append("通行總數：$" + str(fee_total))
    lines.append("已寫入 運輸總表 + 通行 " + sd)
    lines.append("黃色格 = OCR 估嘅，請喺 Sheet 對一次")
    return "\n".join(lines)

@app.post("/webhook")
async def webhook(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        last_webhook["time"] = datetime.now().strftime("%H:%M:%S")
        last_webhook["event"] = str(body.get("event", ""))
        last_webhook["body_keys"] = str(list(body.keys())[:10])
        ev = str(body.get("event", ""))
        log("WEBHOOK hit event=" + ev + " keys=" + str(list(body.keys())[:10]))
        data = body.get("data") or body
        if isinstance(data, dict) and "key" not in data and "message" in body:
            data = body
        key = data.get("key", {}) if isinstance(data, dict) else {}
        jid = key.get("remoteJid") or key.get("remoteJidAlt") or ""
        if not jid and isinstance(data, dict):
            jid = data.get("remoteJid") or ""
        last_webhook["jid"] = jid
        log("WEBHOOK jid=" + str(jid) + " expect_cookie=" + COOKIE_GROUP_ID + " tms=" + TMS_GROUP_ID + " fromMe=" + str(key.get("fromMe", False)))
        if ev and "upsert" not in ev.lower():
            log("WEBHOOK skip non-upsert")
            return {"ok": True}
        msg = data.get("message", {}) if isinstance(data, dict) else {}
        log("WEBHOOK msg_keys=" + str(list(msg.keys())[:10]))
        cap = ""
        try:
            if msg.get("imageMessage"):
                cap = msg.get("imageMessage", {}).get("caption", "") or ""
            if msg.get("extendedTextMessage"):
                cap = cap + " " + msg.get("extendedTextMessage", {}).get("text", "")
            if msg.get("conversation"):
                cap = cap + " " + msg.get("conversation", "")
        except Exception:
            cap = ""
        log("WEBHOOK caption=" + cap[:200])
        has_img = bool(msg.get("imageMessage") or msg.get("extendedTextMessage", {}).get("contextInfo", {}).get("quotedMessage", {}).get("imageMessage"))
        if not has_img:
            log("WEBHOOK no image, skip")
            return {"ok": True}
        if jid == COOKIE_GROUP_ID:
            img = dl_img(data)
            if not img:
                log("WEBHOOK dl_img failed cookie")
                return {"ok": True}
            cookie_photos.append({"img": img, "jid": jid})
            log("COOKIE photo saved jid=" + str(jid) + " total=" + str(len(cookie_photos)))
            return {"ok": True}
        if TMS_GROUP_ID and jid == TMS_GROUP_ID:
            img = dl_img(data)
            if not img:
                log("WEBHOOK dl_img failed pod")
                return {"ok": True}
            mjob = re.search(r"JOB-[A-Z0-9\-]+", cap.upper())
            job = ""
            if mjob:
                job = mjob.group(0)
            pod_photos.append({"img": img, "jid": jid, "job": job, "caption": cap})
            log("POD photo saved jid=" + str(jid) + " job=" + job + " total=" + str(len(pod_photos)))
            if MY_WHATSAPP:
                if job:
                    send_text(MY_WHATSAPP, "POD 收到 " + job + "\n喺 運輸 (VIP單) Group 收到 POD 相，Apps 上傳唔到都唔緊要，呢度已經收到。\nCaption: " + cap[:100])
                else:
                    send_text(MY_WHATSAPP, "POD 收到 (未寫單號)\n喺 運輸 (VIP單) Group 收到 POD 相，Caption: " + cap[:100] + "\n記得叫司機打埋 JOB 單號。")
            return {"ok": True}
        log("WEBHOOK not cookie / tms group, skip. got=" + str(jid))
        return {"ok": True}
    except Exception as e:
        log("WEBHOOK ERR " + str(e))
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

def cookie_xls_multi(results):
    # FIX: 22:00 要 Send 嘅 Excel, 唔係得一張相, 係成日所有相加埋
    import openpyxl
    wb = openpyxl.Workbook()
    w2 = wb.active
    w2.title = "今日送貨"
    w2.append(["日期", "舖頭", "箱數", "通行"])
    grand_boxes = 0
    grand_fee = 0
    for p, sd, fee_total in results:
        for s in SHOPS:
            v = p["boxes"].get(s, 0)
            if v:
                w2.append([p["date"], s, int(v), scan_cost_for(s, v)])
        w2.append([p["date"], "總數", p["total"], fee_total])
        grand_boxes += p["total"]
        grand_fee += fee_total
    w2.append([])
    w2.append(["合計", "", grand_boxes, grand_fee])
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

def parse_created(s):
    try:
        s = str(s).strip()
        if not s:
            return None
        s = s.replace(" HKT", "")
        for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"]:
            try:
                return datetime.strptime(s, fmt)
            except:
                pass
        return None
    except:
        return None

def check_jobs():
    try:
        if not TMS_GROUP_ID:
            log("check_jobs no TMS_GROUP_ID")
            return
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
        ca_idx = None
        for c in ["CreatedAt_HKT", "CreatedAt", "建立時間"]:
            if c in headers:
                ca_idx = headers.index(c)
                break
        is_first = len(jobs_snapshot) == 0
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
                if is_first:
                    up = st.strip().upper()
                    if up in ["DELIVERED", "COMPLETED", "CANCELLED", "CANCEL"]:
                        continue
                    ca = None
                    if ca_idx is not None and ca_idx < len(r):
                        ca = parse_created(r[ca_idx])
                    if ca is None:
                        continue
                    diff = datetime.now() - ca
                    if diff.total_seconds() > 900:
                        continue
                    send_text(TMS_GROUP_ID, "TMS 新單\n" + line)
                else:
                    send_text(TMS_GROUP_ID, "TMS 新單\n" + line)
            elif jobs_snapshot[jid] != st:
                jobs_snapshot[jid] = st
                if is_first:
                    continue
                send_text(TMS_GROUP_ID, "TMS 狀態變更\n" + line)
    except Exception as e:
        log("check_jobs fail " + str(e))

def write_daily_log(status):
    try:
        sh = sheet().open_by_key(TMS_SHEET_ID)
        try:
            ws = sh.worksheet("DailyLog")
        except:
            ws = sh.add_worksheet(title="DailyLog", rows=1000, cols=10)
            ws.update('A1', [["Date","Time_HKT","Status","Detail","TriggeredBy"]])
        ws.append_row([datetime.now().strftime("%Y-%m-%d"), datetime.now().strftime("%H:%M:%S"), status, str(datetime.now()), "app.py-auto"], value_input_option='USER_ENTERED')
    except Exception as e:
        log("DailyLog write fail " + str(e))

def send17():
    # FIX: 唔好再靜靜哋唔 Send, 冇 TMS_GROUP_ID 就要話返你知
    if not TMS_GROUP_ID:
        log("send17 no TMS_GROUP_ID")
        if MY_WHATSAPP:
            send_text(MY_WHATSAPP, "17:00 TMS 每日提醒失敗：Railway 入面 TMS_GROUP_ID 未設定，冇 Send 去 運輸 (VIP單) Group。")
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
    # 寫返入 DailyLog, 等 tms.py 嗰邊唔會重複 Send
    write_daily_log("17:00 OK " + str(info)[:100])

def send22():
    log("send22 start photos=" + str(len(cookie_photos)) + " my=" + str(MY_WHATSAPP))
    if not MY_WHATSAPP:
        log("send22 No MY_WHATSAPP")
        return
    if not cookie_photos:
        send_text(MY_WHATSAPP, "22:00 曲奇：今日曲奇群組冇收到 SGL 相，冇嘢寫入。")
        log("send22 no photo sent")
        return
    # FIX: 唔好再淨係攞最後一張, 要逐張認晒
    results = []
    fail_msgs = []
    photos = list(cookie_photos)
    cookie_photos.clear()
    for idx, item in enumerate(photos):
        img = item.get("img") if isinstance(item, dict) else item
        t = ocr(img)
        log("send22 photo " + str(idx+1) + "/" + str(len(photos)) + " ocr len=" + str(len(t)))
        if not is_sgl(t):
            fail_msgs.append(f"第{idx+1}張 OCR 讀唔到 SGL：{t[:200]}")
            continue
        p = parse(t)
        try:
            sd, row, fee_total = write_both(p, True)
            results.append((p, sd, fee_total))
            log("send22 wrote " + sd)
        except Exception as e:
            fail_msgs.append(f"第{idx+1}張 寫入失敗：{e} OCR：{t[:200]}")
            log("send22 write fail " + str(e))
    if not results:
        send_text(MY_WHATSAPP, "22:00 曲奇：今日 " + str(len(photos)) + " 張相都認唔到 / 寫唔入。\n" + "\n".join(fail_msgs)[:1000])
        return
    # 文字 + Excel 一齊 Send 俾你自己, 唔係淨係 Send 文字
    all_lines = []
    for p, sd, fee_total in results:
        all_lines.append(wa_text(p, sd, fee_total))
        all_lines.append("---")
    if fail_msgs:
        all_lines.append("以下未能寫入：")
        all_lines.extend(fail_msgs)
    send_text(MY_WHATSAPP, "22:00 曲奇已寫入 " + str(len(results)) + " 張相\n\n" + "\n".join(all_lines)[:3000])
    try:
        xb = cookie_xls_multi(results)
        fn = "Cookie_" + datetime.now().strftime("%Y-%m-%d") + ".xlsx"
        cap = "22:00 曲奇已寫入 " + str(len(results)) + " 張相\n已寫入 運輸總表 + 通行，Excel 入面有晒今日所有舖頭箱數 + 通行。"
        send_xls(MY_WHATSAPP, xb, fn, cap)
    except Exception as e:
        log("send22 xls fail " + str(e))
        send_text(MY_WHATSAPP, "22:00 曲奇 Excel Send 失敗：" + str(e))

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
    out["pod_photos"] = len(pod_photos)
    out["cookie_group_expected"] = COOKIE_GROUP_ID
    out["last_webhook"] = last_webhook
    out["tms_group"] = bool(TMS_GROUP_ID)
    out["my_whatsapp"] = bool(MY_WHATSAPP)
    out["jobs_seen"] = len(jobs_snapshot)
    out["logs"] = logs[-30:]
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
    out["result"] = r.text[:800]
    log("fix-webhook " + str(r.status_code) + " " + r.text[:500])
    return out

@app.get("/check-webhook")
def checkwh():
    url = EVOLUTION_URL + "/webhook/find/" + quote(INSTANCE)
    head = {}
    head["apikey"] = EVOLUTION_API_KEY
    r = requests.get(url, headers=head, timeout=20)
    out = {}
    out["status"] = r.status_code
    out["result"] = r.text[:1500]
    return out

@app.get("/fix-17")
def fix17():
    p = {}
    p["date"] = "17-11-2026"
    p["total"] = 187
    p["boxes"] = {}
    p["boxes"]["TSTN"] = 38
    p["boxes"]["TSTG"] = 26
    p["boxes"]["TST"] = 16
    p["boxes"]["MK"] = 22
    p["boxes"]["MPMK"] = 0
    p["boxes"]["MOKO"] = 0
    p["boxes"]["CENT"] = 29
    p["boxes"]["HSP"] = 0
    p["boxes"]["CPE"] = 15
    p["boxes"]["CWB"] = 0
    p["boxes"]["CYW"] = 17
    p["boxes"]["AT1"] = 0
    p["boxes"]["AT2"] = 0
    p["boxes"]["ATRA"] = 16
    p["boxes"]["APM"] = 8
    p["boxes"]["KLN"] = 0
    p["boxes"]["TM"] = 0
    p["boxes"]["SH"] = 0
    sd, row, fee_total = write_both(p, False)
    out = {}
    out["ok"] = True
    out["row"] = sd
    out["line"] = row
    out["fee_total"] = fee_total
    return out

@app.get("/test-22")
def test22():
    send22()
    out = {}
    out["ok"] = True
    out["logs"] = logs[-20:]
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
