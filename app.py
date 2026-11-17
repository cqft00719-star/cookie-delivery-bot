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
ALLOWED_GROUPS = [g for g in [TMS_GROUP_ID, COOKIE_GROUP_ID] if g]
SHOPS = ["TSTN","TSTG","TST","MK","MPMK","MOKO","CENT","HSP","CPE","CWB","CYW","AT1","AT2","ATRA","APM","KLN","TM","SH"]

app = FastAPI()
logs_cache = []
pending = {}
# 防洗版：同一個人 30 秒內最多處理 3 張，唔好 29 張都出聲
recent_photos = {}

def log(m):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {m}", flush=True)
    logs_cache.append(m)
    if len(logs_cache) > 200: logs_cache.pop(0)

def get_gcp_creds():
    from google.oauth2.service_account import Credentials
    s = GCP_JSON.strip()
    if not s.startswith("{"):
        s = base64.b64decode(s).decode()
    info = json.loads(s)
    scopes = ["https://www.googleapis.com/auth/spreadsheets","https://www.googleapis.com/auth/drive","https://www.googleapis.com/auth/cloud-vision"]
    return Credentials.from_service_account_info(info, scopes=scopes), info

def get_sheet_client():
    import gspread
    creds, _ = get_gcp_creds()
    return gspread.authorize(creds)

def send_text(number, text):
    try:
        url = f"{EVOLUTION_URL}/message/sendText/{quote(INSTANCE)}"
        requests.post(url, json={"number": number, "text": text}, headers={"apikey": EVOLUTION_API_KEY}, timeout=20)
    except Exception as e: log(f"SEND fail {e}")

def send_excel_bytes(number, file_bytes, filename, caption):
    try:
        b64 = base64.b64encode(file_bytes).decode()
        url = f"{EVOLUTION_URL}/message/sendMedia/{quote(INSTANCE)}"
        r = requests.post(url, json={"number": number, "mediatype": "document", "mimetype": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "caption": caption, "media": b64, "fileName": filename}, headers={"apikey": EVOLUTION_API_KEY}, timeout=60)
        log(f"SEND_EXCEL {number} {r.status_code}")
        return r.status_code in [200, 201]
    except Exception as e:
        log(f"SEND_EXCEL fail {e}")
        return False

def download_image(data):
    try:
        url = f"{EVOLUTION_URL}/chat/getBase64FromMediaMessage/{quote(INSTANCE)}"
        r = requests.post(url, json={"message": {"key": data.get("key", {}), "message": data.get("message", {})}}, headers={"apikey": EVOLUTION_API_KEY}, timeout=60)
        j = r.json()
        b64 = j.get("base64") or j.get("data", {}).get("base64") or ""
        if not b64: return None
        if "," in b64: b64 = b64.split(",", 1)[1]
        return base64.b64decode(b64)
    except Exception as e:
        log(f"DL fail {e}")
        return None

def ocr_with_gcp_vision(img_bytes):
    try:
        from google.cloud import vision
        creds, _ = get_gcp_creds()
        client = vision.ImageAnnotatorClient(credentials=creds)
        image = vision.Image(content=img_bytes)
        resp = client.document_text_detection(image=image)
        txt = resp.full_text_annotation.text if resp.full_text_annotation else ""
        log(f"OCR: {txt[:800]}")
        return txt
    except Exception as e:
        log(f"VISION fail {e}")
        return ""

# === 核心：自己認係咪 SGL Logistics，唔使司機打 Caption ===
def is_sgl_logistics(txt):
    t = txt.upper()
    # 1. 有 SGL 字樣
    if "SGL" in t: return True
    if "SGL LOGISTICS" in t: return True
    if "出貨" in txt or "送貨單" in txt or "出貨清單" in txt: return True
    # 2. 冇 SGL 字，但有 3 間以上舖名 + 有數字，99% 係 SGL 單
    found_shops = [s for s in SHOPS if s in t]
    nums = re.findall(r"\b\d{1,3}\b", txt)
    if len(found_shops) >= 3 and len(nums) >= 5: return True
    return False

