import os
import re
import json
import base64
import traceback
import io
import glob
from datetime import datetime, timedelta, timezone
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
TMS_GROUP_ID = os.getenv("TMS_GROUP_ID", "") or "120363413020795341@g.us"
COOKIE_GROUP_ID = os.getenv("COOKIE_GROUP_ID", "") or "85264352282-1512209129@g.us"
TMS_SHEET_ID = os.getenv("TMS_SHEET_ID", "") or "15X06a1oOFeta-ETz-CiLbB-5TUWS1rWKGi35HmBNNVE"
OCR_KEY = os.getenv("OCR_SPACE_API_KEY", "helloworld")
HKT = timezone(timedelta(hours=8))
def now_hk():
    return datetime.now(HKT)

SHOPS = ["TSTN", "TSTG", "TST", "MK", "MPMK", "MOKO", "CENT", "HSP", "CPE", "CWB", "CYW", "AT1", "AT2", "ATRA", "APM", "KLN", "TM", "SH"]
FEES = {"TSTN":200,"TSTG":200,"TST":200,"MK":200,"MPMK":200,"MOKO":200,"CENT":380,"HSP":380,"CPE":380,"CWB":380,"CYW":380,"AT1":500,"AT2":500,"ATRA":500,"APM":200,"KLN":200,"TM":250,"SH":250}
SCAN_PER_BOX = 5
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
_seen_msg_ids = set()
COOKIE_DIR = "/tmp/cookie_photos"
os.makedirs(COOKIE_DIR, exist_ok=True)

def log(m):
    print(m, flush=True)
    logs.append(str(m))
    if len(logs) > 200:
        logs.pop(0)

def persist_cookie_photo(img_bytes, mid):
    try:
        fn = os.path.join(COOKIE_DIR, f"{now_hk().strftime('%Y%m%d_%H%M%S')}_{mid[:12]}.jpg")
        with open(fn, "wb") as f:
            f.write(img_bytes)
        return fn
    except Exception as e:
        log(f"persist photo fail {e}")
        return ""
def load_persisted_photos():
    try:
        for fn in sorted(glob.glob(os.path.join(COOKIE_DIR, "*.jpg"))):
            try:
                with open(fn, "rb") as f:
                    img = f.read()
                if img and not any(p.get("fn") == fn for p in cookie_photos):
                    cookie_photos.append({"img": img, "jid": COOKIE_GROUP_ID, "ts": now_hk(), "mid": os.path.basename(fn), "fn": fn})
            except: pass
        if cookie_photos:
            log(f"loaded persisted cookie photos={len(cookie_photos)}")
    except Exception as e:
        log(f"load persisted fail {e}")
load_persisted_photos()

def creds():
    from google.oauth2.service_account import Credentials
    s = GCP_JSON.strip()
    if not s.startswith("{"):
        s = base64.b64decode(s).decode()
    info = json.loads(s)
    sc = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    return Credentials.from_service_account_info(info, scopes=sc)
def sheet():
    import gspread
    return gspread.authorize(creds())

def send_text(n, t):
    try:
        url = EVOLUTION_URL + "/message/sendText/" + quote(INSTANCE)
        head = {"apikey": EVOLUTION_API_KEY}
        r = requests.post(url, json={"number": n, "text": t}, headers=head, timeout=20)
        log("send_text to " + str(n) + " " + str(r.status_code) + " " + r.text[:200])
        return r.status_code in [200, 201]
    except Exception as e:
        log("send_text fail " + str(e))
        return False
def send_xls(n, b, fn, cap):
    try:
        url = EVOLUTION_URL + "/message/sendMedia/" + quote(INSTANCE)
        head = {"apikey": EVOLUTION_API_KEY}
        b64 = base64.b64encode(b).decode()
        payload = {"number": n, "mediatype": "document", "mimetype": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "caption": cap, "media": b64, "fileName": fn}
        r = requests.post(url, json=payload, headers=head, timeout=60)
        log("send_xls to " + str(n) + " " + str(r.status_code) + " " + r.text[:200])
        return r.status_code in [200, 201]
    except Exception as e:
        log(str(e))
        return False
