import os, io, re, base64, requests, json, time
from fastapi import FastAPI, Request
from datetime import datetime, timedelta, date, timezone
from urllib.parse import quote
import gspread
from google.oauth2.service_account import Credentials
import pandas as pd
from apscheduler.schedulers.background import BackgroundScheduler

app = FastAPI()
HKT = timezone(timedelta(hours=8))
def now_hk(): return datetime.now(HKT)
def today_hk(): return now_hk().date()

EVOLUTION_URL = "https://evolution-api-production-5d91de27.up.railway.app"
INSTANCE = "my WhatsApps"
EVOLUTION_API_KEY = os.environ.get("EVOLUTION_API_KEY")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
SHEET_ID = "15X06a1oOFeta-ETz-CiLbB-5TUWS1rWKGi35HmBNNVE"
TMS_SHEET_ID = os.environ.get("TMS_SHEET_ID", SHEET_ID)
MY_WHATSAPP = os.environ.get("MY_WHATSAPP")
TMS_GROUP_ID = os.environ.get("TMS_GROUP_ID", "120363413020795341@g.us")
COOKIE_GROUP_ID = os.environ.get("COOKIE_GROUP_ID", "")

SHOPS = ["TSTN","TSTG","TST","MK","MPMK","MOKO","CENT","HSP","CPE","CWB","CYW","AT1","AT2","ATRA","APM","KLN","TM","SH"]
MIN_CHARGE = [200,200,200,200,200,200,380,380,380,380,380,500,500,500,200,200,250,250]
PER_BOX = 5

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

def get_sheet(sheet_id=SHEET_ID):
    creds = Credentials.from_service_account_info(json.loads(os.environ.get("GCP_JSON")), scopes=["https://www.googleapis.com/auth/spreadsheets","https://www.googleapis.com/auth/drive"])
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

def build_tongxing_matrix():
    rows = []
    rows.append([""] + MIN_CHARGE + [""])
    rows.append([""] + SHOPS + [""])
    for d in range(1, 31):
        ds = f"2026-11-{d:02d}"
        label = f"{d}-Nov"
        boxes_map = SCAN_BOXES.get(ds, {})
        row = [label]
        day_total = 0
        for shop in SHOPS:
            if shop in boxes_map:
                _, actual = cost_for(shop, boxes_map[shop])
                row.append(actual)
                day_total += actual
            else:
                row.append("")
        row.append(day_total if boxes_map else "")
        rows.append(row)
    return rows

def build_zongbiao_matrix():
    rows = []
    rows.append([""] + SHOPS + ["總箱數"])
    for d in range(1, 31):
        ds = f"2026-11-{d:02d}"
        label = f"{d}-Nov"
        boxes_map = SCAN_BOXES.get(ds, {})
        row = [label]
        day_total = 0
        for shop in SHOPS:
            if shop in boxes_map:
                row.append(boxes_map[shop])
                day_total += boxes_map[shop]
            else:
                row.append("")
        row.append(day_total if boxes_map else "")
        rows.append(row)
    return rows

def write_both_sheets():
    sh = get_sheet(SHEET_ID)
    ws1 = get_or_create_ws(sh, "通行", [""]+SHOPS+[""])
    ws1.clear()
    time.sleep(1)
    ws1.update('A1', build_tongxing_matrix(), value_input_option='USER_ENTERED')
    time.sleep(1)
    ws2 = get_or_create_ws(sh, "運輸總表", [""]+SHOPS+["總箱數"])
    ws2.clear()
    time.sleep(1)
    ws2.update('A1', build_zongbiao_matrix(), value_input_option='USER_ENTERED')
    time.sleep(1)
    r1 = len(ws1.get_all_values())
    r2 = len(ws2.get_all_values())
    print(f"WRITE DONE 通行 rows: {r1}, 運輸總表 rows: {r2}")
    return r1, r2

