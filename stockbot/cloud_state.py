from __future__ import annotations

import io
import json
import logging
import mimetypes
import os
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests
import sqlite3

from .config import Settings, env

log = logging.getLogger(__name__)


class SupabaseStorage:
    """Tiny Supabase Storage client for stateless GitHub Actions runners.

    The private bucket stores a zipped copy of local SQLite/model state.
    The public bucket only stores sanitized dashboard JSON for the PWA.
    Supports the 2026 sb_secret_* keys and legacy service_role JWT keys.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self.url = env("SUPABASE_URL").rstrip("/")
        self.key = env("SUPABASE_SECRET_KEY") or env("SUPABASE_SERVICE_ROLE_KEY")
        self.state_bucket = env("SUPABASE_STATE_BUCKET", "krx-ai-state")
        self.public_bucket = env("SUPABASE_PUBLIC_BUCKET", "krx-ai-public")
        self.state_object = env("SUPABASE_STATE_OBJECT", "state/state_bundle.zip")
        self.timeout = float(settings.get("cloud.http_timeout_seconds", 30))
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "KRX-AI-Trading-Assistant/7.0"})

    @property
    def configured(self) -> bool:
        return bool(self.url and self.key)

    def _headers(self, content_type: str | None = None) -> dict[str, str]:
        h = {"apikey": self.key}
        # Legacy service_role keys are JWTs; keep Bearer for compatibility.
        if self.key and not self.key.startswith("sb_secret_"):
            h["Authorization"] = f"Bearer {self.key}"
        if content_type:
            h["Content-Type"] = content_type
        return h

    def _object_url(self, bucket: str, path: str, public: bool = False) -> str:
        safe = "/".join(quote(x, safe="") for x in path.strip("/").split("/"))
        prefix = "object/public" if public else "object"
        return f"{self.url}/storage/v1/{prefix}/{quote(bucket, safe='')}/{safe}"

    def ensure_bucket(self, bucket: str, public: bool) -> None:
        if not self.configured:
            raise RuntimeError("SUPABASE_URL / SUPABASE_SECRET_KEY not configured")
        r = self.session.get(
            f"{self.url}/storage/v1/bucket/{quote(bucket, safe='')}",
            headers=self._headers(), timeout=self.timeout,
        )
        if r.status_code == 200:
            body = r.json() if r.content else {}
            # If it already exists with a different public setting, leave it alone
            # and surface a useful log message instead of mutating unexpectedly.
            if bool(body.get("public", False)) != bool(public):
                log.warning("Supabase bucket %s exists but public=%s (expected %s)", bucket, body.get("public"), public)
            return
        if r.status_code != 404:
            r.raise_for_status()
        payload = {"id": bucket, "name": bucket, "public": bool(public), "file_size_limit": 52428800}
        r = self.session.post(
            f"{self.url}/storage/v1/bucket", headers=self._headers("application/json"),
            data=json.dumps(payload), timeout=self.timeout,
        )
        if r.status_code not in {200, 201}:
            r.raise_for_status()
        log.info("Created Supabase bucket %s public=%s", bucket, public)

    def ensure_buckets(self) -> None:
        self.ensure_bucket(self.state_bucket, public=False)
        self.ensure_bucket(self.public_bucket, public=True)

    def download(self, bucket: str, path: str) -> bytes | None:
        r = self.session.get(self._object_url(bucket, path), headers=self._headers(), timeout=self.timeout)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.content

    def upload(self, bucket: str, path: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        headers = self._headers(content_type)
        headers["x-upsert"] = "true"
        r = self.session.post(
            self._object_url(bucket, path), headers=headers, data=data, timeout=self.timeout,
        )
        if r.status_code not in {200, 201}:
            # Some deployments prefer PUT for replacement; retry once.
            r = self.session.put(
                self._object_url(bucket, path), headers=headers, data=data, timeout=self.timeout,
            )
        r.raise_for_status()

    def upload_json(self, path: str, obj: Any, public: bool = True) -> None:
        raw = json.dumps(obj, ensure_ascii=False, default=str, separators=(",", ":")).encode("utf-8")
        self.upload(self.public_bucket if public else self.state_bucket, path, raw, "application/json; charset=utf-8")

    def public_url(self, path: str) -> str:
        return self._object_url(self.public_bucket, path, public=True)


class CloudStateManager:
    """Restore/persist the local state needed by the existing v6 engine."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.storage = SupabaseStorage(settings)
        self.root = settings.root
        self.state_paths = [
            Path("data/stockbot.sqlite3"),
            Path("data/us_stockbot.sqlite3"),
            Path("data/kis_token.json"),
            Path("data/dart_corp_codes.json"),
            Path("data/models"),
            Path("data/us_models"),
        ]

    @property
    def configured(self) -> bool:
        return self.storage.configured

    def init_remote(self) -> dict[str, str]:
        self.storage.ensure_buckets()
        return {
            "state_bucket": self.storage.state_bucket,
            "public_bucket": self.storage.public_bucket,
            "dashboard_url": self.storage.public_url("dashboard.json"),
        }

    def restore(self) -> bool:
        if not self.configured:
            raise RuntimeError("Supabase is not configured")
        self.storage.ensure_buckets()
        raw = self.storage.download(self.storage.state_bucket, self.storage.state_object)
        if not raw:
            log.info("No cloud state bundle yet; starting fresh")
            return False
        with zipfile.ZipFile(io.BytesIO(raw), "r") as zf:
            root = self.root.resolve()
            for member in zf.infolist():
                target = (root / member.filename).resolve()
                if root not in target.parents and target != root:
                    raise RuntimeError(f"Unsafe path in cloud state: {member.filename}")
            zf.extractall(root)
        log.info("Restored cloud state bundle (%d bytes)", len(raw))
        return True

    def _checkpoint_databases(self) -> None:
        for rel in (Path("data/stockbot.sqlite3"), Path("data/us_stockbot.sqlite3")):
            p = self.root / rel
            if not p.exists():
                continue
            try:
                with sqlite3.connect(p) as con:
                    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except Exception as exc:
                log.warning("SQLite checkpoint failed %s: %s", rel, exc)

    def _bundle_bytes(self) -> bytes:
        self._checkpoint_databases()
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for rel in self.state_paths:
                p = self.root / rel
                if not p.exists():
                    continue
                if p.is_dir():
                    for child in p.rglob("*"):
                        if child.is_file() and "__pycache__" not in child.parts:
                            zf.write(child, child.relative_to(self.root).as_posix())
                else:
                    zf.write(p, rel.as_posix())
        return out.getvalue()

    def backup(self) -> int:
        raw = self._bundle_bytes()
        self.storage.upload(self.storage.state_bucket, self.storage.state_object, raw, "application/zip")
        log.info("Uploaded cloud state bundle (%d bytes)", len(raw))
        return len(raw)