def dl_img(data):
    try:
        url = EVOLUTION_URL + "/chat/getBase64FromMediaMessage/" + quote(INSTANCE)
        key = data.get("key", {})
        msg = data.get("message", {})
        head = {"apikey": EVOLUTION_API_KEY}
        for payload in [
            {"message": {"key": key, "message": msg}},
            {"key": key, "message": msg},
            {"message": msg, "key": key},
        ]:
            try:
                r = requests.post(url, json=payload, headers=head, timeout=60)
                log("dl_img status " + str(r.status_code))
                j = r.json()
                b64 = j.get("base64") or ""
                if not b64:
                    d2 = j.get("data", {})
                    if isinstance(d2, dict):
                        b64 = d2.get("base64", "") or d2.get("media", "")
                if b64:
                    if "," in b64:
                        b64 = b64.split(",", 1)[1]
                    return base64.b64decode(b64)
            except Exception as e:
                log(f"dl_img try fail {e}")
                continue
        log("dl_img no base64")
        return None
    except Exception as e:
        log("dl_img fail " + str(e))
        return None

def ocr(img):
    try:
        data = img
        try:
            from PIL import Image, ImageOps, ImageEnhance
            im = Image.open(io.BytesIO(img)).convert("RGB")
            im = ImageOps.exif_transpose(im)
            im.thumbnail((2200, 2200))
            im = ImageEnhance.Contrast(im).enhance(1.4)
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=90)
            data = buf.getvalue()
        except Exception as e:
            log("PIL skip " + str(e))
        for lang in ["cht", "eng", "chi_tra"]:
            for eng in ["2", "1"]:
                try:
                    url = "https://api.ocr.space/parse/image"
                    files = {"file": ("s.jpg", data, "image/jpeg")}
                    dd = {"apikey": OCR_KEY, "language": lang, "OCREngine": eng, "scale": "true", "isTable": "true"}
                    r = requests.post(url, files=files, data=dd, timeout=60)
                    j = r.json()
                    t = ""
                    try:
                        t = j["ParsedResults"][0]["ParsedText"]
                    except: t = ""
                    if t and "SCAN" in t.upper():
                        log(f"OCR lang={lang} eng={eng} OK len={len(t)}")
                        log("OCR:" + t[:1000])
                        return t
                    if t and t.strip():
                        log(f"OCR lang={lang} eng={eng} no SCAN, try next: {t[:200]}")
                except Exception as e:
                    log("OCR fail " + str(e))
        return ""
    except Exception as e:
        log(str(e))
        return ""

def is_sgl(t):
    if not t:
        return False, "OCR 冇文字"
    u = t.upper()
    has_scan = ("SCAN" in u and "GLOBAL" in u) or "SCAN GLOBAL" in u or ("SCAN" in u and "LOGISTICS" in u)
    has_table = ("SHOP" in u and "CODE" in u) or ("箱數" in t) or ("SHOP CODE" in u)
    has_id = any(x in t for x in ["身份證", "身份証", "身分證", "身份", "身分", "身份證號", "ID NO", "HKID"])
    has_total = ("總箱數" in t or "總箱" in t or "TOTAL" in u or "總數" in t)
    found = [s for s in SHOPS if s in u]
    score = (1 if has_scan else 0) + (1 if has_table else 0) + (1 if has_id else 0) + (1 if has_total else 0) + (1 if len(found) >= 3 else 0)
    if has_scan and has_table and len(found) >= 3:
        return True, f"PASS 呢款 SGL Form score={score} shops={found} has_id={has_id}"
    if not has_scan:
        return False, "冇 SCAN GLOBAL LOGISTICS"
    if not has_table:
        return False, "冇 Shop Code / 箱數 表頭，唔係呢款 SGL Form"
    if len(found) < 3:
        return False, f"舖頭得 {len(found)} 間 ({found})，少過 3 間，已 Skip"
    return False, f"分數唔夠 score={score}，已 Skip"

