from eth_abi import encode
from eth_account import Account
from eth_account.messages import encode_defunct
from eth_utils import keccak

from vestbot.signing import ORDER_TYPES, sign_cancel, sign_order, sign_signer_proof

KEY = "0x" + "11" * 32
ADDR = Account.from_key(KEY).address


def test_order_signature_recovers_signer():
    values = [1700000000000, 1700000000000, "MARKET", "BTC-PERP", True, "0.001", "65000", False]
    sig = sign_order(time_ms=values[0], nonce=values[1], order_type=values[2], symbol=values[3],
                     is_buy=values[4], size=values[5], limit_price=values[6],
                     reduce_only=values[7], private_key=KEY)
    msg = encode_defunct(keccak(encode(ORDER_TYPES, values)))
    assert Account.recover_message(msg, signature=sig) == ADDR


def test_cancel_signature_is_hex():
    sig = sign_cancel(time_ms=1, nonce=1, order_id="abc", private_key=KEY)
    assert sig.startswith("0x") and len(sig) == 132


def test_signer_proof():
    signing = Account.create()
    sig = sign_signer_proof(primary_private_key=KEY, signing_address=signing.address,
                            expiry_ms=1, verifying_contract="0x919386306C47b2Fe1036e3B4F7C40D22D2461a23")
    assert sig.startswith("0x") and len(sig) == 132
