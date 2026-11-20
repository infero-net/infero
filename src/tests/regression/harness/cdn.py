"""Offline copies of the third-party scripts the page loads.

The page pulls `marked` (unpinned), `html2canvas` and, for identities, `@scure/bip39` / `@scure/bip32`
from esm.sh. Tests must not depend on the network or on whatever `marked` version the CDN serves
today, so every request to another host is routed here:

  * a URL in cdn_cache/ is answered from disk;
  * a cache miss on an allowed CDN host is fetched once (record mode) and stored, unless
    REGRESSION_OFFLINE=1, where it fails;
  * any other host is aborted (hub, relay, real LLM providers never get a request).

Delete cdn_cache/ (or a single entry from index.json) to re-record.
"""
import hashlib
import json
import os
import re
import threading

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cdn_cache")
INDEX = os.path.join(CACHE_DIR, "index.json")
RECORD_HOSTS = ("cdn.jsdelivr.net", "cdn.bootcdn.net", "esm.sh")
# Everything that is not the local test server
EXTERNAL = re.compile(r"^(?!https?://127\.0\.0\.1[:/]).*")

_lock = threading.Lock()


def _load_index():
    try:
        with open(INDEX) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_index(idx):
    os.makedirs(CACHE_DIR, exist_ok=True)
    tmp = INDEX + ".tmp"
    with open(tmp, "w") as f:
        json.dump(idx, f, indent=1, sort_keys=True)
    os.replace(tmp, INDEX)


class CdnCache:
    def __init__(self):
        self.index = _load_index()
        self.offline = os.environ.get("REGRESSION_OFFLINE") == "1"
        self.blocked = []   # external URLs the page asked for that were refused

    def install(self, context):
        context.route(EXTERNAL, self._handle)

    def _handle(self, route):
        url = route.request.url
        host = re.sub(r"^https?://([^/:]+).*$", r"\1", url)
        entry = self.index.get(url)
        if entry:
            path = os.path.join(CACHE_DIR, entry["file"])
            with open(path, "rb") as f:
                body = f.read()
            headers = {"content-type": entry["content_type"], "access-control-allow-origin": "*"}
            if entry.get("location"):
                headers["location"] = entry["location"]
            return route.fulfill(status=entry["status"], headers=headers, body=body)
        if host not in RECORD_HOSTS or self.offline:
            self.blocked.append(url)
            return route.abort()
        try:
            resp = route.fetch(max_redirects=0)
        except Exception:
            self.blocked.append(url)
            return route.abort()
        body = resp.body()
        ctype = resp.headers.get("content-type", "application/octet-stream")
        status = resp.status
        loc = resp.headers.get("location")
        if status < 400:
            name = hashlib.sha1(url.encode()).hexdigest()[:16]
            with _lock:
                os.makedirs(CACHE_DIR, exist_ok=True)
                with open(os.path.join(CACHE_DIR, name), "wb") as f:
                    f.write(body)
                self.index[url] = {"file": name, "status": status, "content_type": ctype, "location": loc}
                merged = _load_index()
                merged.update(self.index)
                _save_index(merged)
        headers = {"content-type": ctype, "access-control-allow-origin": "*"}
        if loc:
            headers["location"] = loc
        route.fulfill(status=status, headers=headers, body=body)
