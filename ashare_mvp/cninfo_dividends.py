"""Public issuer dividend histories including explicit cash-payment dates."""
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import urlencode
import base64
import hashlib
import json
import time

import pandas as pd

from .public_data import is_ashare


def get_cninfo_history(symbol):
    # Public timestamp encoding used by the website client (AKShare cninfo.js).
    # No account secret or user credentials participate in this protocol.
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives import padding
    key = b"1234567887654321"
    padder = padding.PKCS7(128).padder()
    padded = padder.update(str(int(time.time())).encode()) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.CBC(key)).encryptor()
    encoded = base64.b64encode(encryptor.update(padded) + encryptor.finalize()).decode()
    if not is_ashare(symbol):
        raise ValueError("supported A-share symbol required")
    request = Request("https://webapi.cninfo.com.cn/api/sysapi/p_sysapi1139?" + urlencode(dict(scode=symbol.split(".")[1])),
                      data=b"", headers={"Accept-Enckey": encoded, "User-Agent": "Mozilla/5.0",
                                         "Referer": "https://webapi.cninfo.com.cn/"})
    with urlopen(request, timeout=20) as response:
        return json.load(response)


def download_cninfo_dividends(out, symbols, resume=False, fetch=get_cninfo_history, delay=0.2):
    symbols = sorted(set(symbols))
    if not symbols or any(not is_ashare(code) for code in symbols) or delay < 0:
        raise ValueError("explicit supported A-share symbols and valid delay required")
    path = Path(out)
    if resume:
        manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
        if manifest.get("schema") != "cninfo_dividend_history_v1" or manifest.get("symbols") != symbols:
            raise ValueError("CNInfo dividend resume request changed")
    else:
        if path.exists() and any(path.iterdir()):
            raise ValueError("CNInfo dividend output must be empty unless resuming")
        path.mkdir(parents=True, exist_ok=True)
        manifest = dict(schema="cninfo_dividend_history_v1", source="CNInfo p_sysapi1139", symbols=symbols,
                        certified=False, model_ready=False, synthetic=False, securities={},
                        limitations=["latest issuer history, original vintages not certified",
                                     "response identity relies on sealed security request; historical renames require reconciliation",
                                     "empty response not independent evidence of absence; terminal settlements not supplied"])

    def checkpoint():
        manifest["saved_securities"] = sum(item.get("status") == "ok" for item in manifest["securities"].values())
        manifest["rows"] = sum(item.get("rows", 0) for item in manifest["securities"].values() if item.get("status") == "ok")
        manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
        temporary = path / "manifest.pending.json"
        temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path / "manifest.json")

    manifest["status"] = "running"
    checkpoint()
    failures = 0
    for index, symbol in enumerate(symbols):
        prior = manifest["securities"].get(symbol)
        if prior and prior.get("status") == "ok":
            for name, digest in prior["hashes"].items():
                if Path(name).name != name or hashlib.sha256((path / name).read_bytes()).hexdigest() != digest:
                    raise ValueError("CNInfo dividend cache hash mismatch")
            continue
        try:
            payload = fetch(symbol)
            records = payload.get("records")
            if payload.get("resultcode") != 200 or not isinstance(records, list):
                raise ValueError("CNInfo dividend provider failed")
            if int(payload["count"]) != len(records) or int(payload["total"]) != len(records):
                raise ValueError("CNInfo history truncated or pagination required")
            frame = pd.DataFrame(records)
            fields = {"F006D", "F020D", "F023D", "F025D", "F010N", "F011N", "F012N", "F007V", "F001V", "F044V"}
            if records and not fields.issubset(frame):
                raise ValueError("CNInfo dividend date/term schema incomplete")
            if frame.empty:
                frame = pd.DataFrame(columns=sorted(fields))
            hashes = {}
            for suffix, content in [("json", json.dumps(payload, ensure_ascii=False).encode("utf-8")),
                                    ("csv", frame.to_csv(index=False).encode("utf-8"))]:
                file = path / f"{symbol}.{suffix}"
                temporary = file.with_suffix(f".pending.{suffix}")
                temporary.write_bytes(content)
                temporary.replace(file)
                hashes[file.name] = hashlib.sha256(content).hexdigest()
            manifest["securities"][symbol] = dict(status="ok", rows=len(frame), hashes=hashes,
                                                 retrieved_at=datetime.now(timezone.utc).isoformat())
            failures = 0
        except Exception as exc:
            manifest["securities"][symbol] = dict(status="failed", error=str(exc))
            failures += 1
        checkpoint()
        if index % 20 == 0:
            print(f"CNInfo dividends: {manifest['saved_securities']}/{len(symbols)} securities", flush=True)
        if failures >= 3:
            break
        time.sleep(delay)
    manifest["status"] = "downloaded_raw" if manifest["saved_securities"] == len(symbols) else "partial"
    checkpoint()
    return manifest
