from __future__ import annotations

import json
import logging
import time
import random
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from .config import Settings, env

log = logging.getLogger(__name__)


class KISAPIError(RuntimeError):
    pass


class KISNetworkError(KISAPIError):
    """Raised when the KIS host cannot be reached after retries."""
    pass


class KISClient:
    """Read-only KIS client. This class contains no order endpoint."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.app_key = env("KIS_APP_KEY")
        self.app_secret = env("KIS_APP_SECRET")
        self.base_url = env("KIS_BASE_URL", "https://openapi.koreainvestment.com:9443").rstrip("/")
        self.token_path = settings.path("storage.token_cache_path")
        self._token: str | None = None
        self._token_expiry: datetime | None = None
        self._last_request = 0.0
        # Serialize REST calls even if future jobs/threads overlap.
        self._rate_lock = threading.Lock()
        # 0.15s is intentionally more conservative than KIS' current production sample.
        self._request_interval = float(env("KIS_REQUEST_INTERVAL", "0.15"))
        self._max_retries = int(env("KIS_MAX_RETRIES", "4"))
        self._connect_timeout = float(env("KIS_CONNECT_TIMEOUT", "8"))
        self._read_timeout = float(env("KIS_READ_TIMEOUT", "20"))
        self._network_cooldown = float(env("KIS_NETWORK_COOLDOWN", "120"))
        self._network_down_until = 0.0
        self._load_token()

    @property
    def configured(self) -> bool:
        return bool(self.app_key and self.app_secret)

    def _load_token(self) -> None:
        try:
            data = json.loads(self.token_path.read_text(encoding="utf-8"))
            expiry = datetime.fromisoformat(data["expires_at"])
            if expiry > datetime.now() + timedelta(minutes=5):
                self._token = data["access_token"]
                self._token_expiry = expiry
        except Exception:
            pass

    def _save_token(self, token: str, expires_in: int) -> None:
        expiry = datetime.now() + timedelta(seconds=max(60, expires_in - 300))
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        self.token_path.write_text(json.dumps({"access_token": token, "expires_at": expiry.isoformat()}), encoding="utf-8")
        self._token, self._token_expiry = token, expiry

    def authenticate(self, force: bool = False) -> str:
        if not self.configured:
            raise KISAPIError("KIS_APP_KEY / KIS_APP_SECRET가 설정되지 않았습니다.")
        if not force and self._token and self._token_expiry and self._token_expiry > datetime.now():
            return self._token
        if time.monotonic() < self._network_down_until:
            remain = max(0, int(self._network_down_until - time.monotonic()))
            raise KISNetworkError(f"KIS network circuit open; retry after ~{remain}s")
        url = f"{self.base_url}/oauth2/tokenP"
        last_error: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                r = requests.post(
                    url,
                    headers={"content-type": "application/json"},
                    json={"grant_type": "client_credentials", "appkey": self.app_key, "appsecret": self.app_secret},
                    timeout=(self._connect_timeout, self._read_timeout),
                )
                r.raise_for_status()
                data = r.json()
                if "access_token" not in data:
                    raise KISAPIError(str(data))
                self._save_token(data["access_token"], int(data.get("expires_in", 86400)))
                self._network_down_until = 0.0
                return self._token or ""
            except requests.RequestException as exc:
                last_error = exc
                if attempt >= self._max_retries:
                    self._network_down_until = time.monotonic() + self._network_cooldown
                    raise KISNetworkError(f"KIS authentication network failure after {attempt} attempts: {exc}") from exc
                wait = min(8.0, 0.8 * (2 ** (attempt - 1))) + random.uniform(0.0, 0.25)
                log.warning("KIS auth network error attempt=%s/%s; retry in %.2fs: %s", attempt, self._max_retries, wait, exc)
                time.sleep(wait)
        raise KISNetworkError(f"KIS authentication network failure: {last_error}")

    def _pace(self) -> None:
        # Conservative process-wide pacing for this client instance.
        # The lock also protects us if jobs are made concurrent later.
        with self._rate_lock:
            elapsed = time.monotonic() - self._last_request
            if elapsed < self._request_interval:
                time.sleep(self._request_interval - elapsed)
            self._last_request = time.monotonic()

    @staticmethod
    def _response_payload(response: requests.Response) -> dict[str, Any]:
        try:
            data = response.json()
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def _response_excerpt(response: requests.Response, limit: int = 500) -> str:
        try:
            text = (response.text or "").strip().replace("\n", " ")
        except Exception:
            text = ""
        return text[:limit]

    def _get(self, path: str, tr_id: str, params: dict[str, str]) -> dict[str, Any]:
        if time.monotonic() < self._network_down_until:
            remain = max(0, int(self._network_down_until - time.monotonic()))
            raise KISNetworkError(f"KIS network circuit open; retry after ~{remain}s")
        token = self.authenticate()
        headers = {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {token}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "tr_id": tr_id,
            "custtype": "P",
        }
        url = f"{self.base_url}{path}"
        refreshed_token = False
        last_error: Exception | None = None

        for attempt in range(1, self._max_retries + 1):
            self._pace()
            try:
                r = requests.get(url, headers=headers, params=params, timeout=(self._connect_timeout, self._read_timeout))
            except requests.RequestException as exc:
                last_error = exc
                if attempt >= self._max_retries:
                    self._network_down_until = time.monotonic() + self._network_cooldown
                    raise KISNetworkError(
                        f"KIS network failure after {attempt} attempts; path={path}; tr={tr_id}; error={exc}"
                    ) from exc
                wait = min(8.0, 0.8 * (2 ** (attempt - 1))) + random.uniform(0.0, 0.25)
                log.warning(
                    "KIS network error %s tr=%s attempt=%s/%s; retry in %.2fs: %s",
                    path, tr_id, attempt, self._max_retries, wait, exc,
                )
                time.sleep(wait)
                continue

            payload = self._response_payload(r)
            msg_cd = str(payload.get("msg_cd", ""))
            msg1 = str(payload.get("msg1", ""))

            # Expired/invalid token: refresh only once, then retry.
            if r.status_code == 401 and not refreshed_token:
                token = self.authenticate(force=True)
                headers["authorization"] = f"Bearer {token}"
                refreshed_token = True
                continue

            # KIS' documented per-second transaction limit signal.
            # Their official examples wait ~61 seconds before retrying.
            if msg_cd == "EGW00201":
                if attempt >= self._max_retries:
                    raise KISAPIError(f"{msg_cd}: {msg1 or '초당 거래건수 초과'}")
                wait = float(env("KIS_RATE_LIMIT_WAIT", "61"))
                log.warning(
                    "KIS rate limit EGW00201 tr=%s attempt=%s/%s; retry in %.0fs",
                    tr_id, attempt, self._max_retries, wait,
                )
                time.sleep(wait)
                continue

            # Temporary upstream/server failures. Do not immediately discard the stock.
            if r.status_code in {429, 500, 502, 503, 504}:
                excerpt = self._response_excerpt(r)
                if attempt >= self._max_retries:
                    raise KISAPIError(
                        f"HTTP {r.status_code} after {attempt} attempts; "
                        f"tr={tr_id}; body={excerpt or '<empty>'}"
                    )
                wait = min(8.0, 0.8 * (2 ** (attempt - 1))) + random.uniform(0.0, 0.25)
                log.warning(
                    "KIS temporary HTTP %s tr=%s attempt=%s/%s; retry in %.2fs; body=%s",
                    r.status_code, tr_id, attempt, self._max_retries, wait, excerpt or "<empty>",
                )
                time.sleep(wait)
                continue

            try:
                r.raise_for_status()
            except requests.HTTPError as exc:
                excerpt = self._response_excerpt(r)
                raise KISAPIError(
                    f"HTTP {r.status_code}; tr={tr_id}; body={excerpt or '<empty>'}"
                ) from exc

            if str(payload.get("rt_cd", "0")) != "0":
                raise KISAPIError(f"{msg_cd}: {msg1}")
            self._network_down_until = 0.0
            return payload

        self._network_down_until = time.monotonic() + self._network_cooldown
        raise KISNetworkError(f"KIS request failed after retries: {last_error}")

    @staticmethod
    def _num(v: Any) -> float:
        try:
            return float(str(v).replace(",", ""))
        except Exception:
            return 0.0

    def current_price(self, code: str) -> dict[str, Any]:
        data = self._get(
            "/uapi/domestic-stock/v1/quotations/inquire-price",
            "FHKST01010100",
            {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code},
        ).get("output", {})
        return {
            "code": code,
            "name": data.get("hts_kor_isnm", ""),
            "price": self._num(data.get("stck_prpr")),
            "open": self._num(data.get("stck_oprc")),
            "high": self._num(data.get("stck_hgpr")),
            "low": self._num(data.get("stck_lwpr")),
            "change_pct": self._num(data.get("prdy_ctrt")),
            "volume": self._num(data.get("acml_vol")),
            "turnover_krw": self._num(data.get("acml_tr_pbmn")),
            "prev_volume_ratio": self._num(data.get("prdy_vol_vrss_acml_vol_rate")),
            "raw": data,
        }

    def volume_rank(self, market_code: str = "0000", by_turnover: bool = True) -> list[dict[str, Any]]:
        data = self._get(
            "/uapi/domestic-stock/v1/quotations/volume-rank",
            "FHPST01710000",
            {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_COND_SCR_DIV_CODE": "20171",
                "FID_INPUT_ISCD": market_code,
                "FID_DIV_CLS_CODE": "0",
                "FID_BLNG_CLS_CODE": "3" if by_turnover else "0",
                "FID_TRGT_CLS_CODE": "0",
                "FID_TRGT_EXLS_CLS_CODE": "0",
                "FID_INPUT_PRICE_1": "",
                "FID_INPUT_PRICE_2": "",
                "FID_VOL_CNT": "",
                "FID_INPUT_DATE_1": "",
            },
        ).get("output", [])
        return list(data or [])

    def fluctuation_rank(self, market_code: str = "0000") -> list[dict[str, Any]]:
        data = self._get(
            "/uapi/domestic-stock/v1/ranking/fluctuation",
            "FHPST01700000",
            {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_COND_SCR_DIV_CODE": "20170",
                "FID_INPUT_ISCD": market_code,
                "FID_RANK_SORT_CLS_CODE": "0",
                "FID_INPUT_CNT_1": "0",
                "FID_PRC_CLS_CODE": "0",
                "FID_INPUT_PRICE_1": "",
                "FID_INPUT_PRICE_2": "",
                "FID_VOL_CNT": "",
                "FID_TRGT_CLS_CODE": "0",
                "FID_TRGT_EXLS_CLS_CODE": "0",
                "FID_DIV_CLS_CODE": "0",
                "FID_RSFL_RATE1": "",
                "FID_RSFL_RATE2": "",
            },
        ).get("output", [])
        return list(data or [])

    def foreign_institution_rank(self, market_code: str = "0000", party: str = "0") -> list[dict[str, Any]]:
        data = self._get(
            "/uapi/domestic-stock/v1/quotations/foreign-institution-total",
            "FHPTJ04400000",
            {
                "FID_COND_MRKT_DIV_CODE": "V",
                "FID_COND_SCR_DIV_CODE": "16449",
                "FID_INPUT_ISCD": market_code,
                "FID_DIV_CLS_CODE": "1",
                "FID_RANK_SORT_CLS_CODE": "0",
                "FID_ETC_CLS_CODE": party,
            },
        ).get("output", [])
        return list(data or [])

    def daily_chart(self, code: str, days: int = 45) -> pd.DataFrame:
        end = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=max(90, days * 2))).strftime("%Y%m%d")
        body = self._get(
            "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
            "FHKST03010100",
            {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": code,
                "FID_INPUT_DATE_1": start,
                "FID_INPUT_DATE_2": end,
                "FID_PERIOD_DIV_CODE": "D",
                "FID_ORG_ADJ_PRC": "0",
            },
        )
        rows = body.get("output2", []) or []
        df = pd.DataFrame(rows)
        if df.empty:
            return df
        rename = {
            "stck_bsop_date": "date", "stck_oprc": "open", "stck_hgpr": "high",
            "stck_lwpr": "low", "stck_clpr": "close", "acml_vol": "volume", "acml_tr_pbmn": "turnover",
        }
        df = df.rename(columns=rename)
        for c in ["open", "high", "low", "close", "volume", "turnover"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        if "date" in df.columns:
            df = df.sort_values("date").tail(days).reset_index(drop=True)
        return df

    def intraday_chart(self, code: str, hour: str | None = None) -> pd.DataFrame:
        hour = hour or datetime.now().strftime("%H%M%S")
        body = self._get(
            "/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice",
            "FHKST03010200",
            {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": code,
                "FID_INPUT_HOUR_1": hour,
                "FID_PW_DATA_INCU_YN": "Y",
                "FID_ETC_CLS_CODE": "",
            },
        )
        rows = body.get("output2", []) or []
        df = pd.DataFrame(rows)
        if df.empty:
            return df
        rename = {
            "stck_cntg_hour": "time", "stck_prpr": "close", "stck_oprc": "open",
            "stck_hgpr": "high", "stck_lwpr": "low", "cntg_vol": "volume", "acml_tr_pbmn": "cum_turnover",
        }
        df = df.rename(columns=rename)
        for c in ["open", "high", "low", "close", "volume", "cum_turnover"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        if "time" in df.columns:
            df = df.sort_values("time").drop_duplicates("time").reset_index(drop=True)
        return df

    def historical_intraday_window(self, code: str, trade_date: str, hour: str) -> pd.DataFrame:
        body = self._get(
            "/uapi/domestic-stock/v1/quotations/inquire-time-dailychartprice",
            "FHKST03010230",
            {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": code,
                "FID_INPUT_HOUR_1": hour,
                "FID_INPUT_DATE_1": trade_date.replace("-", ""),
                "FID_PW_DATA_INCU_YN": "N",
                "FID_FAKE_TICK_INCU_YN": "",
            },
        )
        rows = body.get("output2", []) or []
        df = pd.DataFrame(rows)
        if df.empty:
            return df
        rename = {
            "stck_cntg_hour": "time", "stck_prpr": "close", "stck_oprc": "open",
            "stck_hgpr": "high", "stck_lwpr": "low", "cntg_vol": "volume", "acml_tr_pbmn": "cum_turnover",
        }
        df = df.rename(columns=rename)
        for c in ["open", "high", "low", "close", "volume", "cum_turnover"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        if "time" in df.columns:
            df = df.sort_values("time").drop_duplicates("time").reset_index(drop=True)
        return df

    def full_day_intraday(self, code: str, trade_date: str | None = None) -> pd.DataFrame:
        # Official KIS historical minute endpoint returns up to 120 rows/call.
        # Four overlapping windows safely cover a normal KRX session.
        trade_date = trade_date or datetime.now().date().isoformat()
        frames = []
        for h in ["103000", "123000", "143000", "153000"]:
            try:
                d = self.historical_intraday_window(code, trade_date, h)
                if not d.empty:
                    frames.append(d)
            except Exception as exc:
                log.warning("historical intraday window failed %s %s: %s", code, h, exc)
        if not frames:
            return pd.DataFrame()
        df = pd.concat(frames, ignore_index=True)
        if "time" in df.columns:
            df = df.drop_duplicates("time").sort_values("time").reset_index(drop=True)
        return df

    # -------------------------
    # Overseas stocks (read-only)
    # -------------------------
    def overseas_current_price(self, symbol: str, exchange: str = "NAS") -> dict[str, Any]:
        data = self._get(
            "/uapi/overseas-price/v1/quotations/price",
            "HHDFS00000300",
            {"AUTH": "", "EXCD": exchange, "SYMB": symbol},
        ).get("output", {}) or {}
        price = self._num(data.get("last") or data.get("clos"))
        return {
            "code": symbol,
            "exchange": exchange,
            "name": data.get("name") or data.get("ename") or symbol,
            "price": price,
            "open": self._num(data.get("open")),
            "high": self._num(data.get("high")),
            "low": self._num(data.get("low")),
            "change_pct": self._num(data.get("rate")),
            "volume": self._num(data.get("tvol")),
            "turnover_usd": self._num(data.get("tamt")),
            "raw": data,
        }

    def overseas_turnover_rank(self, exchange: str = "NAS", nday: str = "0", vol_rang: str = "0") -> list[dict[str, Any]]:
        body = self._get(
            "/uapi/overseas-stock/v1/ranking/trade-pbmn",
            "HHDFS76320010",
            {"EXCD": exchange, "NDAY": nday, "VOL_RANG": vol_rang, "AUTH": "", "KEYB": "", "PRC1": "", "PRC2": ""},
        )
        rows = body.get("output2", []) or []
        out: list[dict[str, Any]] = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            d = dict(r)
            for k in ["last", "rate", "tvol", "tamt", "a_tamt"]:
                if k in d:
                    d[k] = self._num(d[k])
            out.append(d)
        return out

    def overseas_daily_chart(self, symbol: str, exchange: str = "NAS", days: int = 45) -> pd.DataFrame:
        body = self._get(
            "/uapi/overseas-price/v1/quotations/dailyprice",
            "HHDFS76240000",
            {"AUTH": "", "EXCD": exchange, "SYMB": symbol, "GUBN": "0", "BYMD": "", "MODP": "1"},
        )
        rows = body.get("output2", []) or []
        df = pd.DataFrame(rows)
        if df.empty:
            return df
        rename = {"xymd": "date", "open": "open", "high": "high", "low": "low", "clos": "close", "tvol": "volume", "tamt": "turnover"}
        df = df.rename(columns=rename)
        for c in ["open", "high", "low", "close", "volume", "turnover"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        if "date" in df.columns:
            df = df.sort_values("date").tail(days).reset_index(drop=True)
        return df

    def overseas_intraday_chart(
        self,
        symbol: str,
        exchange: str = "NAS",
        interval_minutes: int = 5,
        include_previous: bool = False,
        nrec: int = 120,
    ) -> pd.DataFrame:
        body = self._get(
            "/uapi/overseas-price/v1/quotations/inquire-time-itemchartprice",
            "HHDFS76950200",
            {
                "AUTH": "", "EXCD": exchange, "SYMB": symbol,
                "NMIN": str(max(1, interval_minutes)), "PINC": "1" if include_previous else "0",
                "NEXT": "", "NREC": str(min(120, max(1, nrec))), "FILL": "", "KEYB": "",
            },
        )
        rows = body.get("output2", []) or []
        df = pd.DataFrame(rows)
        if df.empty:
            return df
        # KIS overseas minute fields differ slightly by market/session; parse defensively.
        aliases = {
            "time": ["xhms", "khms", "time"],
            "date": ["xymd", "kymd", "date"],
            "open": ["open"], "high": ["high"], "low": ["low"],
            "close": ["last", "clos", "close"],
            "volume": ["evol", "tvol", "volume"],
        }
        out = pd.DataFrame(index=df.index)
        for target, keys in aliases.items():
            for key in keys:
                if key in df.columns:
                    out[target] = df[key]
                    break
        for c in ["open", "high", "low", "close", "volume"]:
            if c in out.columns:
                out[c] = pd.to_numeric(out[c], errors="coerce")
        if "close" not in out.columns:
            return pd.DataFrame()
        for c in ["open", "high", "low"]:
            if c not in out.columns:
                out[c] = out["close"]
        if "volume" not in out.columns:
            out["volume"] = 0.0
        if "time" in out.columns:
            out["time"] = out["time"].astype(str).str.replace(":", "", regex=False).str.zfill(6)
            out = out.sort_values("time").drop_duplicates("time")
        return out.reset_index(drop=True)
