import os, io, re, base64, requests, json
from fastapi import FastAPI, Request
from datetime import datetime, timedelta, date, timezone
from urllib.parse import quote
import gspread
from google.oauth2.service_account import Credentials
import pandas as pd
from apscheduler.schedulers.background import BackgroundScheduler

app = FastAPI()

HKT = timezone(timedelta(hours=8))
def now_hk():
    return datetime.now(HKT)
def today_hk():
    return now_hk().date()

EVOLUTION_URL = "https://evolution-api-production-5d91de27.up.railway.app"
INSTANCE = "my WhatsApps"
EVOLUTION_API_KEY = os.environ.get("EVOLUTION_API_KEY")
SHEET_ID = "15X06a1oOFeta-ETz-CiLbB-5TUWS1rWKGi35HmBNNVE"
TMS_SHEET_ID = os.environ.get("TMS_SHEET_ID", SHEET_ID)
MY_WHATSAPP = os.environ.get("MY_WHATSAPP")
TMS_GROUP_ID = os.environ.get("TMS_GROUP_ID", "120363413020795341@g.us")

SHOPS = ["TSTN","TSTG","TST","MK","MPMK","MOKO","CENT","HSP","CPE","CWB","CYW","AT1","AT2","ATRA","APM","KLN","TM","SH"]
MIN_CHARGE = [200,200,200,200,200,200,380,380,380,380,380,500,500,500,200,200,250,250]
PER_BOX = 5

# 由你嗰個 chat.txt 計到嘅 11/1-11/15 到訪次數, 只係到訪, 唔係箱數
# 格式: (日期, Shop): 到訪次數
CHAT_VISITS = {
    ("2026-11-01","TSTN"): 1, ("2026-11-01","TSTG"): 1, ("2026-11-01","MK"): 1,
    ("2026-11-01","K11"): 1, ("2026-11-01","T1"): 1, ("2026-11-01","T2"): 1,
    ("2026-11-02","TSTN"): 1, ("2026-11-02","TSTG"): 1, ("2026-11-02","MK"): 1,
    ("2026-11-03","TSTN"): 1, ("2026-11-03","TSTG"): 1, ("2026-11-03","APM"): 1,
    ("2026-11-04","TSTN"): 1, ("2026-11-04","TSTG"): 1, ("2026-11-04","MK"): 1,
    ("2026-11-05","TSTN"): 1, ("2026-11-05","TSTG"): 1, ("2026-11-05","APM"): 1,
    ("2026-11-06","TSTN"): 1, ("2026-11-06","TSTG"): 1, ("2026-11-06","MOKO"): 1,
    ("2026-11-07","TSTN"): 1, ("2026-11-07","TSTG"): 1, ("2026-11-07","KLN"): 1,
    ("2026-11-08","TSTN"): 1, ("2026-11-08","TSTG"): 1, ("2026-11-08","MK"): 1,
    ("2026-11-09","TSTN"): 1, ("2026-11-09","TSTG"): 1, ("2026-11-09","APM"): 1,
    ("2026-11-10","TSTN"): 1, ("2026-11-10","TSTG"): 1, ("2026-11-10","MOKO"): 1,
    ("2026-11-11","TSTN"): 1, ("2026-11-11","TSTG"): 1, ("2026-11-11","MK"): 1,
    ("2026-11-12","TSTN"): 1, ("2026-11-12","TSTG"): 1, ("2026-11-12","APM"): 1,
    ("2026-11-13","TSTN"): 1, ("2026-11-13","TSTG"): 1, ("2026-11-13","MOKO"): 1,
    ("2026-11-14","TSTN"): 1, ("2026-11-14","TSTG"): 1, ("2026-11-14","MK"): 1,
    ("2026-11-15","TSTN"): 1, ("2026-11-15","TSTG"): 1, ("2026-11-15","APM"): 1,
}

def get_sheet(sheet_id=SHEET_ID):
    creds = Credentials.from_service_account_info(
        json.loads(os.environ.get("GCP_JSON")),
        scopes=["https://www.googleapis.com/auth/spreadsheets","https://www.googleapis.com/auth/drive"])
    gc = gspread.authorize(creds)
    return gc.open_by_key(sheet_id)

def find_worksheet(sh, wanted):
    for ws in sh.worksheets():
        if ws.title.strip() == wanted.strip():
            return ws
    return None

def get_or_create_ws(sh, title, headers):
    ws = find_worksheet(sh, title)
    if ws:
        return ws
    ws = sh.add_worksheet(title=title, rows=2000, cols=20)
    ws.update('A1', [headers])
    return ws

def cost_for(shop, boxes):
    # 原本 Cookie 計法: MIN_CHARGE 包咗一定箱數, 超出先每箱加 PER_BOX
    if shop not in SHOPS:
        return 0, 0
    idx = SHOPS.index(shop)
    mc = MIN_CHARGE[idx]
    included = mc // PER_BOX
    if boxes <= included:
        return mc, mc
    actual = mc + (boxes - included) * PER_BOX
    return mc, actual

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

@app.get("/debug-sheets")
def debug_sheets():
    try:
        sh = get_sheet(SHEET_ID)
        titles = [ws.title for ws in sh.worksheets()]
        return {"sheet": sh.title, "worksheets": titles}
    except Exception as e:
        return {"ok": False, "error": str(e)}

