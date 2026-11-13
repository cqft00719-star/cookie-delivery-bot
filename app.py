import os, io, re, base64, requests
from fastapi import FastAPI, Request
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials
import pandas as pd
from apscheduler.schedulers.background import BackgroundScheduler

app = FastAPI()

EVOLUTION_URL = "https://evolution-api-production-5d91de27.up.railway.app"
INSTANCE = "my WhatsApps"
EVOLUTION_API_KEY = os.environ.get("EVOLUTION_API_KEY")
SHEET_ID = "15X06a1oOFeta-ETz-CiLbB-5TUWS1rWKGi35HmBNNVE"
MY_WHATSAPP = os.environ.get("MY_WHATSAPP")

SHOPS = ["TSTN","TSTG","TST","MK","MPMK","MOKO","CENT","HSP","CPE","CWB","CYW","AT1","AT2","ATRA","APM","KLN","TM","SH"]
MIN_CHARGE = [200,200,200,200,200,200,380,380,380,380,380,500,500,500,200,200,250,250]
PER_BOX = 5

def get_sheet():
    import json
    creds = Credentials.from_service_account_info(json.loads(os.environ.get("GCP_JSON")), scopes=["https://www.googleapis.com/auth/spreadsheets","https://www.googleapis.com/auth/drive"])
    gc = gspread.authorize(creds)
    return gc.open_by_key(SHEET_ID)

def cost_for(shop, boxes):
    idx = SHOPS.index(shop)
    mc = MIN_CHARGE[idx]
    included = mc // PER_BOX
    if boxes <= included:
        return boxes, mc
    return boxes, mc + (boxes - included) * PER_BOX

@app.get("/")
def home():
    return {"status": "running", "instance": INSTANCE}

@app.post("/webhook")
async def webhook(req: Request):
    data = await req.json()
    print("WEBHOOK RECEIVED:", str(data)[:2000])
    return {"ok": True}

def nightly_send():
    try:
        sh = get_sheet()
        ws = sh.worksheet("通行")
        df = pd.DataFrame(ws.get_all_values())
        df.to_excel("/tmp/每月物流成本節省記錄-Nov2026.xlsx", index=False, header=False)
        with open("/tmp/每月物流成本節省記錄-Nov2026.xlsx", "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        url = f"{EVOLUTION_URL}/message/sendMedia/{INSTANCE}"
        requests.post(url, json={
            "number": MY_WHATSAPP,
            "mediatype": "document",
            "mimetype": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "caption": "今日累計 - 每月物流成本節省記錄 Nov2026 - 通行",
            "media": b64,
            "fileName": "每月物流成本節省記錄-Nov2026.xlsx"
        }, headers={"apikey": EVOLUTION_API_KEY})
    except Exception as e:
        print("nightly error:", e)

scheduler = BackgroundScheduler(timezone="Asia/Hong_Kong")
scheduler.add_job(nightly_send, "cron", hour=22, minute=0)
scheduler.start()
