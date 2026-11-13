#!/usr/bin/env python3
"""
Stock Alert Bot -> Discord
  - price : แจ้งเตือนเมื่อราคาแตะเป้าที่ตั้งไว้ใน watchlist.toml
  - stage : จัดสเตจแบบ Stan Weinstein (30WMA + Mansfield RS) แล้วแจ้งเมื่อสเตจเปลี่ยน
  - test  : ส่งข้อความทดสอบเข้า Discord

ใช้งาน:
  python bot.py --mode price
  python bot.py --mode stage
  python bot.py --mode test
  python bot.py --mode stage --dry-run   # พิมพ์ออกจอแทนการส่ง Discord และไม่บันทึก state
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import tomllib
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "watchlist.toml"
STATE_PATH = ROOT / "state.json"
NY = ZoneInfo("America/New_York")

DEFAULT_SETTINGS = {
    "ma_weeks": 30,          # เส้นค่าเฉลี่ยหลักของ Weinstein
    "slope_lookback": 4,     # วัดความชันของ MA เทียบกับกี่สัปดาห์ก่อน
    "flat_pct": 1.0,         # ความชัน (%) ที่ยังถือว่า "แบน"
    "rs_weeks": 52,          # ฐานของ Mansfield RS
    "rearm_pct": 1.0,        # ราคาต้องถอยห่างจากเป้ากี่ % ก่อนเตือนซ้ำได้
    "weekly_summary": True,  # ส่งสรุปสเตจทุกตัวทุกสัปดาห์
}

STAGE_TEXT = {
    "1": "Stage 1 ฐานสะสม",
    "2": "Stage 2 ขาขึ้น",
    "3": "Stage 3 จ่ายของ",
    "4": "Stage 4 ขาลง",
    "2?": "Stage 2 ⚠️ ราคาหลุดใต้ MA",
    "4?": "Stage 4 ⚠️ ราคายืนเหนือ MA",
    "IPO": "IPO base (ข้อมูลยังไม่พอ)",
}
STAGE_ORDER = ["2", "1", "4?", "2?", "3", "4", "IPO"]


# ---------------------------------------------------------------- config / state
def load_config() -> dict:
    with open(CONFIG_PATH, "rb") as f:
        cfg = tomllib.load(f)
    cfg["settings"] = {**DEFAULT_SETTINGS, **cfg.get("settings", {})}
    return cfg


def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text("utf-8"))
        except json.JSONDecodeError:
            pass
    return {"price": {}, "stage": {}}


def save_state(state: dict) -> None:
    STATE_PATH.write_text(
        json.dumps(state, indent=2, ensure_ascii=False, sort_keys=True) + "\n", "utf-8"
    )


# ---------------------------------------------------------------- discord
def split_message(text: str, limit: int = 1900) -> list[str]:
    """Discord รับข้อความได้ไม่เกิน 2000 ตัวอักษร จึงตัดแบ่งตามบรรทัด"""
    chunks, cur = [], ""
    for line in text.splitlines():
        if len(cur) + len(line) + 1 > limit and cur:
            chunks.append(cur)
            cur = ""
        cur += line + "\n"
    if cur.strip():
        chunks.append(cur)
    return chunks


def send_discord(text: str, dry_run: bool = False) -> None:
    import requests

    url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    chunks = split_message(text)
    if dry_run or not url:
        if not url and not dry_run:
            print("⚠️ ไม่ได้ตั้ง DISCORD_WEBHOOK_URL จึงพิมพ์ข้อความออกจอแทน", file=sys.stderr)
        for c in chunks:
            print(c)
            print("-" * 40)
        return
    for c in chunks:
        for _ in range(3):
            r = requests.post(url, json={"content": c}, timeout=15)
            if r.status_code == 429:  # rate limited
                time.sleep(float(r.json().get("retry_after", 2)))
                continue
            r.raise_for_status()
            break
        time.sleep(0.5)


# ---------------------------------------------------------------- data
def get_last_price(symbol: str) -> float | None:
    import yfinance as yf

    t = yf.Ticker(symbol)
    try:
        p = t.fast_info.last_price
        if p is not None and p == p:  # กัน NaN
            return float(p)
    except Exception:
        pass
    h = t.history(period="5d", interval="1d")
    if h.empty:
        return None
    return float(h["Close"].dropna().iloc[-1])


def get_weekly(symbol: str):
    """แท่งรายสัปดาห์ย้อนหลัง 3 ปี ตัดแท่งของสัปดาห์ที่ยังไม่ปิดทิ้ง"""
    import yfinance as yf

    h = yf.Ticker(symbol).history(period="3y", interval="1wk", auto_adjust=True)
    h = h.dropna(subset=["Close"])
    if h.empty:
        return h
    today = datetime.now(NY).date()
    if today.weekday() < 5:  # จันทร์-ศุกร์ = แท่งสัปดาห์นี้ยังไม่จบ
        monday = today - timedelta(days=today.weekday())
        if h.index[-1].date() >= monday:
            h = h.iloc[:-1]
    h.index = h.index.tz_localize(None) if h.index.tz is not None else h.index
    h.index = h.index.normalize()
    return h


# ---------------------------------------------------------------- stage logic
def last_trend(ma, lb: int, flat: float) -> float | None:
    """ย้อนหาความชันล่าสุดของ MA ที่ไม่แบน (บอกว่าก่อนเข้าฐาน หุ้นมาจากขาขึ้นหรือขาลง)"""
    vals = ma.dropna()
    for i in range(len(vals) - 1, lb - 1, -1):
        sl = (float(vals.iloc[i]) / float(vals.iloc[i - lb]) - 1) * 100
        if abs(sl) > flat:
            return sl
    return None


def classify(close, volume=None, bench=None, s: dict | None = None, prev: str | None = None) -> dict:
    """
    close / volume / bench : pandas Series รายสัปดาห์ (index = วันที่)
    คืนค่า dict: stage, close, ma, ma_name, slope, mrs, mrs_chg, vol_ratio, weeks
    """
    s = {**DEFAULT_SETTINGS, **(s or {})}
    ma_n, lb, flat = s["ma_weeks"], s["slope_lookback"], s["flat_pct"]
    n = len(close)
    last = float(close.iloc[-1])
    out = {"close": last, "weeks": n, "ma": None, "ma_name": f"{ma_n}WMA",
           "slope": None, "mrs": None, "mrs_chg": None, "vol_ratio": None}

    if volume is not None and len(volume) >= 11:
        avg = float(volume.iloc[-11:-1].mean())
        if avg > 0:
            out["vol_ratio"] = float(volume.iloc[-1]) / avg

    if bench is not None:
        b = bench.reindex(close.index).ffill()
        rs = close / b
        rs_n = s["rs_weeks"]
        if rs.notna().sum() >= rs_n:
            mrs = (rs / rs.rolling(rs_n).mean() - 1) * 100
            if mrs.notna().iloc[-1]:
                out["mrs"] = float(mrs.iloc[-1])
                if len(mrs) > 4 and mrs.notna().iloc[-5]:
                    out["mrs_chg"] = float(mrs.iloc[-1] - mrs.iloc[-5])

    if n < ma_n + lb:
        out["stage"] = "IPO"
        if n >= 10:
            out["ma"] = float(close.rolling(10).mean().iloc[-1])
            out["ma_name"] = "10WMA"
        return out

    ma = close.rolling(ma_n).mean()
    ma_last = float(ma.iloc[-1])
    slope = (ma_last / float(ma.iloc[-1 - lb]) - 1) * 100
    out["ma"], out["slope"] = ma_last, slope

    above = last > ma_last
    if slope > flat:
        stage = "2" if above else "2?"
    elif slope < -flat:
        stage = "4?" if above else "4"
    else:  # MA แบน → แยก 1 กับ 3 จากเทรนด์ล่าสุดก่อนที่ MA จะแบน
        trend = last_trend(ma, lb, flat)
        if trend is not None:
            stage = "1" if trend < 0 else "3"
        elif prev in ("1", "3"):
            stage = prev
        else:
            stage = "1"
    out["stage"] = stage
    return out


def fmt_line(sym: str, r: dict) -> str:
    parts = [f"**{sym}** ${r['close']:,.2f}", STAGE_TEXT[r["stage"]]]
    if r["ma"] is not None:
        pos = (r["close"] / r["ma"] - 1) * 100
        parts.append(f"{r['ma_name']} ${r['ma']:,.2f} ({pos:+.1f}%)")
    if r["slope"] is not None:
        parts.append(f"ชัน {r['slope']:+.1f}%")
    if r["mrs"] is not None:
        arrow = ""
        if r["mrs_chg"] is not None:
            arrow = " ↗" if r["mrs_chg"] > 0 else " ↘"
        parts.append(f"RS {r['mrs']:+.1f}{arrow}")
    if r["vol_ratio"] is not None:
        parts.append(f"Vol {r['vol_ratio']:.1f}x")
    if r["stage"] == "IPO":
        parts.append(f"{r['weeks']} สัปดาห์")
    return " · ".join(parts)


def change_message(sym: str, prev: str, r: dict) -> str:
    new = r["stage"]
    if new == "2" and prev in ("1", "4?", "IPO"):
        head = f"🚀 **{sym} เบรกเข้า Stage 2** (จาก {STAGE_TEXT[prev]})"
        checks = []
        if r["vol_ratio"] is not None:
            checks.append("✅ volume ≥ 2x" if r["vol_ratio"] >= 2 else "⚠️ volume ยังไม่ถึง 2x")
        if r["mrs"] is not None:
            checks.append("✅ RS เป็นบวก" if r["mrs"] > 0 else "⚠️ RS ยังติดลบ")
        tail = ("\n" + " · ".join(checks)) if checks else ""
        return f"{head}\n{fmt_line(sym, r)}{tail}"
    if new == "4" and prev in ("3", "2", "2?"):
        return f"🔻 **{sym} หลุดเข้า Stage 4** (จาก {STAGE_TEXT[prev]})\n{fmt_line(sym, r)}"
    if new == "1" and prev in ("4", "4?"):
        return f"🟡 **{sym} เริ่มเข้าฐาน Stage 1** (จาก {STAGE_TEXT[prev]})\n{fmt_line(sym, r)}"
    if new == "3" and prev in ("2", "2?"):
        return f"🟠 **{sym} เริ่มเข้า Stage 3** (จาก {STAGE_TEXT[prev]})\n{fmt_line(sym, r)}"
    return f"🔄 **{sym}** {STAGE_TEXT[prev]} → {STAGE_TEXT[new]}\n{fmt_line(sym, r)}"


# ---------------------------------------------------------------- modes
def run_price(cfg: dict, state: dict, dry_run: bool) -> None:
    rearm = cfg["settings"]["rearm_pct"] / 100
    pstate = state.setdefault("price", {})
    msgs, valid_keys = [], set()

    for a in cfg.get("alerts", []):
        sym = a["ticker"]
        try:
            price = get_last_price(sym)
        except Exception as e:
            print(f"⚠️ {sym}: ดึงราคาไม่ได้ ({e})", file=sys.stderr)
            continue
        if price is None:
            print(f"⚠️ {sym}: ไม่มีข้อมูลราคา", file=sys.stderr)
            continue
        print(f"{sym}: ${price:,.2f}")
        note = f" — {a['note']}" if a.get("note") else ""
        for side in ("above", "below"):
            level = a.get(side)
            if level is None:
                continue
            key = f"{sym}:{side}:{level}"
            valid_keys.add(key)
            if side == "above":
                hit, reset = price >= level, price < level * (1 - rearm)
                icon, word = "🟢", "ขึ้นเหนือ"
            else:
                hit, reset = price <= level, price > level * (1 + rearm)
                icon, word = "🔴", "ลงต่ำกว่า"
            if hit and not pstate.get(key):
                pstate[key] = True
                msgs.append(f"{icon} **{sym}** ราคา ${price:,.2f} {word} ${level:,.2f}{note}")
            elif pstate.get(key) and reset:
                pstate[key] = False  # พร้อมเตือนครั้งใหม่

    for k in list(pstate):
        if k not in valid_keys:
            del pstate[k]

    if msgs:
        send_discord("\n".join(msgs), dry_run)
    else:
        print("ยังไม่มีหุ้นตัวไหนแตะเป้า")


def run_stage(cfg: dict, state: dict, dry_run: bool) -> None:
    s = cfg["settings"]
    bench_sym = cfg.get("benchmark", "^GSPC")
    bench = None
    try:
        bh = get_weekly(bench_sym)
        if not bh.empty:
            bench = bh["Close"]
    except Exception as e:
        print(f"⚠️ ดึงดัชนี {bench_sym} ไม่ได้ ({e}) จะข้าม RS", file=sys.stderr)

    sstate = state.setdefault("stage", {})
    results, changes = {}, []
    for sym in cfg.get("watchlist", []):
        try:
            h = get_weekly(sym)
        except Exception as e:
            print(f"⚠️ {sym}: ดึงข้อมูลไม่ได้ ({e})", file=sys.stderr)
            continue
        if h.empty:
            print(f"⚠️ {sym}: ไม่มีข้อมูล", file=sys.stderr)
            continue
        prev = sstate.get(sym)
        r = classify(h["Close"], h.get("Volume"), bench, s, prev)
        results[sym] = r
        print(fmt_line(sym, r))
        if prev is not None and prev != r["stage"]:
            changes.append(change_message(sym, prev, r))
        sstate[sym] = r["stage"]

    for k in list(sstate):
        if k not in cfg.get("watchlist", []):
            del sstate[k]

    parts = []
    if changes:
        parts.append("## 📣 สเตจเปลี่ยนสัปดาห์นี้\n" + "\n\n".join(changes))
    if s["weekly_summary"] and results:
        lines = [fmt_line(sym, r) for sym, r in
                 sorted(results.items(), key=lambda x: (STAGE_ORDER.index(x[1]["stage"]), x[0]))]
        week = datetime.now(NY).strftime("%d %b %Y")
        parts.append(f"## 📊 สรุปสเตจรายสัปดาห์ ({week}) · RS เทียบ {bench_sym}\n" + "\n".join(lines))
    if parts:
        send_discord("\n\n".join(parts), dry_run)


def run_test(cfg: dict, dry_run: bool) -> None:
    n_alert = len(cfg.get("alerts", []))
    n_watch = len(cfg.get("watchlist", []))
    send_discord(
        f"✅ บอทเชื่อม Discord สำเร็จ · ติดตาม {n_watch} ตัว · ตั้งเป้าราคา {n_alert} ตัว",
        dry_run,
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["price", "stage", "test"], default="price")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cfg = load_config()
    state = load_state()
    if args.mode == "price":
        run_price(cfg, state, args.dry_run)
    elif args.mode == "stage":
        run_stage(cfg, state, args.dry_run)
    else:
        run_test(cfg, args.dry_run)
        return
    if not args.dry_run:
        save_state(state)


if __name__ == "__main__":
    main()
