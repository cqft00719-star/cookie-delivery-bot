import os, io, re, base64, requests, json
from fastapi import FastAPI, Request
from datetime import datetime, timedelta, date
from urllib.parse import quote
import gspread
from google.oauth2.service_account import Credentials
import pandas as pd
from apscheduler.schedulers.background import BackgroundScheduler

app = FastAPI()

EVOLUTION_URL = "https://evolution-api-production-5d91de27.up.railway.app"
INSTANCE = "my WhatsApps"
EVOLUTION_API_KEY = os.environ.get("EVOLUTION_API_KEY")
SHEET_ID = "15X06a1oOFeta-ETz-CiLbB-5TUWS1rWKGi35HmBNNVE"  # Cookie / 通行嗰張
TMS_SHEET_ID = os.environ.get("TMS_SHEET_ID", "")  # <- 要喺 Railway 填
MY_WHATSAPP = os.environ.get("MY_WHATSAPP")
TMS_GROUP_ID = os.environ.get("TMS_GROUP_ID", "120363413020795341@g.us")

SHOPS = ["TSTN","TSTG","TST","MK","MPMK","MOKO","CENT","HSP","CPE","CWB","CYW","AT1","AT2","ATRA","APM","KLN","TM","SH"]
MIN_CHARGE = [200,200,200,200,200,200,380,380,380,380,380,500,500,500,200,200,250,250]
PER_BOX = 5

def get_sheet(sheet_id=SHEET_ID):
    creds = Credentials.from_service_account_info(
        json.loads(os.environ.get("GCP_JSON")),
        scopes=["https://www.googleapis.com/auth/spreadsheets","https://www.googleapis.com/auth/drive"])
    gc = gspread.authorize(creds)
    return gc.open_by_key(sheet_id)

def cost_for(shop, boxes):
    idx = SHOPS.index(shop)
    mc = MIN_CHARGE[idx]
    included = mc // PER_BOX
    if boxes <= included:
        return boxes, mc
    return boxes, mc + (boxes - included) * PER_BOX

def send_evolution_media(to, file_path, caption):
    with open(file_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    url = f"{EVOLUTION_URL}/message/sendMedia/{quote(INSTANCE)}"
    r = requests.post(url, json={
        "number": to,
        "mediatype": "document",
        "mimetype": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "caption": caption,
        "media": b64,
        "fileName": os.path.basename(file_path)
    }, headers={"apikey": EVOLUTION_API_KEY}, timeout=60)
    print(f"Evolution send to {to}: {r.status_code} {r.text[:500]}")
    return r.status_code in [200, 201]

def send_evolution_text(to, text):
    url = f"{EVOLUTION_URL}/message/sendText/{quote(INSTANCE)}"
    r = requests.post(url, json={"number": to, "text": text},
                      headers={"apikey": EVOLUTION_API_KEY}, timeout=20)
    print(f"Evolution text: {r.status_code} {r.text[:300]}")
    return r.status_code in [200, 201]

@app.get("/")
def home():
    return {"status": "running", "instance": INSTANCE}

@app.post("/webhook")
async def webhook(req: Request):
    data = await req.json()
    print("WEBHOOK RECEIVED:", str(data)[:2000])
    return {"ok": True}

# --- 原有 22:00 Cookie 嗰個，保留 ---
def nightly_send():
    try:
        sh = get_sheet(SHEET_ID)
        ws = sh.worksheet("通行")
        df = pd.DataFrame(ws.get_all_values())
        path = "/tmp/每月物流成本節省記錄-Nov2026.xlsx"
        df.to_excel(path, index=False, header=False)
        send_evolution_media(MY_WHATSAPP, path, "今日累計 - 每月物流成本節省記錄 Nov2026 - 通行")
    except Exception as e:
        print("nightly error:", e)

# --- 新增：TMS 17:00 每日 Excel，成個由 tms.py 搬過嚟 ---
def tms_daily_send():
    try:
        if not TMS_SHEET_ID:
            print("TMS_SHEET_ID 未設定，跳過")
            return
        sh = get_sheet(TMS_SHEET_ID)
        ws = sh.worksheet("Jobs")
        vals = ws.get_all_values()
        if len(vals) < 2:
            send_evolution_text(TMS_GROUP_ID, f"📦 TMS每日提醒 {date.today()} 未來兩日無Order")
            return
        headers = [h.strip() for h in vals[0]]
        df = pd.DataFrame(vals[1:], columns=headers)
        # 搵 Date 欄
        date_col = next((c for c in ["Date","送貨日期","DeliveryDate"] if c in df.columns), None)
        if not date_col:
            print("TMS 無 Date 欄")
            return
        df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
        today = date.today()
        tomorrow = today + timedelta(days=1)
        day_after = tomorrow + timedelta(days=1)
        filtered = df[df[date_col].dt.date.isin([tomorrow, day_after])]
        if filtered.empty:
            send_evolution_text(TMS_GROUP_ID, f"📦 TMS每日提醒 {today} 聽日 {tomorrow} 同後日 {day_after} 無Order")
            _write_tms_log(sh, "OK 無單已發文字")
            return
        out_path = f"/tmp/TMS_Orders_{tomorrow}_to_{day_after}.xlsx"
        export_cols = [c for c in ["InternalNo","JobID","Date","Client","Zone","From","To","CargoDetail","DeliveryTime","Notes"] if c in filtered.columns]
        filtered[export_cols].to_excel(out_path, index=False)
        caption = f"📦 TMS每日提醒 {datetime.now().strftime('%Y-%m-%d %H:%M HKT')}\n聽日 {tomorrow} 同後日 {day_after} 共有 {len(filtered)} 張單"
        ok = send_evolution_media(TMS_GROUP_ID, out_path, caption)
        _write_tms_log(sh, f"{'OK' if ok else 'FAIL'} {len(filtered)}單")
    except Exception as e:
        print("tms_daily error:", e)

def _write_tms_log(sh, status):
    try:
        try:
            ws = sh.worksheet("DailyLog")
        except:
            ws = sh.add_worksheet(title="DailyLog", rows=1000, cols=10)
            ws.update('A1', [["Date","Time_HKT","Status","Detail","TriggeredBy"]])
        ws.append_row([str(date.today()), datetime.now().strftime("%H:%M:%S"), status, str(datetime.now()), "railway-auto"], value_input_option='USER_ENTERED')
    except Exception as e:
        print("DailyLog write fail", e)

# 手動測試用：開 https://.../test-tms 就會即刻發一次
@app.get("/test-tms")
def test_tms():
    tms_daily_send()
    return {"ok": True, "msg": "TMS test sent, 睇 Railway Log"}

@app.get("/test-nightly")
def test_nightly():
    nightly_send()
    return {"ok": True}

scheduler = BackgroundScheduler(timezone="Asia/Hong_Kong")
scheduler.add_job(nightly_send, "cron", hour=22, minute=0)
scheduler.add_job(tms_daily_send, "cron", hour=17, minute=0)
scheduler.start()
