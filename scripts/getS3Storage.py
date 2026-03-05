#!/usr/bin/env python3
"""
S3 Storage Monitor — Zabbix external check with LLD support.
Passwords and group IDs are supplied via Zabbix macros.
Author: Pau Santana - 2026-02-27
"""
import json
import re
import sys
import time
from typing import Optional, Tuple
try:
    import requests
    import urllib3
    from bs4 import BeautifulSoup
except ImportError:
    print(json.dumps({
        "timestamp": int(time.time()),
        "status": "error",
        "error": "Missing dependencies. Install requests and beautifulsoup4",
    }, indent=2))
    sys.exit(127)

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------
BASE_URL = "https://cmct1.globalconnect.cloud:8443" # No trailing slash
GMT_OFFSET = 2
VERIFY_SSL = True
KNOWN_USERS = [
    "operations-user",
    "nrk-user"
    # Add real usernames here as needed
]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
TIB_TO_TB = 1.099511627776
TIMEOUT = 45
ALLOWED_METHODS = {"GET", "POST", "HEAD", "OPTIONS"}
FORBIDDEN_METHODS = {"PUT", "DELETE", "PATCH", "TRACE", "CONNECT"}
SIZE_UNITS = {
    "B": 1,
    "KB": 1000, "KIB": 1024,
    "MB": 1000 ** 2, "MIB": 1024 ** 2,
    "GB": 1000 ** 3, "GIB": 1024 ** 3,
    "TB": 1000 ** 4, "TIB": 1024 ** 4,
    "PB": 1000 ** 5, "PIB": 1024 ** 5,
}
SIZE_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(B|KB|MB|GB|TB|PB|KiB|MiB|GiB|TiB|PiB)",
    re.IGNORECASE,
)
QUOTA_PATTERNS = [
    r"(?:Quota|Limit|Capacity|Storage Quota):\s*([\d\.]+)\s*(TB|TIB|GB|GIB|MB|MIB)",
    r"Available:\s*([\d\.]+)\s*(TB|TIB|GB|GIB|MB|MIB)",
    r"Remaining:\s*([\d\.]+)\s*(TB|TIB|GB|GIB|MB|MIB)",
    r"Used\s*/\s*(?:Quota|Limit):\s*[\d\.]+\s*/\s*([\d\.]+)\s*(TB|TIB|GB|GIB)",
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def fatal(message, code=1):
    print(json.dumps({
        "timestamp": int(time.time()),
        "status": "error",
        "error": message,
    }, indent=2))
    sys.exit(code)

def check_method(method):
    m = method.upper()
    if m in FORBIDDEN_METHODS:
        fatal(f"Forbidden HTTP method '{m}'")
    if m not in ALLOWED_METHODS:
        fatal(f"Unrecognised HTTP method '{m}'")

def parse_size_to_bytes(s):
    s = (s or "").strip().replace(",", "")
    match = SIZE_RE.search(s)
    if match:
        mult = SIZE_UNITS.get(match.group(2).upper())
        if mult:
            return int(round(float(match.group(1)) * mult))
    try:
        return int(float(s))
    except ValueError:
        return 0

def bytes_to_tb(b):
    return (b / (1024 ** 4)) * TIB_TO_TB

def gib_to_tb(gib: float) -> float:
    return (gib * 1024 ** 3) / (1024 ** 4) * TIB_TO_TB

def parse_quota_from_text(text: str) -> Optional[float]:
    for pattern in QUOTA_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            val = float(match.group(1))
            unit = match.group(2).upper()
            if "TB" in unit or "TIB" in unit:
                return val
            elif "GB" in unit or "GIB" in unit:
                return val / 1024
            elif "MB" in unit or "MIB" in unit:
                return val / (1024 ** 2)
    return None

# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------
class CloudianClient:
    def __init__(self, group_id):
        self.group_id = group_id
        self._csrf = None
        self._s = requests.Session()
        self._s.headers["User-Agent"] = (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        if not VERIFY_SSL:
            self._s.verify = False
            urllib3.disable_warnings()

    @property
    def _login_url(self):
        return f"{BASE_URL}/Cloudian/login.htm"

    @property
    def _usage_url(self):
        return f"{BASE_URL}/Cloudian/usageorig.htm"

    @property
    def _graph_json_url(self):
        return f"{BASE_URL}/Cloudian/usagereportgraph.json"

    @property
    def _bucket_url(self):
        return f"{BASE_URL}/Cloudian/bucket.htm"

    @property
    def _object_url(self):
        return f"{BASE_URL}/Cloudian/object.htm"

    @staticmethod
    def _csrf_from_html(html):
        soup = BeautifulSoup(html, "html.parser")
        for tag, attr in [("meta", "content"), ("input", "value")]:
            el = soup.find(tag, {"name": "_csrf"})
            if el and el.get(attr):
                return el.get(attr)
        return ""

    def login(self, user, password):
        check_method("GET")
        r = self._s.get(self._login_url, timeout=TIMEOUT)
        r.raise_for_status()
        self._csrf = self._csrf_from_html(r.text)

        check_method("POST")
        login_data = {
            "gmtOffset": GMT_OFFSET,
            "action": "login",
            "groupid": self.group_id,
            "userid": user,
            "password": password,
            "_csrf": self._csrf,
        }
        login_headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": BASE_URL,
            "Referer": self._login_url,
            "X-CSRF-Token": self._csrf,
        }
        r = self._s.post(
            self._login_url,
            data=login_data,
            headers=login_headers,
            allow_redirects=True,
            timeout=TIMEOUT,
        )
        r.raise_for_status()

        # Warmup GETs to settle session
        try:
            self._s.get(f"{BASE_URL}/Cloudian/dashboard.htm", timeout=TIMEOUT, allow_redirects=True)
            self._s.get(self._bucket_url, timeout=TIMEOUT, allow_redirects=True)
        except:
            pass

        fresh = self._csrf_from_html(r.text)
        if fresh:
            self._csrf = fresh

    def _fetch_quota_from_graph_json(self) -> Optional[float]:
        """
        POST to usagereportgraph.json — the same endpoint the UI uses to build
        the storage linegraph. The response contains groupQosHL (hard limit)
        and groupQosWL (warning level) in GiB. We use groupQosHL as the quota.
        Returns quota in TB, or None if the field is missing or zero.
        """
        check_method("POST")
        r = self._s.post(
            self._graph_json_url,
            data={
                "selectedGroup": self.group_id,
                "selectedUser": "",
                "operation": "SB",
                "granularity": "hour",
                "timePeriod": "0",
                "trafficType": "NORMAL",
                "pageSize": "10",
                "action": "list",
                "offset": "",
                "offsetType": "",
                "nextOffsetType": "false",
                "gmtOffset": GMT_OFFSET,
                "regionOffset": "",
                "reportOutputType": "graph",
                "_csrf": self._csrf or "",
            },
            headers={
                "Referer": self._usage_url,
                "X-Requested-With": "XMLHttpRequest",
                "Accept": "application/json, text/javascript, */*; q=0.01",
            },
            timeout=TIMEOUT,
            allow_redirects=False,
        )
        if r.status_code != 200:
            return None
        try:
            data = r.json()
        except Exception:
            return None

        # groupQosHL is the hard limit in GiB; 0.0 means no quota set
        hl = data.get("groupQosHL")
        if hl and float(hl) > 0:
            return gib_to_tb(float(hl))

        return None

    def storage_usage(self) -> Tuple[float, Optional[float]]:
        """Returns (used_tb, quota_tb). quota_tb is None if not found."""
        r_check = self._s.get(self._usage_url, timeout=TIMEOUT, allow_redirects=False)
        if r_check.status_code in (301, 302, 303, 307, 308) or 'login' in r_check.url.lower():
            raise RuntimeError("Session expired or redirected to login before usage fetch")

        check_method("POST")
        r = self._s.post(
            self._usage_url,
            data={
                "selectedGroup": self.group_id,
                "selectedUser": "",
                "operation": "SB",
                "granularity": "hour",
                "timePeriod": "0",
                "trafficType": "NORMAL",
                "pageSize": "10",
                "action": "list",
                "offset": "",
                "offsetType": "",
                "nextOffsetType": "false",
                "gmtOffset": GMT_OFFSET,
                "regionOffset": "",
                "reportOutputType": "list",
                "_csrf": self._csrf or "",
            },
            headers={"Referer": self._usage_url},
            timeout=TIMEOUT,
            allow_redirects=False,
        )
        if r.status_code in (301, 302, 303, 307, 308) or 'login' in r.url.lower():
            raise RuntimeError("Session expired or redirected to login after usage POST")

        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        page_text = soup.get_text(separator=" ", strip=True)

        # Try free-text patterns, then the graph JSON endpoint
        quota_tb = parse_quota_from_text(page_text) or self._fetch_quota_from_graph_json()

        rows = soup.select(".analytics_inner_box_inner table tr") or soup.select("table tr")
        used_tb = 0.0
        for row in rows[1:]:
            cells = row.find_all("td")
            if len(cells) < 7:
                continue
            op = cells[4].get_text(strip=True)
            val = cells[5].get_text(strip=True)
            if op == "Storage Bytes":
                bytes_val = parse_size_to_bytes(val)
                used_tb = bytes_to_tb(bytes_val)
                break

        return used_tb, quota_tb

    def buckets(self, user):
        all_names = []
        check_method("GET")
        r = self._s.get(
            self._bucket_url,
            params={
                "selectedGroup": self.group_id,
                "selectedUser": user,
                "action": "list",
                "gmtOffset": GMT_OFFSET,
                "_csrf": self._csrf or "",
            },
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        bucket_links = soup.find_all("a", href=re.compile(r"object\.htm\?bucket="))
        for link in bucket_links:
            name = link.get_text(strip=True)
            if name:
                all_names.append(name)

        # was `< 2`, which wrongly triggered the fallback for single-bucket users
        if not all_names:
            hidden_inputs = soup.find_all("input", {"name": "loadBucketPropertiesName"})
            for inp in hidden_inputs:
                name = inp.get("value", "").strip() #type: ignore
                if name:
                    all_names.append(name)

        all_names = list(dict.fromkeys([n for n in all_names if n]))

        result = []
        for name in all_names:
            objs, sb = self._bucket_props(name)
            result.append({
                "bucket": name,
                "objects": objs,
                "size_tb": bytes_to_tb(sb),
            })
        return result

    def _bucket_props(self, name):
        params = {
            "bucket": name,
            "selectedGroup": self.group_id,
            "selectedUser": "",
            "showVersioning": "false",
        }
        headers = {
            "Referer": self._bucket_url,
            "X-Requested-With": "XMLHttpRequest",
        }
        r = self._s.get(
            self._object_url,
            params=params,
            headers=headers,
            timeout=TIMEOUT,
        )
        if r.status_code != 200:
            return 0, 0

        soup = BeautifulSoup(r.text, "html.parser")
        page_text = soup.get_text(separator=" ", strip=True)

        obj_match = re.search(r"Total objects\s*(\d+)", page_text, re.IGNORECASE)
        obj_count = int(obj_match.group(1)) if obj_match else 0

        # explicitly passing the captured value+unit strings instead.
        size_match = re.search(r"Total bytes\s*([\d\.]+)\s*([BKMGTPE]?i?B?)", page_text, re.IGNORECASE)
        if size_match:
            size_bytes = parse_size_to_bytes(f"{size_match.group(1)} {size_match.group(2)}")
        else:
            size_bytes = 0

        return obj_count, size_bytes

# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------
def run_discovery():
    data = [{"{#S3USER}": username} for username in KNOWN_USERS]
    print(json.dumps({"data": data}, indent=2))

# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------
def run_metrics(user, password, group_id, quota_tb_override: Optional[float] = None):
    client = CloudianClient(group_id=group_id)
    client.login(user, password)

    usedspace_tb, quota_tb = client.storage_usage()

    # CLI/macro override takes priority over anything scraped
    if quota_tb_override is not None and quota_tb_override > 0:
        quota_tb = quota_tb_override

    buckets_list = client.buckets(user)
    total_obj = sum(b["objects"] for b in buckets_list)
    bucket_used_tb = sum(b["size_tb"] for b in buckets_list)

    # Use the authoritative quota as the denominator.
    # Fall back to summed bucket sizes only if no quota was found.
    if quota_tb and quota_tb > 0:
        available_tb = quota_tb
        pct_used = (usedspace_tb / quota_tb * 100)
    elif bucket_used_tb > 0:
        available_tb = bucket_used_tb
        pct_used = 100.0  # all known space is used — quota unknown
    else:
        available_tb = 0.0
        pct_used = 0.0

    print(json.dumps({
        "totalavailablesize": round(available_tb, 4),
        "usedspace": round(usedspace_tb, 4),
        "percentageused": round(pct_used, 2),
    }, indent=2))

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    argc = len(sys.argv)
    if argc == 1:
        run_discovery()
    elif argc in (4, 5):
        _, user, password, group_id = sys.argv[:4]
        quota_override = None
        if argc == 5:
            try:
                quota_override = float(sys.argv[4])
            except ValueError:
                fatal(f"Invalid quota value '{sys.argv[4]}' — must be a number in TB (e.g. 10 or 10.5)")
        try:
            run_metrics(user, password, group_id, quota_tb_override=quota_override)
        except Exception as exc:
            fatal(f"Failed for '{user}' / group '{group_id}': {type(exc).__name__} - {exc}")
    else:
        fatal(
            "Usage: \n"
            "  python3 main.py                                    for LLD discovery\n"
            "  python3 main.py <user> <pass> <group>              for metrics (discovered quota)\n"
            "  python3 main.py <user> <pass> <group> <quota_tb>   for metrics with quota override"
        )

if __name__ == "__main__":
    main()