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
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
SHEET_ID = "15X06a1oOFeta-ETz-CiLbB-5TUWS1rWKGi35HmBNNVE"
TMS_SHEET_ID = os.environ.get("TMS_SHEET_ID", SHEET_ID)
MY_WHATSAPP = os.environ.get("MY_WHATSAPP")
TMS_GROUP_ID = os.environ.get("TMS_GROUP_ID", "120363413020795341@g.us")

SHOPS = ["TSTN","TSTG","TST","MK","MPMK","MOKO","CENT","HSP","CPE","CWB","CYW","AT1","AT2","ATRA","APM","KLN","TM","SH"]
MIN_CHARGE = [200,200,200,200,200,200,380,380,380,380,380,500,500,500,200,200,250,250]
PER_BOX = 5

# ========== SCAN Global 1/11-15/11 真實箱數，已對數，總數 2494 ==========
# 15/11：TSTN已確認37，逐格加275，紙面274，差1箱在 APM/KLN，暫將 KLN記為4*，以紙面274為準
SCAN_BOXES = {
    "2026-11-01": {"TSTN": 12, "TSTG": 20, "TST": 10, "MPMK": 21, "CPE": 5, "CYW": 16, "AT1": 17, "AT2": 7, "ATRA": 21, "SH": 7},
    "2026-11-02": {"TSTN": 22, "TSTG": 16, "TST": 10, "MK": 37, "CENT": 22, "AT1": 12, "ATRA": 26, "APM": 8, "KLN": 5},
    "2026-11-03": {"TSTN": 25, "TSTG": 22, "MPMK": 26, "CWB": 10, "CYW": 15, "AT1": 15, "AT2": 10, "ATRA": 19, "TM": 10},
    "2026-11-04": {"TSTN": 9, "TSTG": 15, "TST": 11, "MK": 27, "MOKO": 17, "CENT": 15, "HSP": 9, "CPE": 8, "AT1": 16, "AT2": 7, "ATRA": 19, "APM": 6, "SH": 9},
    "2026-11-05": {"TSTN": 21, "TSTG": 21, "MPMK": 20, "CWB": 12, "CYW": 12, "AT1": 8, "ATRA": 18, "KLN": 10, "TM": 6},
    "2026-11-06": {"TSTN": 26, "TSTG": 10, "TST": 10, "MK": 33, "CENT": 21, "AT1": 14, "AT2": 14, "APM": 9, "KLN": 5},
    "2026-11-07": {"TSTN": 13, "TSTG": 17, "MK": 12, "MPMK": 13, "MOKO": 10, "CPE": 8, "AT1": 18, "AT2": 10, "ATRA": 25, "SH": 5},
    "2026-11-08": {"TSTN": 14, "TSTG": 28, "MK": 25, "MPMK": 15, "CENT": 15, "CWB": 12, "CYW": 25, "AT1": 13, "ATRA": 18, "KLN": 7},
    "2026-11-09": {"TSTN": 22, "TSTG": 10, "TST": 10, "HSP": 6, "AT1": 7, "AT2": 5, "ATRA": 20, "APM": 9, "SH": 6},
    "2026-11-10": {"TSTN": 15, "TSTG": 15, "MPMK": 23, "CENT": 17, "CPE": 6, "AT1": 19, "ATRA": 17},
    "2026-11-11": {"TSTN": 20, "TSTG": 21, "TST": 10, "MK": 18, "MOKO": 14, "CWB": 8, "CYW": 31, "AT1": 22, "AT2": 9, "ATRA": 21, "APM": 8, "KLN": 7, "TM": 8, "SH": 6},
    "2026-11-12": {"TSTN": 45, "TSTG": 28, "AT1": 13, "ATRA": 16, "SH": 7},
    "2026-11-13": {"TSTN": 28, "TSTG": 15, "TST": 24, "MK": 57, "MPMK": 26, "MOKO": 19, "CENT": 40, "CPE": 11, "CWB": 15, "CYW": 27, "AT1": 12, "AT2": 10, "ATRA": 22, "APM": 8, "KLN": 12, "TM": 10, "SH": 6},
    "2026-11-14": {"TSTN": 20, "TSTG": 40, "TST": 12, "MK": 23, "MPMK": 12, "HSP": 10, "AT1": 21, "ATRA": 24, "SH": 10},
    "2026-11-15": {"TSTN": 37, "TSTG": 26, "TST": 11, "MK": 19, "MOKO": 14, "CENT": 20, "CWB": 14, "CYW": 29, "AT1": 32, "AT2": 9, "ATRA": 48, "APM": 5, "KLN": 4, "TM": 6},
}

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

