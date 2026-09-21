from __future__ import annotations

import logging
import mimetypes
from pathlib import Path

from .cloud_state import CloudStateManager
from .config import load_settings

log = logging.getLogger(__name__)


def deploy_pwa() -> dict[str, str]:
    settings = load_settings()
    cloud = CloudStateManager(settings)
    info = cloud.init_remote()
    web = settings.root / "web"
    base = cloud.storage.public_url("").rstrip("/")

    for p in sorted(web.rglob("*")):
        if not p.is_file() or p.name == "runtime-config.js":
            continue
        rel = p.relative_to(web).as_posix()
        ctype = mimetypes.guess_type(str(p))[0] or "application/octet-stream"
        if p.suffix == ".webmanifest":
            ctype = "application/manifest+json"
        cloud.storage.upload(cloud.storage.public_bucket, f"app/{rel}", p.read_bytes(), ctype)

    runtime = (
        "window.KRX_CLOUD_MODE=true;\n"
        f"window.KRX_DATA_BASE={base!r};\n"
    ).encode("utf-8")
    cloud.storage.upload(cloud.storage.public_bucket, "app/runtime-config.js", runtime, "application/javascript; charset=utf-8")
    app_url = cloud.storage.public_url("app/index.html")
    log.info("PWA deployed: %s", app_url)
    return {**info, "app_url": app_url, "data_base": base}


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    info = deploy_pwa()
    for k, v in info.items():
        print(f"{k}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
