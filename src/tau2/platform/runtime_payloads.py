"""Late binding of benchmark-owned text. Never execute or log stored contents.

This module is a delivery mechanism, not a sandbox or a content-safety verdict.
The developer workspace must not mount the private store or raw run logs.
"""

import hashlib
import json
import os
import re
import stat
import tempfile
from pathlib import Path

PREFIX = "{{bank_payload:"
TOKEN = re.compile(r"\{\{bank_payload:([a-f0-9]{64})\}\}")
MAX_BYTES = 1024 * 1024


class PayloadError(ValueError):
    """Errors deliberately contain no paths, payload IDs or underlying text."""


def private_root():
    return Path.home() / ".local/share/banking-ru-private"


def reference(payload_id):
    if not re.fullmatch(r"[a-f0-9]{64}", payload_id):
        raise PayloadError("PAYLOAD_INVALID_ID")
    return PREFIX + payload_id + "}}"


class PayloadStore:
    def __init__(self, root):
        self.root = Path(root).expanduser().absolute()

    @classmethod
    def from_environment(cls):
        # Opt-in on the benchmark host. No discovery of developer files.
        value = os.environ.get("BANKING_PAYLOAD_STORE")
        return cls(value) if value else None

    def put(self, text):
        """Authoring worker only. Return a content-addressed reference."""
        data = text.encode("utf-8")
        if not data or len(data) > MAX_BYTES:
            raise PayloadError("PAYLOAD_INVALID_SIZE")
        if PREFIX in text:
            raise PayloadError("PAYLOAD_RESERVED_TOKEN")
        payload_id = hashlib.sha256(data).hexdigest()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        target = self.root / payload_id
        fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=self.root)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, target)
            except FileExistsError:
                if self.get(payload_id) != text:
                    raise PayloadError("PAYLOAD_INTEGRITY") from None
            directory = os.open(self.root, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return reference(payload_id)

    def get(self, payload_id):
        reference(payload_id)
        try:
            fd = os.open(
                self.root / payload_id, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            )
            with os.fdopen(fd, "rb") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise PayloadError("PAYLOAD_NOT_REGULAR")
                data = stream.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES or hashlib.sha256(data).hexdigest() != payload_id:
                raise PayloadError("PAYLOAD_INTEGRITY")
            text = data.decode("utf-8")
            if PREFIX in text:
                raise PayloadError("PAYLOAD_RESERVED_TOKEN")
            return text
        except (OSError, UnicodeError):
            raise PayloadError("PAYLOAD_UNAVAILABLE") from None

    def text(self, value):
        if PREFIX not in value:
            return value
        if PREFIX in TOKEN.sub("", value):
            raise PayloadError("PAYLOAD_INVALID_REFERENCE")
        # Deliberately one pass: payload text cannot load another resource.
        return TOKEN.sub(lambda match: self.get(match[1]), value)

    def value(self, value):
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, list):
            return [self.value(item) for item in value]
        if isinstance(value, dict):
            if any(isinstance(key, str) and PREFIX in key for key in value):
                raise PayloadError("PAYLOAD_REFERENCE_IN_KEY")
            return {key: self.value(item) for key, item in value.items()}
        return value

    def content(self, value):
        """Expand string values inside JSON without corrupting JSON quoting."""
        if not value or PREFIX not in value:
            return value
        try:
            decoded = json.loads(value)
        except ValueError:
            return self.text(value)
        return json.dumps(self.value(decoded), ensure_ascii=False)


def materialize_content(content, store):
    if not content or PREFIX not in content:
        return content
    if store is None:
        raise PayloadError("PAYLOAD_STORE_NOT_CONFIGURED")
    return store.content(content)


def materialize_bytes(raw, *, json_document=False):
    """Runtime input hydration before parsing or hashing, preserving source bytes.

    References in JSON files are inside string literals; escape replacement text
    for that context. Plain corpus text is inserted verbatim. Nothing is executed.
    """
    if PREFIX.encode() not in raw:
        return raw
    store = PayloadStore.from_environment()
    if store is None:
        raise PayloadError("PAYLOAD_STORE_NOT_CONFIGURED")
    text = raw.decode("utf-8")
    if PREFIX in TOKEN.sub("", text):
        raise PayloadError("PAYLOAD_INVALID_REFERENCE")

    def replace(match):
        value = store.get(match[1])
        return json.dumps(value, ensure_ascii=False)[1:-1] if json_document else value

    return TOKEN.sub(replace, text).encode("utf-8")


def materialize_value(value, store=None):
    """Explicit benchmark-runtime mode only; never auto-discover the store."""
    store = store or PayloadStore.from_environment()
    if PREFIX not in json.dumps(value, ensure_ascii=False):
        return value
    if store is None:
        raise PayloadError("PAYLOAD_STORE_NOT_CONFIGURED")
    return store.value(value)