def upsert_scan_day(date_str, boxes_dict, note=""):
    sh = get_sheet(SHEET_ID)
    headers = ["日期","Shop","到訪次數(由WhatsApp Label計)","箱數(待由相片填)","原本成本","實際成本","節省","備註"]
    ws = get_or_create_ws(sh, "通行", headers)
    vals = ws.get_all_values()
    if len(vals) > 1:
        keep = [vals[0]]
        for r in vals[1:]:
            if len(r) > 0 and r[0] != date_str:
                keep.append(r)
        ws.clear()
        ws.update('A1', [headers])
        if len(keep) > 1:
            ws.append_rows(keep[1:], value_input_option='USER_ENTERED')
    rows = []
    for shop, boxes in boxes_dict.items():
        mc, actual = cost_for(shop, boxes)
        rows.append([date_str, shop, 1 if boxes else "", boxes, mc, actual, "", note])
    if rows:
        ws.append_rows(rows, value_input_option='USER_ENTERED')
    return len(rows)

def get_cumulative_text():
    sh = get_sheet(SHEET_ID)
    ws = find_worksheet(sh, "通行")
    if not ws:
        return "通行 Tab 未建立，請先開 /rebuild-nov"
    vals = ws.get_all_values()
    if len(vals) < 2:
        return "通行 Tab 未有數據"
    by_date = {}
    for r in vals[1:]:
        if len(r) < 4 or not r[0].strip() or not str(r[3]).strip().isdigit():
            continue
        by_date.setdefault(r[0], 0)
        by_date[r[0]] += int(r[3])
    if not by_date:
        return "通行 Tab 未有箱數"
    lines = ["📋 SCAN 通行累計表"]
    total = 0
    for d in sorted(by_date.keys()):
        lines.append(f"{d}: {by_date[d]}箱")
        total += by_date[d]
    lines.append(f"———")
    lines.append(f"合計: {total}箱 · {len(by_date)}日 · 平均 {total/len(by_date):.1f}箱/日")
    lines.append("節省：待計")
    return "\n".join(lines)

def ocr_scan_image(image_b64):
    if not OPENAI_API_KEY:
        return None
    try:
        prompt = """你係物流單據識別員。這是一張 SCAN Global Logistics 手寫送貨單，左邊是舖頭代碼 (TSTN,TSTG,TST, MK,MPMK,MOKO,CENT,HSP,CPE,CWB,CYW,AT1,AT2,ATRA,APM,KLN,TM,SH)，右邊是箱數。
請只回傳 JSON，格式：{"date":"2026-11-16","boxes":{"TSTN":12}}，date 是單上日期，若看不清用今天。不要加其他文字。"""
        r = requests.post("https://api.openai.com/v1/chat/completions", json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}}]}],
            "max_tokens": 800
        }, headers={"Authorization": f"Bearer {OPENAI_API_KEY}"}, timeout=60)
        txt = r.json()["choices"][0]["message"]["content"]
        m = re.search(r"\{.*\}", txt, re.S)
        data = json.loads(m.group(0)) if m else json.loads(txt)
        return data
    except Exception as e:
        print("OCR fail:", e)
        return None

@app.get("/")
def home():
    return {"status": "running", "instance": INSTANCE}

