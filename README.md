# Vest Trading Bot

Bot tự động giao dịch hợp đồng perpetual trên sàn [Vest](https://www.vestmarkets.com/), viết bằng Python.

> ⚠️ **Cảnh báo rủi ro:** Giao dịch phái sinh có đòn bẩy có thể mất toàn bộ vốn. Đây là mã mẫu, không phải lời khuyên đầu tư. Hãy chạy ở chế độ `BOT_DRY_RUN=true` và/hoặc môi trường `dev` trước, rồi mới dùng tiền thật với khối lượng nhỏ.

## Chiến lược mặc định

- **Tín hiệu:** EMA nhanh (9) cắt EMA chậm (21) trên nến đã đóng (`BOT_INTERVAL`, mặc định 15m).
  - EMA nhanh cắt lên → mở **LONG**; cắt xuống → mở **SHORT**.
  - Mỗi tín hiệu chỉ được dùng một lần (không mở lại lệnh trên cùng cây nến).
- **Thoát lệnh:** cắt lỗ `BOT_STOP_LOSS_PCT`, chốt lời `BOT_TAKE_PROFIT_PCT`, hoặc khi có tín hiệu ngược chiều.
- **Lệnh:** lệnh MARKET kèm giá giới hạn trượt giá `BOT_MAX_SLIPPAGE_PCT`; lệnh đóng dùng `reduceOnly`.

Logic chiến lược nằm trong `vestbot/strategy.py` (hàm thuần, dễ thay bằng chiến lược của bạn).

## Cấu trúc

```
vestbot/
  config.py    # đọc cấu hình từ .env
  signing.py   # ký EIP-712 (đăng ký) và ký lệnh (keccak + personal_sign)
  client.py    # REST client: exchangeInfo, ticker, klines, depth, account, orders, cancel
  strategy.py  # EMA crossover, SL/TP
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