def parse_sgl_text(txt):
    m = re.search(r"(\d{1,2})[-/](\d{1,2})[-/](202\d)", txt)
    date_str = f"{int(m.group(1)):02d}-{int(m.group(2)):02d}-{m.group(3)}" if m else datetime.now().strftime("%d-%m-%Y")
    nums = [int(x) for x in re.findall(r"\b(\d{2,3})\b", txt)]
    total = max(nums) if nums else 0
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
    gc = get_sheet_client()
    sh = gc.open_by_key(TMS_SHEET_ID)
    ws = sh.worksheet("運輸總表")
    dt = datetime.strptime(parsed["date"], "%d-%m-%Y")
    sheet_date = dt.strftime("%d-%b")
    values = ws.get_all_values()
    row_idx = None
    for i, r in enumerate(values[1:], start=2):
        if r and r[0].strip() == sheet_date:
            row_idx = i; break
    if not row_idx:
        row_idx = len(values) + 1
        ws.update_cell(row_idx, 1, sheet_date)
    row_values = [parsed["boxes"].get(s, 0) if parsed["boxes"].get(s, 0) != 0 else "" for s in SHOPS]
    ws.update(f"B{row_idx}:S{row_idx}", [row_values])
    ws.update_cell(row_idx, 20, parsed["total"])
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
        msg = data.get("message", {}) or {}
        is_image = bool(msg.get("imageMessage"))
        text = extract_text(body)
        if remote_jid not in ALLOWED_GROUPS: return {"ok": True}
        if remote_jid == TMS_GROUP_ID: return {"ok": True}  # VIP 群由 tms.py 做

        low = text.strip().lower()
        if low in ["啱","確認","ok","yes","confirm","寫入"] and pending:
            last = list(pending.keys())[-1]
            parsed = pending[last]
            sd, row = write_to_sheet(parsed)
            send_text(remote_jid, f"✅ 已寫入運輸總表：{sd} 第 {row} 行，總箱數 {parsed['total']}")
            if MY_WHATSAPP: send_text(MY_WHATSAPP, f"✅ 已寫入運輸總表：{sd} 總箱數 {parsed['total']}")
            pending.clear()
            return {"ok": True}
        if low in ["唔啱","錯","no"] and pending:
            pending.clear()
            send_text(remote_jid, "❌ 已取消，請重新 Send 一張清楚啲嘅 SGL 單。")
            return {"ok": True}
        if not is_image: return {"ok": True}

        # === 唔騷擾司機：唔好再出警告，靜靜地處理 ===
        sender = data.get("key", {}).get("participant") or remote_jid
        now_ts = time.time()
        # 同一個人 30 秒內超過 3 張，之後嗰啲先唔處理，等佢 Send 完先，唔好洗版
        lst = [t for t in recent_photos.get(sender, []) if now_ts - t < 30]
        if len(lst) >= 3:
            log(f"SKIP burst sender={sender} - 等緊，唔騷擾")
            return {"ok": True}
        lst.append(now_ts)
        recent_photos[sender] = lst

        img = download_image(data)
        if not img:
            log("DL fail - 靜靜地唔出聲，唔騷擾司機")
            return {"ok": True}  # 唔再 send 警告

        txt = ocr_with_gcp_vision(img)
        # === 自己認：唔係 SGL 就靜靜地唔理 ===
        if not is_sgl_logistics(txt):
            log(f"IGNORE not SGL - 呢張係 POD / 送貨相，唔出聲")
            return {"ok": True}

        parsed = parse_sgl_text(txt)
        calc = sum(parsed["boxes"].values())
        log(f"SGL FOUND {parsed} calc={calc}")
        if calc != parsed["total"]:
            send_text(remote_jid, f"⚠️ 讀到 {parsed['date']}，加埋 {calc}，但單上總數 {parsed['total']}，驗算唔啱，唔會自動寫入。")
            return {"ok": True}
        pending[parsed["date"]] = parsed
        box_str = ", ".join([f"{k}:{v}" for k,v in parsed["boxes"].items() if v])
        send_text(remote_jid, f"📋 讀到 {parsed['date']} SGL單：\n{box_str}\n總箱數：{parsed['total']}\n\n啱請覆「啱」就會寫入運輸總表，錯請覆「唔啱」。")
        return {"ok": True}
    except Exception as e:
        log(f"ERR {e} {traceback.format_exc()}")
        return JSONResponse({"ok": False}, status_code=200)

# === 定時：17:00 VIP 群 / 22:00 你電話 ===
def make_cookie_excel():
    import openpyxl
    gc = get_sheet_client()
    sh = gc.open_by_key(TMS_SHEET_ID)
    ws = sh.worksheet("運輸總表")
    values = ws.get_all_values()
    today_str = datetime.now().strftime("%d-%b")
    row = None
    for r in values[1:]:
        if r and r[0].strip() == today_str: row = r; break
    if not row: return None, today_str
    wb = openpyxl.Workbook()
    ws2 = wb.active; ws2.title = "今日送貨"
    ws2.append(["日期","舖頭","箱數"])
    for shop, val in zip(SHOPS, row[1:19]):
        if val: ws2.append([today_str, shop, int(val)])
    ws2.append([today_str, "總數", row[19] if len(row) > 19 else ""])
    bio = io.BytesIO(); wb.save(bio); bio.seek(0)
    return bio.getvalue(), today_str

def make_tms_daily_excel():
    import openpyxl
    gc = get_sheet_client()
    sh = gc.open_by_key(TMS_SHEET_ID)
    try: ws = sh.worksheet("Jobs")
    except: return None, "無 Jobs"
    values = ws.get_all_values()
    if len(values) < 2: return None, "無 Jobs"
    headers = [h.strip() for h in values[0]]
    date_idx = next((headers.index(c) for c in ["Date","送貨日期"] if c in headers), None)
    if date_idx is None: return None, "無 Date"
    tomorrow = (datetime.now() + timedelta(days=1)).date()
    day_after = tomorrow + timedelta(days=1)
    filtered = []
    for r in values[1:]:
        try: d = datetime.strptime(r[date_idx].split(" ")[0], "%Y-%m-%d").date()
        except:
            try: d = datetime.strptime(r[date_idx].split(" ")[0], "%d-%m-%Y").date()
            except: continue
        if d in [tomorrow, day_after]: filtered.append(r)
    if not filtered: return None, f"{tomorrow}~{day_after} 無單"
    wb = openpyxl.Workbook(); ws2 = wb.active; ws2.title = "TMS Orders"
    idxs = [i for i,h in enumerate(headers) if h in ["InternalNo","JobID","Date","Client","Zone","From","To","CargoDetail","DeliveryTime","Notes"]]
    ws2.append([headers[i] for i in idxs])
    for r in filtered: ws2.append([r[i] if i < len(r) else "" for i in idxs])
    bio = io.BytesIO(); wb.save(bio); bio.seek(0)
    return bio.getvalue(), f"{tomorrow} to {day_after} 共 {len(filtered)} 單"

def send_daily_17_vip():
    if not TMS_GROUP_ID: return
    b, info = make_tms_daily_excel()
    if not b: send_text(TMS_GROUP_ID, f"📦 TMS每日提醒 {datetime.now().strftime('%Y-%m-%d')} {info}")
   
