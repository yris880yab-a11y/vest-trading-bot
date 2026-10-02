# Hướng dẫn cài bot scalp NQ + GC trên Topstep (Windows)

Hai bot chạy song song trên **máy tính cá nhân của bạn**, dùng chung 1 tài khoản Topstep:

| Bot | Mã | Cách vào lệnh | Giờ mở lệnh | File cấu hình | Log |
|---|---|---|---|---|---|
| NQ | MNQ ($2/điểm) | Nến 1M rút râu tại vùng nhiều volume, hoặc nến 5M đóng bung mạnh (`rejection,close`) | 07–20 UTC = 14:00–03:00 giờ VN | `.env.momo` | `bot_momo.log` |
| GC | MGC ($10/điểm) | Nến 1M rút râu tại vùng nhiều volume, nến râu có volume ≥ trung bình (`rejection`) | 24 giờ | `.env.momo.gc` | `bot_momo_gc.log` |

Mỗi lệnh: vào market **kèm OCO** (SL + TP trên sàn), chốt 1/2 ở +10 điểm NQ / +1.5 GC rồi dời SL về hoà vốn, phần còn lại trail; thoát khi nến 1M ngược mạnh, giữ quá 15 phút, tới giờ ra tin 8:30 New York hoặc 3:08 PM Chicago. Không giới hạn số lệnh, không dừng theo R; chỉ dừng khi gần mức thua tối đa (MLL) của Topstep hoặc đạt mục tiêu.

> **Luật Topstep:** chỉ chạy trên máy của bạn (cấm VPS, VPN, server), chỉ dùng cho **Trading Combine / Express Funded** (không dùng API cho Live Funded), không HFT.

---

## Bước 1 — Chuẩn bị tài khoản

1. Tài khoản **Topstep Trading Combine 100K** (hoặc 50K, Express Funded).
2. **TopstepX → Settings → API**: đăng ký gói API của ProjectX (tính phí hằng tháng) → tạo **API key** → copy lại.
3. Ghi lại **tên đăng nhập TopstepX** (username).
4. **Bật Auto-OCO brackets** trong cài đặt TopstepX (mục Brackets / Auto OCO). Bot gửi SL/TP kèm lệnh vào theo kiểu này.
5. (Tuỳ chọn) Telegram nhận tin nhắn khi bot vào/chốt lệnh:
   - Nhắn `/newbot` cho **@BotFather** → đặt tên → nhận **token**.
   - Nhắn 1 tin cho bot vừa tạo, mở `https://api.telegram.org/bot<TOKEN>/getUpdates` → số trong `"chat":{"id": ...}` là **chat id**.

## Bước 2 — Cài Python

1. Tải Python 3.11 hoặc 3.12 tại https://www.python.org/downloads/windows/
2. Khi cài, **tick ô "Add python.exe to PATH"** → Install.

## Bước 3 — Tải bot về máy

1. Mở https://github.com/yris880yab-a11y/vest-trading-bot/tree/claude/vestmarkets-trading-bot-ki86ah
2. Bấm nút xanh **Code** → **Download ZIP**.
3. Giải nén vào `C:\bot` (các file `.bat` nằm ngay trong `C:\bot`).

## Bước 4 — Cài đặt (làm 1 lần)

1. Nhấp đúp **`setup_nq_gc.bat`** → gõ **`100`** (tài khoản 100K) hoặc `50` → Enter. Script cài thư viện, tạo 2 file `.env.momo` (NQ) và `.env.momo.gc` (GC) đúng cỡ tài khoản, rồi mở bằng Notepad.
2. Trong **cả hai file**, sửa:

| Dòng | Điền |
|---|---|
| `TOPSTEP_USERNAME=` | tên đăng nhập TopstepX |
| `TOPSTEP_API_KEY=` | API key |
| `TOPSTEP_ACCOUNT=` | để trống nếu chỉ có 1 tài khoản; nhiều tài khoản thì điền tên tài khoản (xem Bước 5) |
| `TELEGRAM_BOT_TOKEN=` / `TELEGRAM_CHAT_ID=` | tuỳ chọn |

3. **Ctrl+S** lưu cả hai file. Giữ `BOT_DRY_RUN=true`.

Nội dung đầy đủ 2 file (bản 100K) ở cuối hướng dẫn — có thể dán thẳng vào Notepad.

> Không gửi file `.env.momo*` hay API key cho ai. Chụp màn hình lỗi thì che API key.

## Bước 5 — Kiểm tra kết nối

Nhấp đúp **`check_nq_gc.bat`**. Đúng thì thấy (cho cả NQ và GC):

