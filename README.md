# Vest Trading Bot

Bot tự động giao dịch hợp đồng perpetual trên sàn [Vest](https://www.vestmarkets.com/), viết bằng Python.

> ⚠️ **Cảnh báo rủi ro:** Giao dịch phái sinh có đòn bẩy có thể mất toàn bộ vốn. Đây là mã mẫu, không phải lời khuyên đầu tư. Hãy chạy ở chế độ `BOT_DRY_RUN=true` và/hoặc môi trường `dev` trước, rồi mới dùng tiền thật với khối lượng nhỏ.

## Chiến lược mặc định: `smc` — sweep → MSS → retest đa khung

Flow: **Daily/4H context → 1H confirm → 15M location → 5M sweep + MSS → 1M trigger/retest → momentum → entry → SL theo structure → TP theo liquidity.**

| Bước | Khung | Bot kiểm tra |
|---|---|---|
| 1. Bối cảnh | Daily / 4H | HH/HL hay LH/LL, premium/discount 4H → chỉ là bias, không ép hướng scalp |
| 2. Context | 1H | structure, có sweep liquidity không, có displacement không |
| 3. Location | 15M | FVG, order block, BSL/SSL (swing high/low), premium/discount; giữa range → không trade |
| 4. Hướng scalp | 5M | sweep → MSS; **5M quyết định hướng**, được phép ngược 4H nếu structure rõ |
| 5. Trigger | 1M | sweep → displacement → phá swing (giá MSS cụ thể) → retest fail/hold |
| 6. Momentum | 1M | 0–5: displacement, push 3–4 nến, acceleration, 2 ATR expansion, market confirm (vd ES) |
| 7. Entry | 1M | khi retest vùng MSS fail (short) / hold (long) |
| 8. SL | — | trên sweep high/retest (short), dưới sweep low/retest (long) — không dùng SL cố định |
| 9. Target | — | TP1: liquidity gần nhất 1M/5M · TP2: 15M/PDH-PDL · TP3: 1H/4H · Runner: Daily |
| 10. Quản lý | — | TP1 chốt 50% + dời SL về entry, TP2 chốt tiếp + SL lên TP1, không add/đuổi; 1M MSS ngược → trước TP1 cắt 50%, sau TP1 thoát runner; 5M MSS ngược → thoát hết |
| 11. WAIT | — | giữa range, chưa sweep, chưa MSS, MSS đến chậm sau sweep, setup 5M đã cũ, break chưa retest, momentum < 3, chạy quá xa location, TP1 < 0.5R, 1M/5M mâu thuẫn |

Xem nhanh phân tích (không đặt lệnh), in đúng format Bias → Retest zone → MSS → Retest → Entry → SL → TP1/2/3 → target xa → Momentum → LONG/SHORT/WAIT:

```bash
python -m vestbot.analyze NQ-PERP --confirm ES-PERP
```

```
=== NQ-PERP @ 30,558.00 ===
Bias          : D BULL | 4H BULL | 4H premium 52% → HTF BULL (scalp SHORT ngược HTF — chỉ trade nếu structure rõ)
1H context    : BULL, sweep BSL 30,601.00, displacement ✗
Retest zone   : 30,599.61–30,601.00 (BSL 15M)  (15M range 54% premium)
MSS 5M        : SHORT — sweep 30,601.00, phá 30,574.00
MSS 1M (exact): SHORT — phá 30,574.00 (sweep 30,612.00)
Retest sau MSS: 30,574.00–30,577.77 → CONFIRMED
Entry         : 30,558.00
SL            : 30,613.39  (structural invalidation)
TP1/TP2/TP3   : 30,509.00 / 30,507.00 / 30,505.00
Target xa     : 30,503.00
Momentum      : 3/5 VALID  [displacement ✓, push 3-4 nến ✓, acceleration ✗, 2 ATR expansion ✓, market confirm ✗]
=> SHORT
```
(ví dụ trên chạy từ dữ liệu giả lập trong test)

Chiến lược EMA crossover cũ vẫn dùng được với `BOT_STRATEGY=ema`.

**Giới hạn khi máy hoá framework:** những phần vốn cần mắt người đã được quy thành luật cố định — swing = fractal 2 nến mỗi bên, displacement = thân nến ≥ 1 ATR, vùng retest = mức MSS + 0.3 ATR, "quá xa" = > 2 ATR khỏi vùng retest, session level hiện chỉ dùng PDH/PDL. Các ngưỡng chính nằm đầu file `vestbot/smc.py`:

| Hằng số | Mặc định | Ý nghĩa |
|---|---|---|
| `TP1_MIN_R` | 0.5 | liquidity gần hơn 0.5R không tính là target |
| `MAX_CHASE_ATR15` | 1.5 | không vào nếu giá đã chạy quá 1.5 ATR(15M) khỏi mức MSS 5M |
| `FRESH_5M_BARS` | 12 | trigger 1M phải đến trong 12 nến 5M (1 giờ) sau MSS 5M |
| `MSS_MAX_BARS` | 12 | MSS phải đến trong 12 nến sau sweep (sweep → displacement → MSS, không phải bò dần) | SL/TP được bot tự theo dõi mỗi `BOT_POLL_SECONDS` giây (không đặt lệnh stop trên sàn), nên bot phải chạy liên tục.

### An toàn khi chạy thật

- `BOT_RISK_USD`: số tiền chấp nhận mất mỗi lệnh (= 1R). Bot tự tính khối lượng = `BOT_RISK_USD / |entry − SL|`, nên SL xa thì vào ít, SL gần thì vào nhiều. Vì SL theo structure thường chỉ cách 0.15–0.3% giá, giá trị vị thế ≈ 4–6 lần số vốn khi rủi ro 1%/lệnh → cần đòn bẩy ~10x. `BOT_MAX_NOTIONAL_USD` chặn trường hợp SL quá gần.

- `BOT_MAX_DAILY_LOSS_R` (mặc định 3): lỗ đủ 3R trong ngày (phiên CME, bắt đầu 22:00 UTC = 5:00 sáng VN) thì ngừng vào lệnh mới.
- `BOT_MAX_TRADES_PER_DAY` (mặc định 6): tối đa 6 lệnh mỗi ngày.
- `BOT_STATE_FILE` (mặc định `bot_state.json`): lưu lệnh đang mở (SL, TP đã chạm, R đã chốt). Bot tắt/bật lại vẫn quản lý tiếp lệnh đó; nếu trên sàn không còn vị thế thì bỏ trạng thái cũ.

### Tài khoản funded (Vest Capital)

Đặt `BOT_ACCOUNT_SIZE` (5000 / 10000 / 25000) để bật chế độ funded:

- Chỉ vào lệnh mới nếu chạm SL vẫn giữ lỗ trong ngày dưới 75% giới hạn 4% của quỹ, và số dư vẫn cách xa mức sàn drawdown tĩnh 6%.
- Đang có lệnh mà lỗ trong ngày (tính cả phần chưa chốt) chạm 90% giới hạn, hoặc equity gần mức sàn, thì đóng hết ngay.
- Ngày reset lúc 8:00 PM giờ New York, giống Vest Capital.
- `BOT_PROFIT_TARGET_USD`: evaluation đạt mục tiêu 10% thì ngừng giao dịch.

**Vest Capital không cho dùng bot đặt lệnh.** Khi đặt `BOT_ACCOUNT_SIZE`, bot từ chối chạy nếu `BOT_DRY_RUN=false`: chỉ dùng chế độ báo lệnh qua Telegram và tự vào lệnh bằng tay.

### Không có API: chế độ báo lệnh qua Telegram

Nếu tài khoản không được dùng API/bot, để `BOT_DRY_RUN=true` và điền `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`. Bot không đặt lệnh, chỉ nhắn: vào lệnh (hướng, giá, khối lượng, SL, TP1/2/3), chạm TP và chốt bao nhiêu, dời SL, thoát lệnh. Bạn tự bấm lệnh trên web Vest. Không cần API key, vì dữ liệu nến là dữ liệu công khai.

## Backtest

Phát lại dữ liệu từng phút qua đúng code bot (không nhìn trước tương lai, có tính 1 tick phí + trượt giá mỗi chiều):

```bash
# cần data/{MÃ}_{1m,5m,15m,1h,4h,1d}.csv với cột time,open,high,low,close (time = UTC)
python scripts/backtest.py NQ --confirm ES
python scripts/backtest.py GC --set TP1_MIN_R=0.4 --cost 0.2   # thử ngưỡng khác
```

## Cấu trúc

```
vestbot/
  config.py    # đọc cấu hình từ .env
  signing.py   # ký EIP-712 (đăng ký) và ký lệnh (keccak + personal_sign)
  client.py    # REST client: exchangeInfo, ticker, klines, depth, account, orders, cancel
  smc.py       # framework sweep → MSS → retest (thuần, test offline được)
  smc_bot.py   # vào lệnh, chốt từng phần, dời SL theo framework
  analyze.py   # in báo cáo phân tích 1 lần
  strategy.py  # EMA crossover (chiến lược cũ)
  bot.py       # vòng lặp giao dịch
scripts/register.py  # tạo signing key + lấy API key
scripts/backtest.py  # backtest từng phút trên dữ liệu lịch sử
tests/               # unit test (chạy offline)
```

## Cài đặt

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

## Bước 1 — Đăng ký API key

Vest dùng mô hình **ví chính (primary) + khoá ký (signing key)**: ví chính ký một lần để uỷ quyền cho signing key; bot chỉ giữ signing key, không cần private key ví chính.

```bash
PRIMARY_PRIVATE_KEY=0x...ví_đã_nạp_tiền_trên_Vest... python scripts/register.py
```

Script in ra `VEST_API_KEY`, `VEST_ACCOUNT_GROUP`, `VEST_SIGNING_PRIVATE_KEY` → dán vào `.env`. Signing key hết hạn sau 7 ngày (đổi bằng `SIGNER_EXPIRY_DAYS`), nhớ chạy lại trước khi hết hạn.

## Bước 2 — Chạy bot

```bash
python -m vestbot            # dry-run: chỉ log tín hiệu, không đặt lệnh

# Lưu ý: kiểm tra Vest có niêm yết mã bạn muốn (NQ, GC, ...) và đặt đúng BOT_SYMBOL
```

Khi kết quả dry-run ổn, đặt `BOT_DRY_RUN=false` trong `.env` để giao dịch thật.

## Test

```bash
python -m pytest -q
```

## Lưu ý về API

Endpoint và cách ký lệnh dựa trên tài liệu chính thức: <https://docs.vestmarkets.com/vest-api>.

- REST: `https://server-prod.hz.vestmarkets.com/v2` (dev: `server-dev`)
- Header private: `X-API-KEY`, `xrestservermm: restserver{accountGroup}`
- Ký lệnh: `keccak(abi.encode(time, nonce, orderType, symbol, isBuy, size, limitPrice, reduceOnly))`

Một vài chi tiết (định dạng response của `/account`, `/klines`, tên field leverage, có cần `chainId` trong domain EIP-712 hay không) chưa được kiểm chứng trực tiếp với server; code đã parse linh hoạt và có thể chỉnh trong `client.py` / `signing.py` nếu server trả lỗi. Hãy thử trên `VEST_ENV=dev` trước.
