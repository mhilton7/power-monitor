from __future__ import annotations

import hashlib
import hmac
import json
from urllib.parse import parse_qsl, quote, urlsplit


def hkdf(secret: bytes, info: bytes) -> bytes:
    prk = hmac.new(bytes(32), secret, hashlib.sha256).digest()
    return hmac.new(prk, info + b"\x01", hashlib.sha256).digest()


def canonical_target(target: str) -> str:
    split = urlsplit(target)
    pairs = sorted(
        (quote(key, safe="~-._"), quote(value, safe="~-._"))
        for key, value in parse_qsl(split.query, keep_blank_values=True)
    )
    return split.path + (
        "?" + "&".join(f"{key}={value}" for key, value in pairs) if pairs else ""
    )


def main() -> None:
    fixture_key_hex = "000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f"
    body = b'{"device_id":"123e4567-e89b-12d3-a456-426614174000","sequence":42}'
    target = canonical_target(
        "/api/v1/device-readings/batch?z=last&a=hello%20world&a=&slash=%2F"
    )
    timestamp = "1784558400"
    nonce = "0123456789abcdef0123456789abcdef"
    digest = hashlib.sha256(body).hexdigest()
    canonical = "\n".join(
        ("PM-HMAC-SHA256-V1", "POST", target, timestamp, nonce, digest)
    )
    key = hkdf(bytes.fromhex(fixture_key_hex), b"pm-device-to-server-v1")
    signature = hmac.new(key, canonical.encode(), hashlib.sha256).hexdigest()
    print(
        json.dumps(
            {
                "protocol": "pm-protocol/1.0.0",
                "vectors": [
                    {
                        "name": "device push with duplicate and escaped query values",
                        "secret_encoding": "hex",
                        "secret": fixture_key_hex,
                        "direction": "device-to-server",
                        "method": "POST",
                        "target_input": (
                            "/api/v1/device-readings/batch?z=last&a=hello%20world&a=&slash=%2F"
                        ),
                        "canonical_target": target,
                        "timestamp": timestamp,
                        "nonce": nonce,
                        "body_utf8": body.decode(),
                        "content_sha256": digest,
                        "canonical_string": canonical,
                        "derived_key_hex": key.hex(),
                        "signature": signature,
                    }
                ],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
