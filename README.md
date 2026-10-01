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
| 10. Quản lý | — | TP1 chốt 50% + dời SL về entry, TP2 chốt tiếp + SL lên TP1, không add/đuổi; 1M MSS ngược → thoát hết |
| 11. WAIT | — | giữa range, chưa sweep, chưa MSS, break chưa retest, momentum < 3, chạy quá xa, 1M/5M mâu thuẫn |

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

**Giới hạn khi máy hoá framework:** những phần vốn cần mắt người đã được quy thành luật cố định — swing = fractal 2 nến mỗi bên, displacement = thân nến ≥ 1 ATR, vùng retest = mức MSS + 0.3 ATR, "quá xa" = > 2 ATR khỏi vùng retest, session level hiện chỉ dùng PDH/PDL. Có thể chỉnh trong `vestbot/smc.py`. SL/TP được bot tự theo dõi mỗi `BOT_POLL_SECONDS` giây (không đặt lệnh stop trên sàn), nên bot phải chạy liên tục.

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
