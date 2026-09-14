from __future__ import annotations

import io
import json
import logging
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET

import requests

from .config import Settings, env

log = logging.getLogger(__name__)


class DartClient:
    def __init__(self, settings: Settings):
        self.key = env("DART_API_KEY")
        self.cache_path = settings.path("storage.dart_corp_cache_path")
        self._map: dict[str, str] | None = None

    @property
    def configured(self) -> bool:
        return bool(self.key)

    def _load_map(self) -> dict[str, str]:
        if self._map is not None:
            return self._map
        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
            if data.get("saved_at", "")[:10] == datetime.now().date().isoformat():
                self._map = data["stock_to_corp"]
                return self._map
        except Exception:
            pass
        if not self.key:
            return {}
        r = requests.get("https://opendart.fss.or.kr/api/corpCode.xml", params={"crtfc_key": self.key}, timeout=20)
        r.raise_for_status()
        z = zipfile.ZipFile(io.BytesIO(r.content))
        root = ET.fromstring(z.read(z.namelist()[0]))
        mapping: dict[str, str] = {}
        for node in root.findall("list"):
            stock = (node.findtext("stock_code") or "").strip()
            corp = (node.findtext("corp_code") or "").strip()
            if stock and corp:
                mapping[stock] = corp
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps({"saved_at": datetime.now().isoformat(), "stock_to_corp": mapping}), encoding="utf-8")
        self._map = mapping
        return mapping

    def recent_disclosures(self, stock_code: str, days: int = 7, limit: int = 20) -> list[dict[str, Any]]:
        if not self.key:
            return []
        corp_code = self._load_map().get(stock_code)
        if not corp_code:
            return []
        end = datetime.now().strftime("%Y%m%d")
        begin = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
        r = requests.get(
            "https://opendart.fss.or.kr/api/list.json",
            params={
                "crtfc_key": self.key,
                "corp_code": corp_code,
                "bgn_de": begin,
                "end_de": end,
                "last_reprt_at": "Y",
                "page_no": "1",
                "page_count": str(min(limit, 100)),
            },
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        if data.get("status") not in ("000", None):
            if data.get("status") == "013":
                return []
            log.warning("DART %s: %s", data.get("status"), data.get("message"))
            return []
        return data.get("list", []) or []
