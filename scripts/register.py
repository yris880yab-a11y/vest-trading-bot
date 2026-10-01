"""Create a signing key and register it with Vest to obtain an API key.

Usage:
    PRIMARY_PRIVATE_KEY=0x... python scripts/register.py

The primary wallet is the one you deposited to Vest with. Its key is only used
here to sign the delegation proof; the bot itself only needs the new signing key.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eth_account import Account  # noqa: E402

from vestbot.client import VestClient  # noqa: E402
from vestbot.config import Config  # noqa: E402
from vestbot.signing import sign_signer_proof  # noqa: E402

EXPIRY_DAYS = int(os.getenv("SIGNER_EXPIRY_DAYS", "7"))


def main() -> None:
    primary_key = os.getenv("PRIMARY_PRIVATE_KEY")
    if not primary_key:
        sys.exit("Set PRIMARY_PRIVATE_KEY (the wallet you trade with on Vest)")
    cfg = Config.from_env()

    primary = Account.from_key(primary_key)
    signing = Account.create()
    expiry_ms = int(time.time() * 1000) + EXPIRY_DAYS * 24 * 3600 * 1000

    signature = sign_signer_proof(
        primary_private_key=primary_key,
        signing_address=signing.address,
        expiry_ms=expiry_ms,
        verifying_contract=cfg.verifying_contract,
    )
    resp = VestClient(cfg.rest_url).register(
        signing_addr=signing.address,
        primary_addr=primary.address,
        signature=signature,
        expiry_ms=expiry_ms,
    )
    print("Register response:", resp)
    print("\nAdd these lines to your .env (keep them secret!):\n")
    print(f"VEST_API_KEY={resp.get('apiKey')}")
    print(f"VEST_ACCOUNT_GROUP={resp.get('accGroup')}")
    print(f"VEST_SIGNING_PRIVATE_KEY={signing.key.hex()}")
    print(f"\nSigning key expires in {EXPIRY_DAYS} days; re-run this script before then.")


if __name__ == "__main__":
    main()
