import time

import pytest

from slave_bot import transport_security


def test_verify_request_rejects_expired_and_bad_signatures():
    secret = "s" * 64
    now = int(time.time())
    body = b"{}"
    signature = transport_security.request_signature(
        secret, str(now), "nonce", "POST", "/instructions", body
    )
    assert transport_security.verify_request(
        secret,
        timestamp=str(now),
        nonce="nonce",
        signature=signature,
        method="POST",
        target="/instructions",
        body=body,
        now=now,
    ) == "nonce"
    with pytest.raises(ValueError, match="expired"):
        transport_security.verify_request(
            secret,
            timestamp=str(now - 31),
            nonce="nonce",
            signature=signature,
            method="POST",
            target="/instructions",
            body=body,
            now=now,
        )
    with pytest.raises(ValueError, match="invalid transport signature"):
        transport_security.verify_request(
            secret,
            timestamp=str(now),
            nonce="nonce",
            signature="bad",
            method="POST",
            target="/instructions",
            body=body,
            now=now,
        )