def parse(t):
    txt = t.replace("\r", "\n")
    ds = (now_hk() - timedelta(days=1)).strftime("%d-%m-%Y")
    dates = re.findall(r"(\d{1,2})[-/. ](\d{1,2})[-/. ](202\d)", txt)
    if dates:
        d1, d2, y = map(int, dates[-1])
        try:
            cand = datetime(y, d2, d1)
            if abs((now_hk().replace(tzinfo=None) - cand).days) > 60:
                cand = now_hk().replace(tzinfo=None) - timedelta(days=1)
            ds = cand.strftime("%d-%m-%Y")
        except:
            pass
    total = 0
    m2 = re.search(r"總箱數[^0-9]{0,10}(\d{1,3})", txt)
    if not m2:
        m2 = re.search(r"總數[^0-9]{0,10}(\d{1,3})", txt)
    if not m2:
        m2 = re.search(r"TOTAL[^0-9]{0,10}(\d{1,3})", txt.upper())
    if m2:
        total = int(m2.group(1))
    boxes = {s: 0 for s in SHOPS}
    tokens = re.findall(r"[A-Z0-9]+", txt.upper())
    log("parse tokens=" + str(tokens[:80]))
    norm_map = {"TSTH":"TSTN","TSTW":"TSTN","M0K0":"MOKO"}
    tokens = [norm_map.get(x, x) for x in tokens]
    for i, tok in enumerate(tokens):
        if tok in SHOPS:
            for j in range(i + 1, min(i + 4, len(tokens))):
                nt = tokens[j]
                if nt.isdigit():
                    v = int(nt)
                    if 0 < v <= 100 and boxes[tok] == 0:
                        boxes[tok] = v
                    break
                if nt in SHOPS:
                    break
    s = sum(boxes.values())
    if total == 10 and 90 <= s <= 110:
        total = s
    if not total:
        total = s
    out = {"date": ds, "total": total, "boxes": boxes, "raw": t}
    log("parse result date=" + ds + " total=" + str(total) + " boxes=" + str(boxes))
    return out

def validate_sgl_parse(p):
    s = sum(p["boxes"].values())
    if s == 0:
        return False, "所有舖頭都係 0 箱, 已 Skip"
    if p["total"] and abs(p["total"] - s) > 15:
        return False, f"總箱數 {p['total']} 同舖頭加埋 {s} 差超過 15, 可能 OCR 讀錯, 已 Skip, 請人手對"
    return True, "PASS"

def yellow(ws, ri, cols):
    try:
        for c in cols:
            col = chr(64 + c) if c <= 26 else "A" + chr(64 + c - 26)
            ws.format(col + str(ri), {"backgroundColor": {"red": 1, "green": 1, "blue": 0}})
    except Exception as e:
        log("yellow fail " + str(e))

def _ensure_header(ws, expected):
    try:
        vals = ws.get_all_values()
        if not vals:
            ws.update('A1', [expected], value_input_option='USER_ENTERED')
            return
        first = [h.strip() for h in vals[0]]
        if first[0] == "" or "200" in first or "380" in first or "500" in first:
            ws.update('A1', [expected], value_input_option='USER_ENTERED')
            return
        if first[0].lower() not in ["date", "日期"] and first[0] != "Date":
            ws.insert_row(expected, 1, value_input_option='USER_ENTERED')
    except Exception as e:
        log(f"ensure_header fail {e}")

