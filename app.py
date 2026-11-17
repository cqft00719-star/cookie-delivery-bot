import os, re, json, base64, traceback, io, time
from datetime import datetime, timedelta
from urllib.parse import quote
import requests
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

# ============ 1. Railway 嗰 6 個 Variables，唔使再加 ============
EVOLUTION_URL = "https://evolution-api-production-5d91de27.up.railway.app"
EVOLUTION_API_KEY = os.getenv("EVOLUTION_API_KEY", "")
INSTANCE = "my WhatsApps"
BOT_URL = "https://cookie-delivery-bot-production.up.railway.app"
GCP_JSON = os.getenv("GCP_JSON", "")
MY_WHATSAPP = os.getenv("MY_WHATSAPP", "")
TMS_GROUP_ID = os.getenv("TMS_GROUP_ID", "")  # = 運輸(VIP單) Group by 2CM = 120363413020795341@g.us
COOKIE_GROUP_ID = os.getenv("COOKIE_GROUP_ID", "85264352282-1512209129@g.us")
TMS_SHEET_ID = os.getenv("TMS_SHEET_ID", "15X06a1oOFeta-ETz-CiLbB-5TUWS1rWKGi35HmBNNVE")

ALLOWED_GROUPS = [g for g in [TMS_GROUP_ID, COOKIE_GROUP_ID] if g]
SHOPS = ["TSTN","TSTG","TST","MK","MPMK","MOKO","CENT","HSP","CPE","CWB","CYW","AT1","AT2","ATRA","APM","KLN","TM","SH"]

app = FastAPI()
logs_cache = []
pending = {}
last_sgl_time = {}  # 防洗版：同一個人 2 分鐘只處理 1 張 SGL

def log(m):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {m}", flush=True)
    logs_cache.append(m)
    if len(logs_cache) > 200:
        logs_cache.pop(0)

# ============ 2. GCP / Sheet ============
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

# ============ 3. WhatsApp 發送 ============
def send_text(number, text):
    try:
        url = f"{EVOLUTION_URL}/message/sendText/{quote(INSTANCE)}"
        r = requests.post(url, json={"number": number, "text": text}, headers={"apikey": EVOLUTION_API_KEY}, timeout=20)
        log(f"SEND_TEXT to {number} status={r.status_code}")
    except Exception as e:
        log(f"SEND fail {e}")

def send_excel_bytes(number, file_bytes, filename, caption):
    try:
        b64 = base64.b64encode(file_bytes).decode()
        url = f"{EVOLUTION_URL}/message/sendMedia/{quote(INSTANCE)}"
        r = requests.post(url, json={
            "number": number,
            "mediatype": "document",
            "mimetype": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "caption": caption,
            "media": b64,
            "fileName": filename
        }, headers={"apikey": EVOLUTION_API_KEY}, timeout=60)
        log(f"SEND_EXCEL to {number} status={r.status_code} {r.text[:300]}")
        return r.status_code in [200, 201]
    except Exception as e:
        log(f"SEND_EXCEL fail {e}")
        return False

# ============ 4. 下載相 - FIX: 用啱 Endpoint ============
def download_image(data):
    #