def load_scan_boxes_from_sheet():
    try:
        sh = get_sheet(SHEET_ID)
        ws = find_worksheet(sh, "運輸總表")
        if not ws:
            print("No 運輸總表 sheet, skip load")
            return
        vals = ws.get_all_values()
        if len(vals) < 2:
            print("運輸總表 only header, skip load")
            return
        for row in vals[1:]:
            if not row or not row[0].strip():
                continue
            try:
                day = row[0].split("-")[0].strip()
                ds = f"2026-11-{int(day):02d}"
            except:
                continue
            boxes = {}
            for i, shop in enumerate(SHOPS):
                v = row[i+1].strip() if i+1 < len(row) else ""
                if v and v.isdigit():
                    boxes[shop] = int(v)
            if boxes:
                SCAN_BOXES[ds] = boxes
        print(f"Loaded SCAN_BOXES from Sheet: {sorted(SCAN_BOXES.keys())}")
    except Exception as e:
        print(f"load from sheet fail: {e}")

def style_sheet(ws, matrix, is_cost_sheet):
    from openpyxl.styles import PatternFill, Font, Alignment, Border, Side
    fills = {
        "TSTN": "FFFF00", "TSTG": "FFFF00", "TST": "FFFF00",
        "MK": "92D050", "MPMK": "92D050", "MOKO": "92D050", "CENT": "92D050", "HSP": "92D050", "CPE": "92D050", "CWB": "92D050", "CYW": "92D050",
        "AT1": "FF0000", "AT2": "FF0000", "ATRA": "FF0000",
        "APM": "FFFF00", "KLN": "FFFF00",
        "TM": "00B0F0", "SH": "00B0F0",
    }
    thin = Side(style="thin", color="B0B0B0")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    header_row = 2 if is_cost_sheet else 1
    for r_idx, row in enumerate(matrix, start=1):
        for c_idx, val in enumerate(row, start=1):
            cell = ws.cell(row=r_idx, column=c_idx, value=val)
            cell.border = border
            cell.alignment = Alignment(horizontal="center", vertical="center")
            if r_idx == 1 and is_cost_sheet:
                cell.font = Font(bold=True)
            if r_idx == header_row and 2 <= c_idx <= 19:
                shop = SHOPS[c_idx-2]
                cell.fill = PatternFill("solid", fgColor=fills.get(shop, "FFFFFF"))
                cell.font = Font(bold=True)
            if r_idx == header_row and c_idx == 20:
                cell.font = Font(bold=True)
            if r_idx > header_row and c_idx == 1:
                cell.font = Font(bold=True)
            if r_idx > header_row and c_idx == 20 and val != "":
                cell.font = Font(bold=True)
    ws.column_dimensions['A'].width = 12
    for i in range(2, 20):
        ws.column_dimensions[chr(64+i)].width = 10
    ws.column_dimensions['T'].width = 12
    ws.freeze_panes = "B3" if is_cost_sheet else "B2"

def build_tongxing_excel(path):
    from openpyxl import Workbook
    wb = Workbook()
    ws1 = wb.active
    ws1.title = "通行"
    style_sheet(ws1, build_tongxing_matrix(), True)
    ws2 = wb.create_sheet("運輸總表")
    style_sheet(ws2, build_zongbiao_matrix(), False)
    wb.save(path)
    return path