def write_both(p, hl):
    import time as _time
    sh = sheet().open_by_key(TMS_SHEET_ID)
    dt = datetime.strptime(p["date"], "%d-%m-%Y")
    sd = dt.strftime("%d-%b")
    expected = ["Date"] + SHOPS + ["總箱數"]
    ws1 = sh.worksheet("運輸總表")
    _ensure_header(ws1, expected)
    vals1 = ws1.get_all_values()
    ri = None
    for i, r in enumerate(vals1[1:], start=2):
        if r and r[0].strip() == sd:
            ri = i; break
    if not ri:
        ri = len(vals1) + 1
        ws1.update_cell(ri, 1, sd)
    rv = [p["boxes"].get(s, 0) if p["boxes"].get(s, 0) != 0 else "" for s in SHOPS]
    for attempt in range(3):
        try:
            ws1.update(f"B{ri}:S{ri}", [rv]); break
        except Exception as e:
            if "429" in str(e): _time.sleep(5); continue
            raise
    ws1.update_cell(ri, 20, p["total"])
    ws2 = sh.worksheet("通行")
    _ensure_header(ws2, expected)
    vals2 = ws2.get_all_values()
    ri2 = None
    for i, r in enumerate(vals2[1:], start=2):
        if r and r[0].strip() == sd:
            ri2 = i; break
    if not ri2:
        ri2 = len(vals2) + 1
        ws2.update_cell(ri2, 1, sd)
    rv2 = []; fee_total = 0
    for s in SHOPS:
        v = p["boxes"].get(s, 0)
        if v == 0: rv2.append("")
        else:
            c = scan_cost_for(s, v); rv2.append(c); fee_total += c
    ws2.update(f"B{ri2}:S{ri2}", [rv2])
    ws2.update_cell(ri2, 20, fee_total)
    if hl:
        cols1 = [idx for idx, s in enumerate(SHOPS, start=2) if p["boxes"].get(s, 0) > 0]
        yellow(ws1, ri, cols1); yellow(ws1, ri, [20])
        yellow(ws2, ri2, cols1); yellow(ws2, ri2, [20])
    return sd, ri, fee_total

def wa_text(p, sd, fee_total):
    lines = [p["date"] + " 曲奇送貨已寫入"]
    for s in SHOPS:
        v = p["boxes"].get(s, 0)
        if v: lines.append(s + " " + str(v))
    lines.append("總箱數：" + str(p["total"]))
    lines.append("通行總數：$" + str(fee_total))
    lines.append("已寫入 運輸總表 + 通行 " + sd)
    lines.append("黃色格 = OCR 估嘅，請喺 Sheet 對一次")
    return "\n".join(lines)

def _is_cookie_group(jid):
    j = str(jid)
    if j == COOKIE_GROUP_ID: return True
    if "85264352282" in j or "1512209129" in j: return True
    return False

