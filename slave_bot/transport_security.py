from __future__ import annotations

import hashlib
import hmac
import time

TIMESTAMP_HEADER = "X-Tradebot-Timestamp"
NONCE_HEADER = "X-Tradebot-Nonce"
SIGNATURE_HEADER = "X-Tradebot-Signature"
RESPONSE_SIGNATURE_HEADER = "X-Tradebot-Response-Signature"
MAX_CLOCK_SKEW_SECONDS = 30


def request_signature(secret: str, timestamp: str, nonce: str, method: str, target: str, body: bytes) -> str:
    digest = hashlib.sha256(body).hexdigest()
    payload = f"{timestamp}\n{nonce}\n{method.upper()}\n{target}\n{digest}".encode()
    return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()


def response_signature(secret: str, nonce: str, status: int, body: bytes) -> str:
    digest = hashlib.sha256(body).hexdigest()
    payload = f"{nonce}\n{status}\n{digest}".encode()
    return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()


def verify_request(
    secret: str,
    *,
    timestamp: str | None,
    nonce: str | None,
    signature: str | None,
    method: str,
    target: str,
    body: bytes,
    now: int | None = None,
) -> str:
    if not timestamp or not nonce or not signature:
        raise ValueError("missing transport signature headers")
    try:
        sent_at = int(timestamp)
    except ValueError as exc:
        raise ValueError("invalid transport timestamp") from exc
    current = int(time.time()) if now is None else now
    if abs(current - sent_at) > MAX_CLOCK_SKEW_SECONDS:
        raise ValueError("transport signature timestamp expired")
    expected = request_signature(secret, timestamp, nonce, method, target, body)
    if not hmac.compare_digest(signature, expected):
        raise ValueError("invalid transport signature")
    return nonce