@app.post("/webhook")
async def webhook(req: Request):
    data = await req.json()
    print("WEBHOOK RECEIVED:", str(data)[:3000])
    try:
        payload = data.get("data") or data
        msg = payload.get("message") or {}
        remote = (payload.get("key") or {}).get("remoteJid") or payload.get("remoteJid") or ""
        if TMS_GROUP_ID not in str(remote) and "g.us" not in str(remote):
            return {"ok": True, "ignored": "not group"}
        image_msg = msg.get("imageMessage") or msg.get("image") or None
        if not image_msg:
            return {"ok": True, "ignored": "not image"}
        b64 = image_msg.get("base64") or msg.get("base64") or ""
        if not b64 and image_msg.get("url"):
            b64 = base64.b64encode(requests.get(image_msg["url"], timeout=30).content).decode()
        if not b64:
            send_evolution_text(TMS_GROUP_ID, "📸 收到 SCAN 單相，但下載唔到圖片，請再 Send 一次")
            return {"ok": False, "error": "no image data"}
        result = ocr_scan_image(b64)
        if not result:
            send_evolution_text(TMS_GROUP_ID, "📸 收到 SCAN 單相，已存低。未設定 OPENAI_API_KEY 做自動識別，請人手對返數，之後我會再寫入通行。")
            return {"ok": True, "need_manual": True}
        date_str = result.get("date") or str(today_hk())
        boxes = result.get("boxes") or {}
        clean = {k: int(v) for k, v in boxes.items() if k in SHOPS and str(v).strip().isdigit()}
        if not clean:
            send_evolution_text(TMS_GROUP_ID, "📸 相已收到，但認唔到箱數，請再 Send 一張清啲嘅")
            return {"ok": False, "error": "no boxes"}
        upsert_scan_day(date_str, clean, note="由 WhatsApp Group 相片自動識別")
        total_today = sum(clean.values())
        cum = get_cumulative_text()
        # Group 只回一句確認，詳細累計表只 Send 俾自己
        send_evolution_text(TMS_GROUP_ID, f"✅ 已認到 {date_str} SCAN 單：今日 {total_today}箱，已寫入通行")
        if MY_WHATSAPP:
            send_evolution_text(MY_WHATSAPP, f"✅ 已認到 {date_str} SCAN 單：今日 {total_today}箱\n{clean}\n\n{cum}")
        return {"ok": True, "date": date_str, "boxes": clean}
    except Exception as e:
        print("webhook error:", e)
        return {"ok": False, "error": str(e)}

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
    try:
        sh = get_sheet(SHEET_ID)
        headers = ["日期","Shop","到訪次數(由WhatsApp Label計)","箱數(待由相片填)","原本成本","實際成本","節省","備註"]
        ws = get_or_create_ws(sh, "通行", headers)
        ws.clear()
        ws.update('A1', [headers])
        rows = []
        for d in range(1, 31):
            date_str = f"2026-11-{d:02d}"
            boxes_map = SCAN_BOXES.get(date_str)
            if boxes_map:
                for shop in SHOPS:
                    if shop in boxes_map:
                        boxes = boxes_map[shop]
                        mc, actual = cost_for(shop, boxes)
                        note = "15/11 KLN暫記4*，TSTN已確認37，以紙面274為準" if date_str == "2026-11-15" and shop == "KLN" else "由15張SCAN單對數入，節省待計"
                        rows.append([date_str, shop, 1, boxes, mc, actual, "", note])
            elif d > 15:
                rows.append([date_str, shop, "", "", "", "", "", "待補相片"])
        ws.append_rows(rows, value_input_option='USER_ENTERED')
        total = sum(sum(v.values()) for v in SCAN_BOXES.values())
        return {"ok": True, "rows": len(rows), "total_boxes_1_15": total, "msg": f"通行已寫入 1-15/11 真實箱數，合計 {total}箱，節省待計"}
    except Exception as e:
        print("rebuild-nov error:", e)
        return {"ok": False, "error": str(e)}

@app.get("/send-cumulative")
def send_cumulative():
    try:
        text = get_cumulative_text()
        # 累計表只 Send 俾自己，唔 Send 去 Group
        if not MY_WHATSAPP:
            return {"ok": False, "error": "MY_WHATSAPP 未設定，去 Railway Variables 加返"}
        ok = send_evolution_text(MY_WHATSAPP, text)
        return {"ok": ok, "text": text}
    except Exception as e:
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
        # 唔再喺 Group Send 累計表，累計表只俾自己。夜晚嗰份 Excel 已經係 Send 俾自己。
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