def send_evolution_media(to, file_path, caption):
    with open(file_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    url = f"{EVOLUTION_URL}/message/sendMedia/{quote(INSTANCE)}"
    r = requests.post(url, json={"number": to, "mediatype": "document", "mimetype": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "caption": caption, "media": b64, "fileName": os.path.basename(file_path)}, headers={"apikey": EVOLUTION_API_KEY}, timeout=60)
    print(f"Evolution send to {to}: {r.status_code} {r.text[:500]}")
    return r.status_code in [200, 201]

def send_evolution_text(to, text):
    url = f"{EVOLUTION_URL}/message/sendText/{quote(INSTANCE)}"
    r = requests.post(url, json={"number": to, "text": text}, headers={"apikey": EVOLUTION_API_KEY}, timeout=20)
    print(f"Evolution text: {r.status_code} {r.text[:300]}")
    return r.status_code in [200, 201]

def get_cumulative_excel_path():
    path = f"/tmp/每月物流成本節省記錄-{today_hk().strftime('%b%Y')}.xlsx"
    return build_tongxing_excel(path)

def ocr_scan_image(image_b64):
    if not OPENAI_API_KEY:
        return None
    try:
        prompt = """你係物流單據識別員。這是一張 SCAN Global Logistics 手寫送貨單，左邊是舖頭代碼 (TSTN,TSTG,TST, MK,MPMK,MOKO,CENT,HSP,CPE,CWB,CYW,AT1,AT2,ATRA,APM,KLN,TM,SH)，右邊是箱數。請只回傳 JSON，格式：{"date":"2026-11-16","boxes":{"TSTN":12}}，date 是單上日期，若看不清用今天。不要加其他文字。"""
        r = requests.post("https://api.openai.com/v1/chat/completions", json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}}]}], "max_tokens": 800}, headers={"Authorization": f"Bearer {OPENAI_API_KEY}"}, timeout=60)
        txt = r.json()["choices"][0]["message"]["content"]
        m = re.search(r"\{.*\}", txt, re.S)
        return json.loads(m.group(0)) if m else json.loads(txt)
    except Exception as e:
        print("OCR fail:", e)
        return None

@app.get("/")
def home():
    return {"status": "running", "instance": INSTANCE, "scan_dates": sorted(SCAN_BOXES.keys())}

@app.post("/webhook")
async def webhook(req: Request):
    data = await req.json()
    print("WEBHOOK RECEIVED:", str(data)[:3000])
    try:
        payload = data.get("data") or data
        msg = payload.get("message") or {}
        remote = (payload.get("key") or {}).get("remoteJid") or payload.get("remoteJid") or ""
        print(f"WEBHOOK remoteJid: {remote}")
        # 認 TMS Group + 曲奇 Group + 任何 g.us 群組，唔好再漏咗曲奇群組 21:07 嗰張相
        allowed = [TMS_GROUP_ID, COOKIE_GROUP_ID]
        if remote not in allowed and "g.us" not in str(remote):
            print(f"IGNORED group: {remote}, allowed: {allowed}")
            return {"ok": True, "ignored": "not group", "remote": remote}
        print(f"ACCEPTED group: {remote}")
        image_msg = msg.get("imageMessage") or msg.get("image") or None
        if not image_msg:
            return {"ok": True, "ignored": "not image", "remote": remote}
        b64 = image_msg.get("base64") or msg.get("base64") or ""
        if not b64 and image_msg.get("url"):
            b64 = base64.b64encode(requests.get(image_msg["url"], timeout=30).content).decode()
        if not b64:
            send_evolution_text(remote, "📸 收到 SCAN 單相，但下載唔到圖片，請再 Send 一次")
            return {"ok": False, "error": "no image data"}
        result = ocr_scan_image(b64)
        if not result:
            send_evolution_text(remote, "📸 收到 SCAN 單相，已存低。未設定 OPENAI_API_KEY 做自動識別，請人手對返數。")
            return {"ok": True, "need_manual": True}
        date_str = result.get("date") or str(today_hk())
        boxes = result.get("boxes") or {}
        clean = {k: int(v) for k, v in boxes.items() if k in SHOPS and str(v).strip().isdigit()}
        if not clean:
            send_evolution_text(remote, "📸 相已收到，但認唔到箱數，請再 Send 一張清啲嘅")
            return {"ok": False, "error": "no boxes"}
        SCAN_BOXES[date_str] = clean
        r1, r2 = write_both_sheets()
        total_today = sum(clean.values())
        send_evolution_text(remote, f"✅ 已認到 {date_str} SCAN 單：今日 {total_today}箱，已寫入通行 + 運輸總表 (通行{r1}行，總表{r2}行)")
        if MY_WHATSAPP:
            path = get_cumulative_excel_path()
            send_evolution_media(MY_WHATSAPP, path, f"📋 SCAN 累計表 Excel - {date_str} 已更新，今日 {total_today}箱 (通行+運輸總表)")
        return {"ok": True, "date": date_str, "boxes": clean, "tongxing_rows": r1, "zongbiao_rows": r2}
    except Exception as e:
        print("webhook error:", e)
        return {"ok": False, "error": str(e)}