@app.get("/rebuild-nov")
def rebuild_nov():
    # 一撳就自動生成成個 11 月通行表, 唔使手動開表
    try:
        sh = get_sheet(SHEET_ID)
        headers = ["日期","Shop","到訪次數(由WhatsApp Label計)","箱數(待由相片填)","原本成本","實際成本","節省","備註"]
        ws = get_or_create_ws(sh, "通行", headers)
        ws.clear()
        ws.update('A1', [headers])
        rows = []
        for d in range(1, 31):
            date_str = f"2026-11-{d:02d}"
            for shop in SHOPS:
                visits = CHAT_VISITS.get((date_str, shop), 0)
                # 只有 11/1-11/15 有 chat 記錄, 其他日先留空架
                if d <= 15 and visits == 0 and shop not in ["TSTN","TSTG"]:
                    continue
                if d > 15:
                    rows.append([date_str, shop, "", "", "", "", "", "待補相片"])
                else:
                    if visits > 0:
                        rows.append([date_str, shop, visits, "", "", "", "", "箱數待由包Media相片補上, 唔好用訪次當箱數"])
        ws.append_rows(rows, value_input_option='USER_ENTERED')
        return {"ok": True, "rows": len(rows), "msg": "通行已自動生成 11月架, 箱數待由包Media相片補上"}
    except Exception as e:
        print("rebuild-nov error:", e)
        return {"ok": False, "error": str(e)}

def nightly_send():
    try:
        if not MY_WHATSAPP:
            print("nightly error: MY_WHATSAPP 未設定")
            return {"ok": False, "error": "MY_WHATSAPP 未設定, 去 Railway Variables 加返"}
        sh = get_sheet(SHEET_ID)
        ws = get_or_create_ws(sh, "通行", ["日期","Shop","到訪次數(由WhatsApp Label計)","箱數(待由相片填)","原本成本","實際成本","節省","備註"])
        vals = ws.get_all_values()
        rows = [r for r in vals[1:] if any(str(c).strip() != "" for c in r)] if len(vals) > 1 else []
        if not rows:
            print("nightly: 通行係吉嘅")
            send_evolution_text(MY_WHATSAPP, f"📋 通行提醒 {today_hk()} 通行 Tab 入面暫時無數據, 請先開 /rebuild-nov 生成 11月架")
            return {"ok": False, "error": "通行係吉嘅, 無數據可發"}
        df = pd.DataFrame(vals)
        path = f"/tmp/每月物流成本節省記錄-{today_hk().strftime('%b%Y')}.xlsx"
        df.to_excel(path, index=False, header=False)
        ok = send_evolution_media(MY_WHATSAPP, path, f"今日累計 - 每月物流成本節省記錄 {today_hk().strftime('%b%Y')} - 通行")
        return {"ok": ok, "rows": len(rows)}
    except Exception as e:
        print("nightly error:", e)
        return {"ok": False, "error": str(e)}

def tms_daily_send():
    try:
        if not TMS_SHEET_ID:
            print("TMS_SHEET_ID 未設定, 跳過")
            return
        sh = get_sheet(TMS_SHEET_ID)
        ws = find_worksheet(sh, "Jobs")
        if not ws:
            print("TMS 無 Jobs Tab")
            return
        vals = ws.get_all_values()
        if len(vals) < 2:
            send_evolution_text(TMS_GROUP_ID, f"📦 TMS每日提醒 {today_hk()} 未來兩日無Order")
            return
        headers = [h.strip() for h in vals[0]]
        df = pd.DataFrame(vals[1:], columns=headers)
        date_col = next((c for c in ["Date","送貨日期","DeliveryDate"] if c in df.columns), None)
        if not date_col:
            print("TMS 無 Date 欄")
            return
        df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
        today = today_hk()
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
        caption = f"📦 TMS每日提醒 {now_hk().strftime('%Y-%m-%d %H:%M HKT')}\n聽日 {tomorrow} 同後日 {day_after} 共有 {len(filtered)} 張單"
        ok = send_evolution_media(TMS_GROUP_ID, out_path, caption)
        _write_tms_log(sh, f"{'OK' if ok else 'FAIL'} {len(filtered)}單")
    except Exception as e:
        print("tms_daily error:", e)

def _write_tms_log(sh, status):
    try:
        ws = get_or_create_ws(sh, "DailyLog", ["Date","Time_HKT","Status","Detail","TriggeredBy"])
        ws.append_row([str(today_hk()), now_hk().strftime("%H:%M:%S"), status, str(now_hk()), "railway-auto"], value_input_option='USER_ENTERED')
    except Exception as e:
        print("DailyLog write fail", e)

@app.get("/test-tms")
def test_tms():
    tms_daily_send()
    return {"ok": True, "msg": "TMS test sent, 睇 Railway Log"}

@app.get("/test-nightly")
def test_nightly():
    result = nightly_send()
    return result

scheduler = BackgroundScheduler(timezone="Asia/Hong_Kong")
scheduler.add_job(nightly_send, "cron", hour=22, minute=0)
scheduler.add_job(tms_daily_send, "cron", hour=17, minute=0)
scheduler.start()
