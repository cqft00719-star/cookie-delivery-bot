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