@app.post("/webhook")
async def webhook(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        last_webhook["time"] = now_hk().strftime("%H:%M:%S")
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
            return {"ok": True}
        msg = data.get("message", {}) if isinstance(data, dict) else {}
        inner = msg.get("viewOnceMessage", {}).get("message", {}) if msg.get("viewOnceMessage") else msg
        if msg.get("ephemeralMessage"):
            inner = msg.get("ephemeralMessage", {}).get("message", msg)
        cap = ""
        try:
            if inner.get("imageMessage"): cap = inner.get("imageMessage", {}).get("caption", "") or ""
            if inner.get("extendedTextMessage"): cap += " " + inner.get("extendedTextMessage", {}).get("text", "")
            if inner.get("conversation"): cap += " " + inner.get("conversation", "")
            if msg.get("imageMessage"): cap += " " + msg.get("imageMessage", {}).get("caption", "")
        except: cap = ""
        log("WEBHOOK caption=" + cap[:200])
        has_img = bool(inner.get("imageMessage") or inner.get("extendedTextMessage", {}).get("contextInfo", {}).get("quotedMessage", {}).get("imageMessage") or msg.get("imageMessage"))
        if not has_img:
            log("WEBHOOK no image, skip")
            return {"ok": True}
        mid = key.get("id", "") or ""
        if mid:
            if mid in _seen_msg_ids:
                log("WEBHOOK duplicate msg id, skip " + mid[:20])
                return {"ok": True}
            _seen_msg_ids.add(mid)
            if len(_seen_msg_ids) > 500: _seen_msg_ids.clear()
        # ===== FIX LID: 唔再靠電話號碼對 MY_WHATSAPP =====
        # 舊版：MY_WHATSAPP (852...) in jid (8050...@lid) -> 永遠對唔上 -> Skip
        # 新版：只要唔係 Group (@g.us)，就當係 DM，@lid 同 @s.whatsapp.net 都係 DM
        is_dm = ("@g.us" not in str(jid))
        if is_dm:
            img = dl_img({"key": key, "message": inner if inner != msg else msg})
            if not img:
                # DM 嗰陣要 Send 返去邊個？jid 本身就係 sender，唔好用 MY_WHATSAPP 電話號碼
                # 因為 Evolution 而家用 LID，Send 去 LID 先收到
                send_text(jid, "背後測試：收唔到張相，下載失敗。")
                return {"ok": True}
            t = ocr(img)
            ok, reason = is_sgl(t)
            if not ok:
                send_text(jid, "\n原因：" + reason + "\n\nOCR 讀到：\n" + t[:800])
                return {"ok": True}
            p = parse(t)
            ok2, reason2 = validate_sgl_parse(p)
            if not ok2:
                send_text(jid, "\n原因：" + reason2 + "\n\nOCR 讀到：\n" + t[:800])
                return {"ok": True}
            fee = sum([scan_cost_for(s, v) for s, v in p["boxes"].items() if v])
            txt_lines = [""]
            txt_lines.append("日期：" + p["date"] + " 總箱數：" + str(p["total"]) + " 通行：$" + str(fee))
            for s in SHOPS:
                if p["boxes"].get(s, 0): txt_lines.append(s + " " + str(p["boxes"][s]))
            txt_lines += ["", "⚠️ 呢個係測試，冇寫入 運輸總表 + 通行，冇 Send 去 Group。", "如果你確認呢張係真 SGL，先至去曲奇 Group Send 返一次，22:00 先會真係寫入。"]
            send_text(jid, "\n".join(txt_lines))
            return {"ok": True}
        if _is_cookie_group(jid):
            img = dl_img({"key": key, "message": inner if inner != msg else msg})
            if not img:
                log("WEBHOOK dl_img failed cookie")
                return {"ok": True}
            fn = persist_cookie_photo(img, mid or "noid")
            cookie_photos.append({"img": img, "jid": jid, "ts": now_hk(), "mid": mid, "fn": fn})
            log("COOKIE photo saved jid=" + str(jid) + " total=" + str(len(cookie_photos)))
            return {"ok": True}
        if TMS_GROUP_ID and jid == TMS_GROUP_ID:
            img = dl_img(data)
            if not img:
                log("WEBHOOK dl_img failed pod")
                return {"ok": True}
            mjob = re.search(r"JOB-[A-Z0-9\-]+", cap.upper())
            job = mjob.group(0) if mjob else ""
            pod_photos.append({"img": img, "jid": jid, "job": job, "caption": cap})
            if MY_WHATSAPP:
                if job: send_text(MY_WHATSAPP, "POD 收到 " + job + "\n喺 運輸 (VIP單) Group 收到 POD 相，Caption: " + cap[:100])
                else: send_text(MY_WHATSAPP, "POD 收到 (未寫單號)\n喺 運輸 (VIP單) Group 收到 POD 相，Caption: " + cap[:100] + "\n記得叫司機打埋 JOB 單號。")
            return {"ok": True}
        log("WEBHOOK not cookie / tms group, skip. got=" + str(jid))
        return {"ok": True}
    except Exception as e:
        log("WEBHOOK ERR " + str(e))
        log(traceback.format_exc())
        return JSONResponse({"ok": False}, status_code=200)

def cookie_xls_multi(results):
    import openpyxl
    wb = openpyxl.Workbook()
    w2 = wb.active; w2.title = "今日送貨"
    w2.append(["日期", "舖頭", "箱數", "通行"])
    grand_boxes = 0; grand_fee = 0
    for p, sd, fee_total in results:
        for s in SHOPS:
            v = p["boxes"].get(s, 0)
            if v: w2.append([p["date"], s, int(v), scan_cost_for(s, v)])
        w2.append([p["date"], "總數", p["total"], fee_total])
        grand_boxes += p["total"]; grand_fee += fee_total
    w2.append([]); w2.append(["合計", "", grand_boxes, grand_fee])
    bio = io.BytesIO(); wb.save(bio); bio.seek(0)
    return bio.getvalue()

def tms_daily_xls():
    import openpyxl
    ws = sheet().open_by_key(TMS_SHEET_ID).worksheet("Jobs")
    vals = ws.get_all_values()
    if len(vals) < 2: return None, "Jobs 冇資料"
    headers = [h.strip() for h in vals[0]]
    date_idx = None
    for c in ["Date", "送貨日期"]:
        if c in headers: date_idx = headers.index(c); break
    if date_idx is None: return None, "Jobs 冇 Date 欄"
    tomorrow = (now_hk() + timedelta(days=1)).date()
    day_after = tomorrow + timedelta(days=1)
    filtered = []
    for r in vals[1:]:
        d = None
        try:
            txt = r[date_idx].split(" ")[0]
            d = datetime.strptime(txt, "%Y-%m-%d").date()
        except:
            try:
                txt = r[date_idx].split(" ")[0]
                d = datetime.strptime(txt, "%d-%m-%Y").date()
            except: continue
        if d in [tomorrow, day_after]: filtered.append(r)
    if not filtered:
        return None, str(tomorrow) + "~" + str(day_after) + " 冇單"
    wb = openpyxl.Workbook(); w2 = wb.active; w2.title = "TMS Orders"
    want = ["InternalNo","JobID","Date","Client","Zone","From","To","CargoDetail","DeliveryTime","Notes","Status"]
    idxs = [i for i, h in enumerate(headers) if h in want]
    w2.append([headers[i] for i in idxs])
    for r in filtered:
        w2.append([r[i] if i < len(r) else "" for i in idxs])
    bio = io.BytesIO(); wb.save(bio); bio.seek(0)
    return bio.getvalue(), str(tomorrow) + " to " + str(day_after) + " 共 " + str(len(filtered)) + " 單"

def parse_created(s):
    try:
        s = str(s).strip()
        if not s: return None
        s = s.replace(" HKT", "")
        for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"]:
            try: return datetime.strptime(s, fmt)
            except: pass
        return None
    except: return None

def check_jobs():
    try:
        if not TMS_GROUP_ID: return
        ws = sheet().open_by_key(TMS_SHEET_ID).worksheet("Jobs")
        vals = ws.get_all_values()
        if len(vals) < 2: return
        headers = [h.strip() for h in vals[0]]
        id_idx = 0
        for c in ["InternalNo","JobID","單號"]:
            if c in headers: id_idx = headers.index(c); break
        st_idx = next((headers.index(c) for c in ["Status","狀態"] if c in headers), None)
        ca_idx = next((headers.index(c) for c in ["CreatedAt_HKT","CreatedAt","建立時間"] if c in headers), None)
        is_first = len(jobs_snapshot) == 0
        for r in vals[1:]:
            if not r or len(r) <= id_idx or not r[id_idx].strip(): continue
            jid = r[id_idx].strip()
            st = r[st_idx] if st_idx is not None and st_idx < len(r) else ""
            line = " | ".join([x for x in r if x.strip()])
            if jid not in jobs_snapshot:
                jobs_snapshot[jid] = st
                if is_first:
                    if st.strip().upper() in ["DELIVERED","COMPLETED","CANCELLED","CANCEL"]: continue
                    ca = parse_created(r[ca_idx]) if ca_idx is not None and ca_idx < len(r) else None
                    if ca is None: continue
                    if (now_hk().replace(tzinfo=None) - ca).total_seconds() > 900: continue
                    send_text(TMS_GROUP_ID, "TMS 新單\n" + line)
                else:
                    send_text(TMS_GROUP_ID, "TMS 新單\n" + line)
            elif jobs_snapshot[jid] != st:
                jobs_snapshot[jid] = st
                if is_first: continue
                send_text(TMS_GROUP_ID, "TMS 狀態變更\n" + line)
    except Exception as e:
        log("check_jobs fail " + str(e))

def write_daily_log(status):
    try:
        sh = sheet().open_by_key(TMS_SHEET_ID)
        try: ws = sh.worksheet("DailyLog")
        except:
            ws = sh.add_worksheet(title="DailyLog", rows=1000, cols=10)
            ws.update('A1', [["Date","Time_HKT","Status","Detail","TriggeredBy"]])
        ws.append_row([now_hk().strftime("%Y-%m-%d"), now_hk().strftime("%H:%M:%S"), status, str(now_hk()), "app.py-auto"], value_input_option='USER_ENTERED')
    except Exception as e:
        log("DailyLog write fail " + str(e))

def already_sent_today():
    try:
        sh = sheet().open_by_key(TMS_SHEET_ID)
        ws = sh.worksheet("DailyLog")
        vals = ws.get_all_values()
        today_s = now_hk().strftime("%Y-%m-%d")
        for r in reversed(vals[1:]):
            if r and len(r) >= 3 and r[0].strip() == today_s and "17:00" in str(r[2]):
                return True
        return False
    except Exception as e:
        log("already_sent_today fail " + str(e))
        return False

def send17():
    log(f"send17 trigger at {now_hk().strftime('%Y-%m-%d %H:%M:%S HKT')}")
    if not TMS_GROUP_ID:
        if MY_WHATSAPP: send_text(MY_WHATSAPP, "17:00 TMS 每日提醒失敗：Railway 入面 TMS_GROUP_ID 未設定")
        return
    if already_sent_today():
        log("send17 already sent today, skip"); return
    b, info = tms_daily_xls()
    if not b:
        send_text(TMS_GROUP_ID, "TMS每日提醒 " + now_hk().strftime("%Y-%m-%d") + " " + info)
    else:
        fn = "TMS_" + now_hk().strftime("%Y-%m-%d") + ".xlsx"
        send_xls(TMS_GROUP_ID, b, fn, "TMS每日提醒 " + now_hk().strftime("%Y-%m-%d %H:%M") + "\n" + info)
    write_daily_log("17:00 OK " + str(info)[:100])

def send22():
    load_persisted_photos()
    log("send22 start photos=" + str(len(cookie_photos)) + " my=" + str(MY_WHATSAPP))
    if not MY_WHATSAPP:
        log("send22 No MY_WHATSAPP"); return
    if not cookie_photos:
        send_text(MY_WHATSAPP, "22:00 曲奇：今日曲奇群組冇收到 SGL 相，冇嘢寫入。")
        return
    results = []; fail_msgs = []
    photos = list(cookie_photos); cookie_photos.clear()
    for p in photos:
        try:
            if p.get("fn") and os.path.exists(p["fn"]): os.remove(p["fn"])
        except: pass
    by_date = {}
    for idx, item in enumerate(photos):
        img = item.get("img") if isinstance(item, dict) else item
        t = ocr(img)
        ok, reason = is_sgl(t)
        if not ok:
            fail_msgs.append(f"第{idx+1}張唔係 SGL, 已 Skip: {reason}"); continue
        p = parse(t)
        ok2, reason2 = validate_sgl_parse(p)
        if not ok2:
            fail_msgs.append(f"第{idx+1}張 SGL 但總數對唔返, 已 Skip: {reason2}"); continue
        by_date[p["date"]] = p
    for date_s, p in by_date.items():
        try:
            sd, row, fee_total = write_both(p, True)
            results.append((p, sd, fee_total))
        except Exception as e:
            fail_msgs.append(f"{date_s} 寫入失敗：{e}")
            log("send22 write fail " + str(e))
    if not results:
        send_text(MY_WHATSAPP, "22:00 曲奇：今日 " + str(len(photos)) + " 張相, 冇一張係合格 SGL。\n" + "\n".join(fail_msgs)[:1500])
        return
    all_lines = []
    for p, sd, fee_total in results:
        all_lines.append(wa_text(p, sd, fee_total)); all_lines.append("---")
    if fail_msgs:
        all_lines.append("以下已 Skip："); all_lines.extend(fail_msgs)
    send_text(MY_WHATSAPP, "22:00 曲奇已寫入 " + str(len(results)) + " 張 SGL 單\n\n" + "\n".join(all_lines)[:3000])
    try:
        xb = cookie_xls_multi(results)
        send_xls(MY_WHATSAPP, xb, "Cookie_" + now_hk().strftime("%Y-%m-%d") + ".xlsx", "22:00 曲奇已寫入 " + str(len(results)) + " 張 SGL 單\n已寫入 運輸總表 + 通行")
    except Exception as e:
        send_text(MY_WHATSAPP, "22:00 曲奇 Excel Send 失敗：" + str(e))

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
sc = BackgroundScheduler(timezone="Asia/Hong_Kong")
sc.add_job(send17, CronTrigger(hour=17, minute=0, timezone="Asia/Hong_Kong"))
sc.add_job(send22, CronTrigger(hour=22, minute=0, timezone="Asia/Hong_Kong"))
sc.add_job(check_jobs, CronTrigger(minute="*/5", timezone="Asia/Hong_Kong"))
sc.start()

@app.get("/")
def root():
    return {"status": "running", "time_hkt": now_hk().strftime("%Y-%m-%d %H:%M:%S HKT"), "cookie_photos": len(cookie_photos), "pod_photos": len(pod_photos), "cookie_group_expected": COOKIE_GROUP_ID, "last_webhook": last_webhook, "tms_group": bool(TMS_GROUP_ID), "my_whatsapp": bool(MY_WHATSAPP), "jobs_seen": len(jobs_snapshot), "logs": logs[-30:]}

@app.get("/fix-webhook")
def fix():
    url = EVOLUTION_URL + "/webhook/set/" + quote(INSTANCE)
    head = {"apikey": EVOLUTION_API_KEY}
    payload = {"webhook": {"enabled": True, "url": BOT_URL + "/webhook", "webhookByEvents": False, "webhookBase64": False, "events": ["MESSAGES_UPSERT"]}}
    r = requests.post(url, json=payload, headers=head, timeout=20)
    log("fix-webhook " + str(r.status_code) + " " + r.text[:500])
    return {"ok": True, "status": r.status_code, "result": r.text[:800]}

@app.get("/check-webhook")
def checkwh():
    url = EVOLUTION_URL + "/webhook/find/" + quote(INSTANCE)
    r = requests.get(url, headers={"apikey": EVOLUTION_API_KEY}, timeout=20)
    return {"status": r.status_code, "result": r.text[:1500]}

@app.get("/fix-17")
def fix17():
    p = {"date": "17-11-2026", "total": 187, "boxes": {"TSTN":38,"TSTG":26,"TST":16,"MK":22,"MPMK":0,"MOKO":0,"CENT":29,"HSP":0,"CPE":15,"CWB":0,"CYW":17,"AT1":0,"AT2":0,"ATRA":16,"APM":8,"KLN":0,"TM":0,"SH":0}}
    sd, row, fee_total = write_both(p, False)
    return {"ok": True, "row": sd, "line": row, "fee_total": fee_total}

@app.get("/test-22")
def test22():
    send22()
    return {"ok": True, "logs": logs[-20:]}
@app.get("/test-17")
def test17():
    if not TMS_GROUP_ID: return {"ok": False, "logs": ["no TMS_GROUP_ID"]}
    b, info = tms_daily_xls()
    if not b: send_text(TMS_GROUP_ID, "TMS每日提醒 " + now_hk().strftime("%Y-%m-%d") + " " + info + " (手動測試)")
    else: send_xls(TMS_GROUP_ID, b, "TMS_" + now_hk().strftime("%Y-%m-%d") + ".xlsx", "TMS每日提醒 " + now_hk().strftime("%Y-%m-%d %H:%M") + "\n" + info + " (手動測試)")
    return {"ok": True, "logs": logs[-10:]}
@app.get("/test-jobs")
def testjobs():
    check_jobs()
    return {"ok": True, "snapshot": jobs_snapshot, "logs": logs[-10:]}
