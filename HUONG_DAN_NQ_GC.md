# Hướng dẫn cài bot scalp NQ + GC trên Topstep (Windows)

Hai bot chạy song song trên **máy tính cá nhân của bạn**:

| Bot | Mã | Cách vào lệnh | Giờ chạy | File cấu hình | Log |
|---|---|---|---|---|---|
| NQ | MNQ ($2/điểm) | Nến 1M rút râu tại vùng nhiều volume + nến 5M đóng mạnh (`rejection,close`) | 14:00 – 03:00 giờ VN (London + New York) | `.env.momo` | `bot_momo.log` |
| GC | MGC ($10/điểm) | Nến 1M rút râu tại vùng nhiều volume, volume nến ≥ trung bình (`rejection`) | 24 giờ (trừ giờ Topstep bắt đóng lệnh) | `.env.momo.gc` | `bot_momo_gc.log` |

Mỗi lệnh: rủi ro $100 (1R), chốt 1/2 ở +10 điểm NQ / +1.5 điểm GC rồi dời SL về hoà vốn, phần còn lại trail; thoát khi nến 1M ngược mạnh hoặc giữ quá 15 phút. Lệnh SL thật được đặt trên sàn.

> **Luật Topstep bắt buộc:** chỉ chạy trên máy của bạn (cấm VPS, VPN, server), chỉ dùng cho **Trading Combine / Express Funded** (không dùng API cho Live Funded), không HFT. Bot tự đóng lệnh lúc 3:08 PM giờ Chicago.

---

## Bước 1 — Chuẩn bị tài khoản

1. Có tài khoản **Topstep Trading Combine 50K** (hoặc Express Funded).
2. Mở **TopstepX** → **Settings** → **API**: đăng ký gói API của ProjectX (tính phí hằng tháng, xem giá trên trang đó) → tạo **API key** → copy lại.
3. Ghi lại **tên đăng nhập TopstepX** (username, không phải email nếu hai cái khác nhau).
4. (Tuỳ chọn) Telegram để nhận tin nhắn khi bot vào/chốt lệnh:
   - Nhắn `/newbot` cho **@BotFather** → đặt tên → nhận **token**.
   - Nhắn 1 tin bất kỳ cho bot vừa tạo, rồi mở `https://api.telegram.org/bot<TOKEN>/getUpdates` → số trong `"chat":{"id": ...}` là **chat id**.

## Bước 2 — Cài Python

1. Tải Python 3.11 hoặc 3.12 tại https://www.python.org/downloads/windows/
2. Khi cài, **tick ô "Add python.exe to PATH"** → Install.

## Bước 3 — Tải bot về máy

1. Mở https://github.com/yris880yab-a11y/vest-trading-bot/tree/claude/vestmarkets-trading-bot-ki86ah
2. Bấm nút xanh **Code** → **Download ZIP**.
3. Giải nén vào thư mục ngắn, ví dụ `C:\bot` (để các file `.bat` nằm ngay trong `C:\bot`).

## Bước 4 — Cài đặt (làm 1 lần)

1. Nhấp đúp **`setup_nq_gc.bat`**. Script tạo môi trường Python, cài thư viện, rồi mở 2 file `.env.momo` và `.env.momo.gc` bằng Notepad.
2. Trong **cả hai file**, sửa 2 dòng (và Telegram nếu có):

| Dòng trong file | Điền |
|---|---|
| `TOPSTEP_USERNAME=` | tên đăng nhập TopstepX |
| `TOPSTEP_API_KEY=` | API key ở Bước 1 |
| `TOPSTEP_ACCOUNT=` | để trống nếu chỉ có 1 tài khoản; nhiều tài khoản thì điền tên tài khoản Combine (xem Bước 5) |
| `TELEGRAM_BOT_TOKEN=` | token Telegram (tuỳ chọn) |
| `TELEGRAM_CHAT_ID=` | chat id Telegram (tuỳ chọn) |

3. **Ctrl+S** lưu cả hai file. Giữ nguyên `BOT_DRY_RUN=true` lúc này.

> Không gửi file `.env.momo*` hay API key cho ai. Khi chụp màn hình lỗi, che API key đi.

## Bước 5 — Kiểm tra kết nối

Nhấp đúp **`check_nq_gc.bat`**. Kết quả đúng trông như sau (cho cả NQ và GC):

```
1) Đăng nhập + tài khoản:
   - 50KTC-V2-xxxxx-xxxxxxx  (id 123456, số dư 50000)
2) Tài khoản bot sẽ dùng: id 123456
3) MNQ: CON.F.US.MNQ.Z25 | nến 1m mới nhất: {...}
OK — kết nối TopstepX hoạt động.
```

| Lỗi | Cách sửa |
|---|---|
| `401` / `Thiếu TOPSTEP_USERNAME` | sai username hoặc API key, hoặc chưa lưu file |
| `Đặt TOPSTEP_ACCOUNT = ...` | có nhiều tài khoản: copy đúng tên tài khoản Combine vào `TOPSTEP_ACCOUNT=` ở **cả hai file** |
| `Không tìm thấy hợp đồng` | ngoài giờ giao dịch hoặc gói API chưa có dữ liệu; thử lại khi thị trường mở |
| `'py' is not recognized` / `python không tìm thấy` | cài lại Python và tick "Add to PATH" |

Nếu `api.topstepx.com` không kết nối được, đổi `TOPSTEP_API_URL=https://api.topstepx.projectx.com` ở cả hai file.