```
1) Đăng nhập + tài khoản:
   - 100KTC-V2-xxxxx-xxxxxxx  (id 123456, số dư 100000)
2) Tài khoản bot sẽ dùng: id 123456
3) MNQ: CON.F.US.MNQ.Z26 | nến 1m mới nhất: {...}
OK — kết nối TopstepX hoạt động.
```

| Lỗi | Cách sửa |
|---|---|
| `401` / `Thiếu TOPSTEP_USERNAME` | sai username / API key, hoặc chưa lưu file |
| `Đặt TOPSTEP_ACCOUNT = ...` | nhiều tài khoản: copy đúng tên tài khoản vào `TOPSTEP_ACCOUNT=` ở **cả hai file** |
| `Không tìm thấy hợp đồng` | ngoài giờ giao dịch; thử lại khi thị trường mở |
| `'py' is not recognized` | cài lại Python, tick "Add to PATH" |

Không kết nối được `api.topstepx.com` thì đổi `TOPSTEP_API_URL=https://api.topstepx.projectx.com` ở cả hai file.

## Bước 6 — Chạy thử (dry-run) 1–2 tuần

1. Nhấp đúp **`start_nq_gc.bat`**: mở 2 cửa sổ bot (thu nhỏ) và 2 cửa sổ log.
2. Bot **không đặt lệnh**, chỉ ghi log (và nhắn Telegram) lệnh nó *sẽ* vào:

| Dòng log | Nghĩa |
|---|---|
| `MNQ 31,050.25 \| WAIT (...)` | đang chờ, trong ngoặc là lý do |
| `ENTRY LONG 6 (1R = $150.00)` + khối Entry/SL/TP | tín hiệu vào lệnh |
| `chốt scalp ...` / `Trail SL -> ...` | chốt 1/2, dời SL |
| `Trade closed: +1.20R / $+180.00 (hôm nay ...)` | lệnh đóng, kết quả |
| `Bỏ qua LONG: ...` | có tín hiệu nhưng bị chặn (gần MLL, đạt mục tiêu...) |

3. Mỗi ngày ghi lại: số lệnh, tổng R, chuỗi thua dài nhất; đối chiếu vài lệnh với biểu đồ.
4. **Chỉ chạy thật khi** sau ≥ 5 ngày giao dịch: tổng R dương, ngày tệ nhất không quá ~−10R, log không có lỗi.

Dừng bot: nhấp đúp **`stop_nq_gc.bat`**.

## Bước 7 — Chạy thật

1. Chạy `stop_nq_gc.bat`.
2. Cả hai file: `BOT_DRY_RUN=true` → **`BOT_DRY_RUN=false`**. Tuần đầu nên đặt `BOT_RISK_USD=75`, ổn mới lên 150. Lưu.
3. Chạy `start_nq_gc.bat`.
4. **Lệnh đầu tiên — ngồi xem trên TopstepX:**
   - Log có dòng `Lệnh + OCO (SL .. tick, TP .. tick)`.
   - Vị thế có **đúng 1 lệnh Stop + 1 lệnh Limit**.
   - Sau khi chốt 1/2: hai lệnh còn **đúng nửa khối lượng**, Stop dời về giá vào.
   - Log báo `Không sửa được lệnh OCO` / `Chưa thấy lệnh SL của OCO` → **tắt bot, đóng lệnh bằng tay**, gửi log (che API key).

Cách bot xử lý OCO:

| Sự kiện | Bot làm |
|---|---|
| Vào lệnh | market + OCO: LONG → SL tick âm, TP tick dương; SHORT ngược lại |
| Chạm mức chốt 1/2 | giảm khối lượng 2 lệnh OCO trước, rồi đóng 1/2, dời SL về hoà vốn |
| Trail | sửa giá lệnh Stop của OCO |
| Thoát sớm (momentum tắt, 15 phút, giờ ra tin, 3:08 PM) | huỷ OCO rồi đóng hết |
| SL / TP khớp trên sàn | ghi nhận, huỷ lệnh còn lại nếu còn |

Lưu ý khi chạy thật:
- Máy **bật, có mạng, không ngủ**: Windows **Settings → System → Power** → *Sleep = Never*.
- Tắt bot giữa chừng không sao: SL vẫn nằm trên sàn; bật lại bot đọc `bot_state_momo_*.json` và quản lý tiếp.
- Không tự bấm lệnh MNQ/MGC khi bot đang chạy.
- Tắt bot xong luôn kiểm tra TopstepX còn vị thế / lệnh chờ nào không.

## Bot vào lệnh thế nào (tóm tắt)

