"""Opaque cursors for immutable, session-scoped catalogs."""

import base64
import hashlib
import hmac
import json
import secrets
from copy import deepcopy


class CursorError(ValueError):
    pass


class CatalogPages:
    def __init__(self, namespace):
        self._namespace = namespace
        self._secret = secrets.token_bytes(32)

    def page(self, items, context, cursor=None, *, size=5, key="items"):
        bound = hashlib.sha256(
            json.dumps([self._namespace, context, size, key], sort_keys=True).encode()
        ).hexdigest()
        offset = 0
        if cursor is not None:
            try:
                if not isinstance(cursor, str):
                    raise ValueError()
                payload, signature = cursor.split(".")
                expected = hmac.new(
                    self._secret, payload.encode(), hashlib.sha256
                ).hexdigest()
                if not hmac.compare_digest(signature, expected):
                    raise ValueError()
                data = json.loads(
                    base64.b64decode(
                        payload + "=" * (-len(payload) % 4),
                        altchars=b"-_",
                        validate=True,
                    )
                )
                offset = data["offset"]
                if (
                    data["context"] != bound
                    or type(offset) is not int
                    or offset <= 0
                    or offset % size
                    or offset >= len(items)
                ):
                    raise ValueError()
            except (ValueError, TypeError, KeyError):
                raise CursorError(
                    "INVALID_CURSOR: курсор не соответствует запросу или сессии"
                ) from None
        next_cursor = None
        if offset + size < len(items):
            payload = (
                base64.urlsafe_b64encode(
                    json.dumps({"offset": offset + size, "context": bound}).encode()
                )
                .decode()
                .rstrip("=")
            )
            next_cursor = (
                payload
                + "."
                + hmac.new(self._secret, payload.encode(), hashlib.sha256).hexdigest()
            )
        return {
            key: deepcopy(items[offset : offset + size]),
            "next_cursor": next_cursor,
        }
