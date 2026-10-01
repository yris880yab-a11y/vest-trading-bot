"""Signature helpers for the Vest API.

Vest uses two kinds of signatures:

* Registration: the *primary* wallet signs an EIP-712 ``SignerProof`` that
  delegates trading rights to a separate *signing* key.
* Every private POST (orders, cancels, ...): the *signing* key signs
  ``keccak(abi.encode(...fields))`` as an EIP-191 personal message.
"""
from __future__ import annotations

from eth_abi import encode
from eth_account import Account
from eth_account.messages import encode_defunct, encode_typed_data
from eth_utils import keccak

ORDER_TYPES = ["uint256", "uint256", "string", "string", "bool", "string", "string", "bool"]
CANCEL_TYPES = ["uint256", "uint256", "string"]


def _hex(sig: bytes) -> str:
    h = sig.hex()
    return h if h.startswith("0x") else "0x" + h


def sign_fields(types: list[str], values: list, private_key: str) -> str:
    digest = keccak(encode(types, values))
    return _hex(Account.sign_message(encode_defunct(digest), private_key).signature)


def sign_order(
    *,
    time_ms: int,
    nonce: int,
    order_type: str,
    symbol: str,
    is_buy: bool,
    size: str,
    limit_price: str,
    reduce_only: bool,
    private_key: str,
) -> str:
    return sign_fields(
        ORDER_TYPES,
        [time_ms, nonce, order_type, symbol, is_buy, size, limit_price, reduce_only],
        private_key,
    )


def sign_cancel(*, time_ms: int, nonce: int, order_id: str, private_key: str) -> str:
    return sign_fields(CANCEL_TYPES, [time_ms, nonce, order_id], private_key)


def sign_signer_proof(
    *,
    primary_private_key: str,
    signing_address: str,
    expiry_ms: int,
    verifying_contract: str,
) -> str:
    """EIP-712 proof that the primary wallet approves ``signing_address``."""
    typed = {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "verifyingContract", "type": "address"},
            ],
            "SignerProof": [
                {"name": "approvedSigner", "type": "address"},
                {"name": "signerExpiry", "type": "uint256"},
            ],
        },
        "primaryType": "SignerProof",
        "domain": {
            "name": "VestRouterV2",
            "version": "0.0.1",
            "verifyingContract": verifying_contract,
        },
        "message": {"approvedSigner": signing_address, "signerExpiry": expiry_ms},
    }
    signable = encode_typed_data(full_message=typed)
    return _hex(Account.sign_message(signable, primary_private_key).signature)