**Rejection** (NQ + GC): vẽ volume profile từ nến 1M (NQ 240 phút, GC 120 phút), lấy vùng nhiều volume nhất → một nến 1M vừa đóng có râu ≥ 50% cây nến chọc vào vùng rồi đóng lại phía trên (GC: nến râu volume ≥ trung bình) → giá bật khỏi đáy râu 6–15 điểm (GC 0.8–2.0) → vào. SL dưới đáy râu 2 điểm (GC 0.3).

**Close** (chỉ NQ): nến 5M vừa đóng có thân ≥ 15 điểm và ≥ 0.8 × ATR, đóng gần đỉnh, chưa chạy quá xa → giá chưa hồi quá 30% thân nến → vào. SL dưới 2 nến 1M gần nhất.

**Chung:** SL 6–20 điểm (GC 1–3), xa hơn là vào trễ → bỏ. Mục tiêu 10–50 điểm (GC 1.5–7) theo độ mạnh, dừng trước đỉnh/đáy cũ hoặc vùng volume kế tiếp; mục tiêu < SL thì bỏ. Khối lượng = rủi ro ÷ (SL × giá trị điểm), tối đa 50 hợp đồng. Thử LONG trước rồi SHORT (ngược lại y hệt).

## Thông số chính

| Biến | NQ | GC | Ý nghĩa |
|---|---|---|---|
| `BOT_RISK_USD` | 150 | 150 | tiền rủi ro mỗi lệnh (1R) |
| `BOT_MAX_CONTRACTS` | 50 | 50 | tối đa micro mỗi bot (100K cho tổng 100) |
| `BOT_MAX_TRADES_PER_DAY` | 0 | 0 | 0 = không giới hạn số lệnh |
| `BOT_MAX_DAILY_LOSS_R` | 0 | 0 | 0 = không dừng theo R lỗ trong ngày |
| `BOT_PROP_MAX_LOSS_USD` | 3000 | 3000 | MLL 100K; bot ngừng vào lệnh khi còn cách mức sàn ~25% ($750) |
| `BOT_PROFIT_TARGET_USD` | 6000 | 6000 | đạt mục tiêu → ngừng |
| `BOT_FLATTEN_TIME_CT` | 15:08 | 15:08 | đóng hết lúc 3:08 PM Chicago |
| `TOPSTEP_OCO` | true | true | lệnh vào kèm OCO |
| `MOMO_ENTRY_MODE` | rejection,close | rejection | kiểu vào lệnh |
| `MOMO_SESSIONS` | 7-20 | (trống = 24h) | giờ UTC được mở lệnh |
| `MOMO_BLACKOUT` | 08:28-08:40 | 08:28-08:40 | giờ New York không vào lệnh, đóng lệnh đang mở (giờ ra tin) |
| `MOMO_MAX_PER_CANDLE` | 0 | 0 | 0 = không giới hạn số lệnh trong 1 nến 5M |
| `MOMO_VOL_MULT` | 0 | 1.0 | volume nến râu ≥ x lần trung bình |

Tài khoản **50K**: `BOT_ACCOUNT_SIZE=50000`, `BOT_PROP_MAX_LOSS_USD=2000`, `BOT_PROFIT_TARGET_USD=3000`, `BOT_MAX_CONTRACTS=25`, `BOT_RISK_USD=100` (setup gõ `50` là có sẵn). **150K**: 150000 / 4500 / 9000 / 75 / 200. Kiểm tra lại luật trên trang Topstep vì có thể đổi.

Hai bot dùng chung tài khoản: bot đọc **số dư thật** nên MLL/mục tiêu tính chung cho cả hai. Tài khoản đã có lời trước khi bật bot thì mức sàn bot tính có thể thấp hơn mức thật — xem MLL trên TopstepX.

## Rủi ro cần biết

- Không còn dừng theo R: một ngày xấu có thể thua liên tục tới khi gần mức sàn (~$2,100–2,250 ở $150/lệnh). Đừng tăng `BOT_RISK_USD` lên $300.
- Phí: không giới hạn lệnh → NQ có thể 50–100+ lệnh/ngày; backtest **chưa trừ phí hoa hồng**.
- Giờ ra tin / mở cửa: giá khớp thật xấu hơn backtest (trượt giá).

## Kết quả backtest (dữ liệu 1/10 02:10 → 2/10 16:43 UTC, ~1.5 ngày)

| Cấu hình hiện tại | Số lệnh | Kết quả | Đảo thứ tự giá + phí x2: sụt max |
|---|---|---|---|
| NQ | 211 | +85.0R | −10.8R |
| GC | 44 | +16.1R | −2.8R |

