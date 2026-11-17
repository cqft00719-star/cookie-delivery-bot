import os, re, json, base64, traceback, io, time
from datetime import datetime, timedelta
from urllib.parse import quote
import requests
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

# ============ 1. Railway Variables ============
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
SHOPS = ["TSTN","TSTG","TST","MK","MPMK","MOKO","CENT","HSP","CPE","CWB","CYW","AT1","AT2","ATRA","APM","KLN","TM","SH"]

app = FastAPI()
logs_cache = []
pending = {}
recent_photos = {}  # 防洗版

def log(m):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {m}", flush=True)
    logs_cache.append(m)
    if len(logs_cache) > 200:
        logs_cache.pop(0)

# ============ 2. GCP / Sheet (只係用嚟寫 Sheet，唔再用嚟 OCR) ============
def get_gcp_creds():
    from google.oauth2.service_account import Credentials
    s = GCP_JSON.strip()
    if not s.startswith("{"):
        s = base64.b64decode(s).decode()
    info = json.loads(s)
    scopes = ["https://www.googleapis.com/auth/spreadsheets","https://www.googleapis.com/auth/drive"]
    return Credentials.from_service_account_info(info, scopes=scopes), info

def get_sheet_client():
    import gspread
    creds, _ = get_gcp_creds()
    return gspread.authorize(creds)

# ============ 3. WhatsApp 發送 ============
def send_text(number, text):
    try:
        url = f"{EVOLUTION_URL}/message/sendText/{quote(INSTANCE)}"
        requests.post(url, json={"number": number, "text": text}, headers={"apikey": EVOLUTION_API_KEY}, timeout=20)
    except Exception as e:
        log(f"SEND fail {e}")

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

# ============ 4. 下載相 ============
def download_image(data):
    try:
        url = f"{EVOLUTION_URL}/chat/getBase64FromMediaMessage/{quote(INSTANCE)}"
        r = requests.post(url, json={"message": {"key": data.get("key", {}), "message": data.get("message", {})}}, headers={"apikey": EVOLUTION_API_KEY}, timeout=60)
        j = r.json()
        b64 = j.get("base64") or j.get("data", {}).get("base64") or ""
        if not b64:
            return None
        if "," in b64:
            b64 = b64.split(",", 1)[1]
        return base64.b64decode(b64)
    except Exception as e:
        log(f"DL fail {e}")
        return None

# ============ 5. OCR - 唔用 Google Vision，轉用 OCR.space，唔使 GCP 權限 ============
def ocr_with_gcp_vision(img_bytes):
    try:
        # 先壓細張相，OCR.space 免費版先食得落
        from PIL import Image
        import io as _io
        im = Image.open(_io.BytesIO(img_bytes)).convert("RGB")
        im.thumbnail((1200, 1200))
        buf = _io.BytesIO()
        im.save(buf, format="JPEG", quality=75)
        small = buf.getvalue()
        r = requests.post(
            "https://api.ocr.space/parse/image",
            files={"file": ("sgl.jpg", small, "image/jpeg")},
            data={"apikey": OCR_SPACE_API_KEY, "language": "eng", "OCREngine": "2", "scale": "true", "isTable": "true"},
            timeout=60
        )
        j = r.json()
        txt = ""
        try:
            txt = j["ParsedResults"][0]["ParsedText"]
        except:
            txt = ""
        log(f"OCR.space: {txt[:1000]}")
        # OCR.space 讀到就返，讀唔到先試 Vision 做後備，失敗都唔出聲
        if txt.strip():
            return txt
        log("OCR.space empty, try Vision fallback")
        try:
            from google.cloud import vision
            creds, _ = get_gcp_creds()
            # Vision 要 cloud-vision scope，上面 get_gcp_creds 冇加，呢度直接加
            from google.oauth2.service_account import Credentials
            s = GCP_JSON.strip()
            if not s.startswith("{"):
                s = base64.b64decode(s).decode()
            info = json.loads(s)
            creds2 = Credentials.from_service_account_info(info, scopes=["https://www.googleapis.com/auth/cloud-vision"])
            client = vision.ImageAnnotatorClient(credentials=creds2)
            image = vision.Image(content=img_bytes)
            resp = client.document_text_detection(image=image)
            txt2 = resp.full_text_annotation.text if resp.full_text_annotation else ""
            return txt2
        except Exception as e2:
            log(f"Vision fallback fail {e2} - 唔使理，已經唔靠佢")
            return ""
    except Exception as e:
        log(f"OCR fail {e}")
        return ""

# ============ 6. 自己認係咪 SGL Logistics ============
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
    # 冇 SGL 字，但有 3 間以上舖名 + 有數字，99% 係 SGL 單
    found_shops = [s for s in SHOPS if s in t]
    nums = re.findall(r"\b\d{1,3}\b", txt)
    if len(found_shops) >= 3 and len(nums) >= 5:
        return True
    return False

def parse_sgl_text(txt):
    m = re.search(r"(\d{1,2})[-/](\d{1,2})[-/](202\d)", txt)
    date_str = f"{int(m.group(1)):02d}-{int(m.group(2)):02d}-{m.group(3)}" if m else datetime.now().strftime("%d-%m-%Y")
    nums = [int(x) for x in re.findall(r"\b(\d{2,3})\b", txt)]a(days=1)
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
   