@app.get("/debug-sheets")
def debug_sheets():
    try:
        sh = get_sheet(SHEET_ID)
        titles = [ws.title for ws in sh.worksheets()]
        return {"sheet": sh.title, "worksheets": titles, "scan_dates": sorted(SCAN_BOXES.keys())}
    except Exception as e:
        return {"ok": False, "error": str(e)}

@app.get("/rebuild-nov")
def rebuild_nov():
    try:
        # 先由 Sheet 讀返已有嘅數，唔好覆蓋咗 16號之後手動入咗嘅數
        load_scan_boxes_from_sheet()
        r1, r2 = write_both_sheets()
        total = sum(sum(v.values()) for v in SCAN_BOXES.values())
        return {"ok": True, "total_boxes": total, "tongxing_rows": r1, "zongbiao_rows": r2, "scan_dates": sorted(SCAN_BOXES.keys()), "msg": f"通行 {r1}行，運輸總表 {r2}行，合計 {total}箱"}
    except Exception as e:
        print("rebuild-nov error:", e)
        return {"ok": False, "error": str(e)}

@app.get("/send-cumulative")
def send_cumulative():
    try:
        if not MY_WHATSAPP:
            return {"ok": False, "error": "MY_WHATSAPP 未設定，去 Railway Variables 加返"}
        load_scan_boxes_from_sheet()
        path = get_cumulative_excel_path()
        ok = send_evolution_media(MY_WHATSAPP, path, f"📋 每月物流成本節省記錄 {today_hk().strftime('%b%Y')} - 通行 + 運輸總表")
        return {"ok": ok, "file": os.path.basename(path), "scan_dates": sorted(SCAN_BOXES.keys())}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def nightly_send():
    try:
        if not MY_WHATSAPP:
            return {"ok": False, "error": "MY_WHATSAPP 未設定"}
        load_scan_boxes_from_sheet()
        path = get_cumulative_excel_path()
        ok = send_evolution_media(MY_WHATSAPP, path, f"今日累計 - 每月物流成本節省記錄 {today_hk().strftime('%b%Y')} - 通行 + 運輸總表")
        return {"ok": ok}
    except Exception as e:
        print("nightly error:", e)
        return {"ok": False, "error": str(e)}

def tms_daily_send():
    try:
        if not TMS_SHEET_ID:
            return
        sh = get_sheet(TMS_SHEET_ID)
        ws = find_worksheet(sh, "Jobs")
        if not ws:
            return
        vals = ws.get_all_values()
        if len(vals) < 2:
            send_evolution_text(TMS_GROUP_ID, f"📦 TMS每日提醒 {today_hk()} 未來兩日無Order")
            return
        headers = [h.strip() for h in vals[0]]
        df = pd.DataFrame(vals[1:], columns=headers)
        date_col = next((c for c in ["Date","送貨日期","DeliveryDate"] if c in df.columns), None)
        if not date_col:
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
    return {"ok": True, "msg": "TMS test sent"}

@app.get("/test-nightly")
def test_nightly():
    return nightly_send()

# 開機先讀返 Sheet，唔好淨係靠硬編碼 1-15號
try:
    load_scan_boxes_from_sheet()
except Exception as e:
    print(f"startup load fail: {e}")

scheduler = BackgroundScheduler(timezone="Asia/Hong_Kong")
scheduler.add_job(nightly_send, "cron", hour=22, minute=0)
scheduler.add_job(tms_daily_send, "cron", hour=17, minute=0)
scheduler.start()