## Bước 6 — Chạy thử (dry-run) ít nhất 3–5 ngày

1. Nhấp đúp **`start_nq_gc.bat`**: mở 2 cửa sổ bot (thu nhỏ) và 2 cửa sổ log.
2. Bot **không đặt lệnh**, chỉ ghi log (và nhắn Telegram) lệnh nó *sẽ* vào. Đọc log:

| Dòng log | Nghĩa |
|---|---|
| `MNQ 21,050.25 \| WAIT (...)` | đang chờ, trong ngoặc là lý do chưa vào |
| `ENTRY LONG 5 (1R = $100.00)` + khối Entry/SL/TP | tín hiệu vào lệnh |
| `chốt scalp ...` / `Trail SL -> ...` | chốt 1/2, dời SL |
| `Trade closed: +1.20R / $+120.00 (hôm nay ...)` | lệnh đóng, kết quả |
| `Bỏ qua LONG: ...` | có tín hiệu nhưng bị chặn (hết giới hạn lỗ ngày, gần mức MLL...) |

3. Mỗi ngày đối chiếu vài lệnh với biểu đồ TopstepX. Dừng bot: nhấp đúp **`stop_nq_gc.bat`** (hoặc đóng cửa sổ "BOT NQ/GC").

## Bước 7 — Chạy thật

Khi dry-run ổn:

1. Chạy `stop_nq_gc.bat`.
2. Trong **cả hai file** `.env.momo` và `.env.momo.gc`, sửa `BOT_DRY_RUN=true` → `BOT_DRY_RUN=false`, lưu.
3. Chạy `start_nq_gc.bat`. Lệnh đầu tiên: mở TopstepX xem có **vị thế + lệnh Stop** tương ứng không.

Lưu ý khi chạy thật:
- Máy phải **bật, có mạng, không ngủ**: Windows **Settings → System → Power** → *Sleep = Never* khi cắm sạc.
- Tắt bot giữa chừng không sao: lệnh SL vẫn nằm trên sàn; bật lại, bot đọc `bot_state_momo_*.json` và quản lý tiếp.
- Đừng tự bấm lệnh MNQ/MGC trên TopstepX khi bot đang chạy (bot thấy vị thế lạ sẽ không vào lệnh mới).
- Sau khi tắt bot luôn kiểm tra TopstepX xem còn vị thế / lệnh chờ nào không.

## Thông số chính (đã cài sẵn — chỉ chỉnh khi cần)

| Biến | NQ | GC | Ý nghĩa |
|---|---|---|---|
| `BOT_RISK_USD` | 100 | 100 | tiền rủi ro mỗi lệnh (1R) |
| `BOT_MAX_CONTRACTS` | 25 | 25 | tối đa hợp đồng micro mỗi bot (Topstep 50K cho tổng 50) |
| `BOT_MAX_DAILY_LOSS_R` | 3 | 3 | lỗ 3R trong ngày → ngừng vào lệnh (2 bot = tối đa ~$600/ngày) |
| `BOT_MAX_TRADES_PER_DAY` | 15 | 15 | số lệnh tối đa mỗi ngày |
| `BOT_PROP_MAX_LOSS_USD` | 2000 | 2000 | MLL của Topstep 50K; bot chừa 25% an toàn (`BOT_PROP_SAFETY=0.75`) |
| `BOT_PROFIT_TARGET_USD` | 3000 | 3000 | đạt mục tiêu Combine → ngừng |
| `BOT_FLATTEN_TIME_CT` | 15:08 | 15:08 | đóng hết lệnh lúc 3:08 PM Chicago |
| `MOMO_ENTRY_MODE` | rejection,close | rejection | kiểu vào lệnh |
| `MOMO_SESSIONS` | 7-20 | (trống) | giờ UTC được mở lệnh; trống = 24h |
| `MOMO_VOL_MULT` | 0 | 1.0 | volume nến rút râu ≥ x lần trung bình |
| `MOMO_SCALP_TP` | 10 | 1.5 | chốt 1/2 (điểm) |
| `MOMO_MAX_SL` | 20 | 3.0 | SL xa hơn → bỏ lệnh |

Hai bot dùng chung một tài khoản: bot đọc **số dư thật** của tài khoản nên giới hạn MLL/target tính chung cho cả hai.

Muốn rủi ro thấp hơn lúc đầu: đặt `BOT_RISK_USD=50` ở cả hai file.

## Kết quả backtest (để biết nên kỳ vọng gì)

| | Số lệnh | Kết quả | Đảo giá + phí x2 | Dữ liệu mới (sau khi chỉnh) |
|---|---|---|---|---|
| NQ (London + NY) | 72 | +37.8R | +31.3R | — |
| GC (24h, lọc volume) | 28 | +11.7R | +13.9R | +5.7R |

**Chỉ có ~29 giờ dữ liệu** → kết quả này chưa chứng minh được gì chắc chắn. Kết nối Topstep và lệnh Stop trên sàn chưa được thử với máy chủ thật. Vì vậy **bắt buộc dry-run trước**, rồi chạy thật với rủi ro nhỏ.

## Cập nhật bot sau này

Tải ZIP mới (Bước 3), giải nén **đè** lên `C:\bot`. Các file `.env.momo`, `.env.momo.gc`, log và trạng thái của bạn không nằm trong ZIP nên được giữ nguyên.
