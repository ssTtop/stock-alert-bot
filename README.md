# Stock Alert Bot → Discord

บอทแจ้งเตือนหุ้นเข้า Discord ทำงานฟรีบน GitHub Actions ไม่ต้องเปิดคอมทิ้งไว้

| โหมด | ทำอะไร | รันเมื่อไร (เวลาไทย) |
|---|---|---|
| `price` | เตือนเมื่อราคาแตะเป้าใน `watchlist.toml` | ทุก 15 นาที จ.–ศ. ราว 20:00–04:45 น. |
| `stage` | จัดสเตจ Weinstein (30WMA + Mansfield RS) แจ้งเมื่อสเตจเปลี่ยน + สรุปทุกตัว | เสาร์ 07:30 น. |
| `test` | ส่งข้อความทดสอบ | กดรันเอง |

## ไฟล์ในชุด

```
bot.py                         ตัวบอท
watchlist.toml                 ← แก้ไฟล์นี้ไฟล์เดียว (รายชื่อหุ้น + เป้าราคา)
requirements.txt               ไลบรารีที่ต้องใช้
state.json                     บอทจำว่าเตือนอะไรไปแล้ว (บอทแก้เอง ไม่ต้องยุ่ง)
.github/workflows/alerts.yml   ตารางเวลารันบน GitHub Actions
```

## ติดตั้ง (ราว 10 นาที)

### 1. สร้าง Discord Webhook
Server Settings → Integrations → Webhooks → **New Webhook** → เลือกห้อง → **Copy Webhook URL**

> URL นี้เหมือนรหัสผ่าน ใครได้ไปก็โพสต์เข้าห้องคุณได้ ห้ามใส่ลงในไฟล์โค้ด

### 2. สร้าง GitHub repo แล้วอัปโหลดไฟล์
1. github.com → **New repository** ตั้งชื่อ เช่น `stock-alert-bot`
   - **Public** = นาที Actions ฟรีไม่จำกัด (คนอื่นเห็น watchlist ได้ แต่ไม่เห็น webhook)
   - **Private** = ฟรี 2,000 นาที/เดือน บอทนี้ใช้ราว 800–1,000 นาที พอใช้แต่เฉียดๆ
2. แตกไฟล์ zip แล้วอัปโหลดทั้งหมด **รวมโฟลเดอร์ `.github`** (โฟลเดอร์นี้ถูกซ่อนบน Windows ถ้าลากไม่ติด ให้ใช้ `git push` หรือสร้างไฟล์ `.github/workflows/alerts.yml` บนเว็บ GitHub แล้ววางเนื้อหาเอง)

### 3. ใส่ Webhook เป็น Secret
repo → **Settings → Secrets and variables → Actions → New repository secret**
- Name: `DISCORD_WEBHOOK_URL`
- Secret: วาง URL จากข้อ 1

### 4. อนุญาตให้บอทบันทึก state
**Settings → Actions → General → Workflow permissions** → เลือก **Read and write permissions** → Save

### 5. ทดสอบ
แท็บ **Actions** → `stock-alerts` → **Run workflow** → mode `test` → Run
ถ้าห้อง Discord ขึ้น "✅ บอทเชื่อม Discord สำเร็จ" แปลว่าใช้งานได้แล้ว
จากนั้นลองรัน mode `stage` เพื่อดูสรุปสเตจครั้งแรก

## แก้เป้าราคา
เปิด `watchlist.toml` บนเว็บ GitHub → กดไอคอนดินสอ → แก้ → Commit

```toml
[[alerts]]
ticker = "NVDA"
above  = 200.00     # เตือนเมื่อราคา ≥ 200
below  = 160.00     # เตือนเมื่อราคา ≤ 160
note   = "เบรกกรอบ"
```

แต่ละเป้าเตือน**ครั้งเดียว** แล้วจะเตือนใหม่ได้เมื่อราคาถอยออกจากเป้าเกิน 1% (`rearm_pct`) กันการแจ้งรัวตอนราคาแกว่งอยู่แถวเป้า

## ทดสอบบนเครื่องตัวเอง (Windows PowerShell)
```powershell
pip install -r requirements.txt
python bot.py --mode stage --dry-run        # พิมพ์ออกจอ ไม่ส่ง Discord
$env:DISCORD_WEBHOOK_URL="วาง URL ที่นี่"
python bot.py --mode test                   # ส่งจริง
```
ต้องใช้ Python 3.11 ขึ้นไป

## ตรรกะจัดสเตจ (กราฟรายสัปดาห์)
| | MA 30 สัปดาห์ (ชันใน 4 สัปดาห์) | ราคา |
|---|---|---|
| Stage 2 | ชันขึ้น > 1% | เหนือ MA |
| Stage 4 | ชันลง > 1% | ใต้ MA |
| Stage 1 | แบน และก่อนหน้านั้นเป็นขาลง | — |
| Stage 3 | แบน และก่อนหน้านั้นเป็นขาขึ้น | — |
| 2 ⚠️ / 4 ⚠️ | MA ยังชันอยู่ แต่ราคาข้ามไปอีกฝั่ง | สัญญาณเปลี่ยนสเตจเร็ว |
| IPO | ข้อมูลไม่ถึง 34 สัปดาห์ ใช้ 10WMA ดูคร่าวๆ | — |

- **Mansfield RS** = (ราคาหุ้น/ดัชนี ÷ ค่าเฉลี่ย 52 สัปดาห์ของมัน − 1) × 100 ต้องมีข้อมูลอย่างน้อย 52 สัปดาห์
- **Vol** = volume สัปดาห์ล่าสุด เทียบค่าเฉลี่ย 10 สัปดาห์ก่อนหน้า
- รันกลางสัปดาห์ บอทจะตัดแท่งของสัปดาห์ที่ยังไม่ปิดทิ้ง เพื่อใช้เฉพาะราคาปิดรายสัปดาห์จริง
- ปรับความไวได้ที่ `[settings]` ใน `watchlist.toml` เช่น `flat_pct`

## ข้อจำกัดที่ควรรู้
- **GitHub cron ดีเลย์ได้ 5–30 นาที** ช่วงคนใช้เยอะ ไม่เหมาะกับการเทรดที่ต้องการความเร็วระดับวินาที (กรณีนั้นใช้ TradingView alert)
- **ข้อมูลจาก Yahoo Finance ผ่าน `yfinance`** ไม่ใช่ API ทางการ บางครั้งดีเลย์ราว 15 นาทีหรือพังชั่วคราว ถ้า Action ล้มให้ดู log ในแท็บ Actions แล้วลอง `pip install -U yfinance`
- GitHub จะปิด scheduled workflow อัตโนมัติถ้า repo ไม่มีความเคลื่อนไหว 60 วัน ถ้าได้อีเมลเตือนให้เข้าไปกดเปิดใหม่
- สเตจที่บอทให้เป็นการคำนวณเชิงกลไก ควรเปิดกราฟดูเองก่อนตัดสินใจเสมอ ไม่ใช่คำแนะนำการลงทุน