**Chưa tới 2 phiên giao dịch** → chưa chứng minh được gì chắc chắn. Kết nối, OCO và sửa lệnh chưa được thử trên máy chủ thật. **Bắt buộc dry-run trước.**

## Cập nhật bot sau này

Tải ZIP mới (Bước 3), giải nén **đè** lên `C:\bot`. `.env.momo`, `.env.momo.gc`, log và trạng thái không nằm trong ZIP nên được giữ nguyên. Khởi động lại bot.

---

## Phụ lục — nội dung đầy đủ 2 file (100K)

### `.env.momo` (NQ / MNQ)
```ini
BOT_BROKER=topstep
TOPSTEP_USERNAME=<ten dang nhap TopstepX>
TOPSTEP_API_KEY=<API key>
TOPSTEP_ACCOUNT=
TOPSTEP_API_URL=https://api.topstepx.com
TOPSTEP_OCO=true

BOT_STRATEGY=momentum
BOT_SYMBOL=MNQ
BOT_CONFIRM_SYMBOL=MES
BOT_POINT_VALUE=2
BOT_SIZE_DECIMALS=0
BOT_MAX_CONTRACTS=50

BOT_RISK_USD=150
BOT_MAX_DAILY_LOSS_R=0
BOT_MAX_TRADES_PER_DAY=0

BOT_ACCOUNT_SIZE=100000
BOT_PROP_MAX_LOSS_USD=3000
BOT_PROP_TRAILING=eod
BOT_PROP_DAILY_LOSS_PCT=0
BOT_PROP_DAILY_LOSS_USD=
BOT_PROP_SAFETY=0.75
BOT_PROFIT_TARGET_USD=6000
BOT_FLATTEN_TIME_CT=15:08
BOT_DAY_RESET=cme

BOT_POLL_SECONDS=5
BOT_STATE_FILE=bot_state_momo_MNQ.json
BOT_DRY_RUN=true

TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

MOMO_ENTRY_MODE=rejection,close
MOMO_VP_LOOKBACK=240
MOMO_SESSIONS=7-20
MOMO_BLACKOUT=08:28-08:40
MOMO_TREND_FILTER=0
MOMO_MAX_PER_CANDLE=0
```

### `.env.momo.gc` (GC / MGC)
```ini
BOT_BROKER=topstep
TOPSTEP_USERNAME=<ten dang nhap TopstepX>
TOPSTEP_API_KEY=<API key>
TOPSTEP_ACCOUNT=
TOPSTEP_API_URL=https://api.topstepx.com
TOPSTEP_OCO=true

BOT_STRATEGY=momentum
BOT_SYMBOL=MGC
BOT_CONFIRM_SYMBOL=
BOT_POINT_VALUE=10
BOT_SIZE_DECIMALS=0
BOT_MAX_CONTRACTS=50

BOT_RISK_USD=150
BOT_MAX_DAILY_LOSS_R=0
BOT_MAX_TRADES_PER_DAY=0

BOT_ACCOUNT_SIZE=100000
BOT_PROP_MAX_LOSS_USD=3000
BOT_PROP_TRAILING=eod
BOT_PROP_DAILY_LOSS_PCT=0
BOT_PROP_DAILY_LOSS_USD=
BOT_PROP_SAFETY=0.75
BOT_PROFIT_TARGET_USD=6000
BOT_FLATTEN_TIME_CT=15:08
BOT_DAY_RESET=cme

BOT_POLL_SECONDS=5
BOT_STATE_FILE=bot_state_momo_MGC.json
BOT_DRY_RUN=true

TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

MOMO_ENTRY_MODE=rejection
MOMO_VOL_MULT=1.0
MOMO_VP_LOOKBACK=120
MOMO_SESSIONS=
MOMO_BLACKOUT=08:28-08:40
MOMO_TREND_FILTER=0
MOMO_MAX_PER_CANDLE=0
MOMO_VP_BIN=0.5
MOMO_ZONE_W=0.4
MOMO_FAST_MOVE=0.8
MOMO_MIN_5M_MOVE=2.0
MOMO_MIN_ATR_MULT=0.8
MOMO_MIN_1M_MOVE=1.2
MOMO_MIN_ATR5=1.2
MOMO_MIN_TP=1.5
MOMO_MAX_TP=7.0
MOMO_SCALP_TP=1.5
MOMO_MIN_SL=1.0
MOMO_MAX_SL=3.0
MOMO_SL_BUFFER=0.3
MOMO_TRAIL=1.2
MOMO_FADE_BODY=1.0
MOMO_MAX_HOLD_MIN=15
MOMO_TICK=0.1
```
