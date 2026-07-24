#!/usr/bin/env python3
"""
logdash — Internal log dashboard for Docker + application logs.
Zero dependencies. Binds to 127.0.0.1:6060 (SSH tunnel access only).

v4: Fixed chart distortion (perfect circles, crisp text, dynamic viewBox).
"""

import http.server
import json
import subprocess
import urllib.parse
import threading
import time
import os
import signal
import sys
from datetime import datetime, timedelta
from collections import defaultdict, deque, Counter

IST = timedelta(hours=5, minutes=30)

BACKEND_CONTAINER = "infra-backend-1"
LOG_DIR = "/app/logs"
PORT = 6060
HOST = "127.0.0.1"

# ─── metrics & cache ring buffer ─────────────────────────────────

SAMPLE_INTERVAL = 10         # seconds between background samples (was 3)
HISTORY_MAX = 360            # ~1 hour at 10s sampling (was 600)
MAX_TAIL_CACHE = 1000        # max log lines to cache in memory (was 5000)

_lock = threading.Lock()
METRICS_HISTORY = deque(maxlen=HISTORY_MAX)
LAST_ACCESS_LOGS = []
LAST_ERROR_LOGS = []
LAST_AUDIT_LOGS = []
LAST_SAMPLE = {"ts": 0}
_containers_cache = {"data": [], "ts": 0}


def _log(msg):
    print(f"[logdash] {msg}", file=sys.stderr, flush=True)


def _now():
    return time.time()


def _sampler_loop():
    global LAST_ACCESS_LOGS, LAST_ERROR_LOGS, LAST_AUDIT_LOGS
    while True:
        try:
            access_lines = file_lines("access.log", MAX_TAIL_CACHE)
            error_lines = file_lines("error.log", MAX_TAIL_CACHE)
            audit_lines = file_lines("audit.log", MAX_TAIL_CACHE)
            
            with _lock:
                LAST_ACCESS_LOGS = list(parse_json_log_lines(access_lines))
                LAST_ERROR_LOGS = list(parse_json_log_lines(error_lines))
                LAST_AUDIT_LOGS = list(parse_json_log_lines(audit_lines))
                
            _sample_metrics()
        except Exception as e:
            _log(f"sampler error: {e}")
            time.sleep(5)
            continue
        time.sleep(SAMPLE_INTERVAL)


def _sample_metrics():
    global LAST_SAMPLE
    try:
        with _lock:
            access_logs = LAST_ACCESS_LOGS[-500:]
            error_logs = LAST_ERROR_LOGS[-500:]

        req_count = len(access_logs)
        total_dur = 0
        durations = []
        status_counts = Counter()

        for e in access_logs:
            meta = e.get("metadata", {})
            dur = int(meta.get("duration_ms", 0) or 0)
            total_dur += dur
            durations.append(dur)
            status = int(meta.get("status", 0) or 0)
            bucket = (status // 100) * 100 if status else 0
            status_counts[bucket] += 1

        errors = len(error_logs)
        durations.sort()
        def pct(p):
            if not durations: return 0
            idx = min(len(durations) - 1, max(0, int(len(durations) * p)))
            return durations[idx]

        stats = docker_stats()

        sample = {
            "ts": _now(),
            "req_count": req_count,
            "avg_dur": round(total_dur / req_count) if req_count else 0,
            "p50": pct(0.50),
            "p95": pct(0.95),
            "p99": pct(0.99),
            "errors": errors,
            "status_2xx": status_counts.get(200, 0),
            "status_3xx": status_counts.get(300, 0),
            "status_4xx": status_counts.get(400, 0),
            "status_5xx": status_counts.get(500, 0),
            "cpu": stats.get("total_cpu", 0),
            "mem_bytes": stats.get("total_mem_bytes", 0),
            "host_mem_bytes": stats.get("host_mem_bytes", 0),
            "containers": len(stats.get("containers", [])),
        }
        with _lock:
            METRICS_HISTORY.append(sample)
            LAST_SAMPLE = sample
        return sample
    except Exception as e:
        return {"ts": _now(), "error": str(e)}





# ─── docker helpers ──────────────────────────────────────────────

def run(cmd, timeout=10):
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return (p.stdout or "") + (p.stderr or "")


def docker_containers():
    global _containers_cache
    now = _now()
    if now - _containers_cache["ts"] < 5:
        return _containers_cache["data"]
    out = run(["docker", "ps", "--format", "{{.Names}}\t{{.Status}}\t{{.Image}}"])
    containers = []
    for line in out.strip().split("\n"):
        if not line: continue
        parts = line.split("\t")
        if len(parts) >= 3:
            containers.append({"name": parts[0], "status": parts[1], "image": parts[2]})
    _containers_cache = {"data": containers, "ts": now}
    return containers


def docker_stats():
    try:
        raw = run(["docker", "stats", "--no-stream", "--format", "{{json .}}"])
        containers = []
        total_cpu = 0.0
        total_mem_bytes = 0
        host_mem = 0
        for line in raw.strip().split("\n"):
            if not line: continue
            try: s = json.loads(line)
            except json.JSONDecodeError: continue
            
            cpu_str = s.get("CPUPerc", "0%").replace("%", "")
            cpu = float(cpu_str) if cpu_str else 0.0
            mem_str = s.get("MemUsage", "0B / 0B")
            parts = mem_str.split(" / ")
            used_raw = parts[0].strip() if parts else "0B"
            total_raw = parts[1].strip() if len(parts) > 1 else "0B"
            mem_bytes = _parse_bytes(used_raw)
            host_mem = _parse_bytes(total_raw)
            mem_pct_str = s.get("MemPerc", "0%").replace("%", "")
            mem_pct = float(mem_pct_str) if mem_pct_str else 0.0
            
            total_cpu += cpu
            total_mem_bytes += mem_bytes
            containers.append({
                "name": s.get("Name", "?"),
                "cpu": cpu,
                "cpu_str": f"{cpu:.1f}%",
                "mem_bytes": mem_bytes,
                "mem_str": _format_bytes(mem_bytes),
                "mem_total_str": _format_bytes(host_mem),
                "mem_pct": mem_pct,
                "net": s.get("NetIO", "-"),
                "block": s.get("BlockIO", "-"),
            })
        return {
            "containers": containers,
            "total_cpu": round(total_cpu, 1),
            "total_mem_bytes": total_mem_bytes,
            "total_mem_str": _format_bytes(total_mem_bytes),
            "host_mem_str": _format_bytes(host_mem) if host_mem else "?",
            "host_mem_bytes": host_mem,
        }
    except Exception as e:
        return {"containers": [], "total_cpu": 0, "total_mem_str": "0", "host_mem_str": "?", "error": str(e)}


def _parse_bytes(s):
    s = s.strip()
    if not s: return 0
    try:
        if s.endswith("TiB"): return int(float(s[:-3]) * 1024 ** 4)
        if s.endswith("GiB"): return int(float(s[:-3]) * 1024 ** 3)
        if s.endswith("MiB"): return int(float(s[:-3]) * 1024 ** 2)
        if s.endswith("KiB"): return int(float(s[:-3]) * 1024)
        if s.endswith("B"): return int(s[:-1]) if s[:-1].isdigit() else 0
        return int(s) if s.isdigit() else 0
    except (ValueError, IndexError): return 0


def _format_bytes(b):
    if b >= 1024 ** 4: return f"{b / 1024 ** 4:.2f} TiB"
    if b >= 1024 ** 3: return f"{b / 1024 ** 3:.2f} GiB"
    if b >= 1024 ** 2: return f"{b / 1024 ** 2:.1f} MiB"
    if b >= 1024: return f"{b / 1024:.1f} KiB"
    return f"{b} B"


def docker_logs(container, tail=200):
    try:
        raw = run(["docker", "logs", "--tail", str(tail), "-t", container])
        return [s for s in raw.strip().split("\n") if s]
    except Exception as e:
        return [str(e)]


def file_lines(filename, tail=200):
    try:
        return run(["docker", "exec", BACKEND_CONTAINER, "tail", "-n", str(tail), f"{LOG_DIR}/{filename}"]).strip().split("\n")
    except Exception:
        return []


# ─── log parsing (instant, reads from memory cache) ──────────────

def parse_json_log_lines(lines):
    for line in lines:
        if not line: continue
        try: yield json.loads(line)
        except json.JSONDecodeError: continue


def get_cached_logs(log_type, tail):
    with _lock:
        if log_type == "access":
            logs = LAST_ACCESS_LOGS
        elif log_type == "error":
            logs = LAST_ERROR_LOGS
        elif log_type == "audit":
            logs = LAST_AUDIT_LOGS
        else:
            logs = []
        return logs[-tail:] if tail else logs


def access_entries(tail=200, search=None, cid=None):
    entries = []
    for e in get_cached_logs("access", tail):
        msg = e.get("message", "")
        meta = e.get("metadata", {})
        t = e.get("time", "")
        entry = {
            "time": _format_ist_time(t),
            "full_time": t,
            "method": meta.get("method", msg.split(" ")[0] if msg else "-"),
            "path": meta.get("path", "-"),
            "status": meta.get("status", 0),
            "duration_ms": int(meta.get("duration_ms", 0)),
            "ip": meta.get("client_ip", "-"),
            "correlation_id": e.get("correlation_id", "-"),
            "message": msg,
        }
        if _match(entry, search, cid):
            entries.append(entry)
    return entries


def error_groups(tail=500, search=None, cid=None):
    groups = defaultdict(lambda: {"count": 0, "first": "", "last": "", "level": "ERROR", "source": "", "cids": []})
    for e in get_cached_logs("error", tail):
        msg = e.get("message", "")
        if not _match_text(msg, search, cid, e.get("correlation_id")): continue
        g = groups[msg]
        g["count"] += 1
        t = e.get("time", "")
        if not g["first"] or t < g["first"]: g["first"] = t
        g["last"] = t
        g["source"] = e.get("source", "")
        g["level"] = e.get("level", "ERROR")
        ci = e.get("correlation_id", "-")
        if ci != "-" and ci not in g["cids"]: g["cids"].append(ci)
    
    result = []
    for msg, g in groups.items():
        result.append({"message": msg, "count": g["count"], "first": g["first"], "last": g["last"], "source": g["source"], "level": g["level"], "cids": g["cids"]})
    result.sort(key=lambda x: -x["count"])
    return result


def audit_entries(tail=200, search=None, cid=None):
    entries = []
    for e in get_cached_logs("audit", tail):
        if _match(e, search, cid):
            t = e.get("time", "")
            entries.append({
                "time": _format_ist_time(t),
                "message": e.get("message", ""),
                "level": e.get("level", "INFO"),
                "source": e.get("source", ""),
                "correlation_id": e.get("correlation_id", "-"),
            })
    entries.reverse()
    return entries


def all_logs(tail=200, search=None, cid=None):
    items = []
    for e in get_cached_logs("access", tail):
        if _match(e, search, cid): items.append(_to_line(e, "access"))
    for e in get_cached_logs("error", tail):
        if _match(e, search, cid): items.append(_to_line(e, "error"))
    for e in get_cached_logs("audit", tail):
        if _match(e, search, cid): items.append(_to_line(e, "audit"))
    items.sort(key=lambda x: x["time"], reverse=True)
    return items


def search_docker(tail=200, query=None):
    if not query: return []
    q = query.lower()
    results = []
    for c in docker_containers():
        for line in docker_logs(c["name"], tail):
            if q in line.lower():
                results.append({"container": c["name"], "line": line})
    return results


def summary():
    access_logs = get_cached_logs("access", 500)
    error_logs = get_cached_logs("error", 500)
    containers = docker_containers()

    req_count = len(access_logs)
    total_dur = sum(int(e.get("metadata", {}).get("duration_ms", 0)) for e in access_logs)
    errors = len(error_logs)
    error_types = len(set(e.get("message", "") for e in error_logs))
    status_5xx = sum(1 for e in access_logs if int(e.get("metadata", {}).get("status", 0) or 0) >= 500)
    status_4xx = sum(1 for e in access_logs if 400 <= int(e.get("metadata", {}).get("status", 0) or 0) < 500)

    return {
        "requests": req_count,
        "avg_duration_ms": round(total_dur / req_count) if req_count else 0,
        "errors": errors,
        "error_types": error_types,
        "status_5xx": status_5xx,
        "status_4xx": status_4xx,
        "containers": len(containers),
        "containers_down": sum(1 for c in containers if "Up" not in c["status"]),
    }


def metrics_history(window=300):
    cutoff = _now() - window
    with _lock:
        return [s for s in METRICS_HISTORY if s.get("ts", 0) >= cutoff]


def top_paths(tail=500, limit=10):
    counts = Counter()
    durations = defaultdict(list)
    statuses = defaultdict(Counter)
    for e in get_cached_logs("access", tail):
        meta = e.get("metadata", {})
        p = meta.get("path", "?")
        counts[p] += 1
        durations[p].append(int(meta.get("duration_ms", 0) or 0))
        s = int(meta.get("status", 0) or 0)
        statuses[p][(s // 100) * 100] += 1
    out = []
    for p, c in counts.most_common(limit):
        durs = durations[p]
        out.append({"path": p, "count": c, "avg_ms": round(sum(durs)/len(durs)) if durs else 0, "max_ms": max(durs) if durs else 0, "status": dict(statuses[p])})
    return out


def top_ips(tail=500, limit=10):
    counts = Counter()
    statuses = defaultdict(Counter)
    for e in get_cached_logs("access", tail):
        meta = e.get("metadata", {})
        ip = meta.get("client_ip", "?")
        counts[ip] += 1
        s = int(meta.get("status", 0) or 0)
        statuses[ip][(s // 100) * 100] += 1
    out = []
    for ip, c in counts.most_common(limit):
        out.append({"ip": ip, "count": c, "status": dict(statuses[ip])})
    return out


def latency_histogram(tail=500):
    buckets = [0, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000]
    counts = [0] * (len(buckets))
    for e in get_cached_logs("access", tail):
        d = int(e.get("metadata", {}).get("duration_ms", 0) or 0)
        placed = False
        for i in range(len(buckets) - 1):
            if buckets[i] <= d < buckets[i + 1]:
                counts[i] += 1
                placed = True
                break
        if not placed: counts[-1] += 1
    return [{"label": f"<{buckets[i+1]}ms" if i < len(buckets)-1 else f">{buckets[-2]}ms", "count": counts[i]} for i in range(len(buckets))]


def status_distribution(tail=500):
    c = Counter()
    for e in get_cached_logs("access", tail):
        s = int(e.get("metadata", {}).get("status", 0) or 0)
        c[(s // 100) * 100] += 1
    return [{"code": k, "count": v} for k, v in sorted(c.items())]


# ─── helpers ─────────────────────────────────────────────────────

def _format_ist_time(iso_str):
    try:
        dt = datetime.fromisoformat(iso_str)
        ist = dt + IST
        return ist.strftime("%H:%M:%S")
    except (ValueError, TypeError):
        return iso_str[11:19] if len(iso_str) > 19 else iso_str

def _match(entry, search, cid):
    if cid and entry.get("correlation_id") != cid: return False
    if search and search.lower() not in json.dumps(entry).lower(): return False
    return True

def _match_text(text, search, cid, entry_cid):
    if cid and entry_cid != cid: return False
    if search and search.lower() not in (text or "").lower(): return False
    return True

def _to_line(entry, source):
    t = entry.get("time", "")
    return {
        "time": t,
        "time_short": _format_ist_time(t),
        "level": entry.get("level", "INFO"),
        "source": source,
        "message": entry.get("message", ""),
        "correlation_id": entry.get("correlation_id", "-"),
    }


# ─── HTTP server ─────────────────────────────────────────────────

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        params = urllib.parse.parse_qs(parsed.query)

        def get(p, default=None):
            v = params.get(p, [None])[0]
            return v if v else default

        tail = int(get("tail", "200"))
        search = get("search") or None
        cid = get("cid") or None
        window = int(get("window", "300"))

        try:
            if path == "/": self._serve_html()
            elif path == "/api/summary": self._json(summary())
            elif path == "/api/access": self._json(access_entries(tail, search, cid))
            elif path == "/api/errors": self._json(error_groups(tail, search, cid))
            elif path == "/api/audit": self._json(audit_entries(tail, search, cid))
            elif path == "/api/all": self._json(all_logs(tail, search, cid))
            elif path == "/api/stats": self._json(docker_stats())
            elif path == "/api/docker-containers": self._json(docker_containers())
            elif path == "/api/docker-logs":
                container = get("container")
                if not container:
                    self._json({"error": "container param required"}, 400); return
                self._json(docker_logs(container, tail))
            elif path == "/api/docker-search": self._json(search_docker(tail, get("q")))
            elif path == "/api/metrics-history": self._json(metrics_history(window))
            elif path == "/api/top-paths": self._json(top_paths(tail))
            elif path == "/api/top-ips": self._json(top_ips(tail))
            elif path == "/api/latency-hist": self._json(latency_histogram(tail))
            elif path == "/api/status-dist": self._json(status_distribution(tail))
            else: self.send_error(404)
        except Exception as e:
            self._json({"error": str(e)}, 500)

    def _json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _serve_html(self):
        html = HTML.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(html)

    def log_message(self, fmt, *args):
        pass


# ─── HTML (Fixed SVG Distortion) ─────────────────────────────────

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>logdash — real-time</title>
<style>
:root{
  --bg:#0a0d12; --bg2:#0d1117; --surface:#12161d; --surface2:#1a2029; --surface3:#222934;
  --border:#262c37; --border-soft:#1c222b;
  --text:#d5dbe3; --muted:#7d8794; --dim:#565f6b;
  --blue:#58a6ff; --green:#3fb950; --yellow:#d29922; --red:#f85149; --purple:#bc8cff; --cyan:#39c5cf; --orange:#e88758;
  --r-sm:6px; --r-md:9px; --r-lg:12px;
}
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
html{scrollbar-color:var(--border) var(--bg)}
body{background:radial-gradient(ellipse at top,#0d1117 0%,#0a0d12 60%);color:var(--text);
  font:12px/1.55 'JetBrains Mono','Fira Code',ui-monospace,monospace;
  min-height:100vh;display:flex;flex-direction:column;letter-spacing:.1px}
:focus-visible{outline:2px solid var(--blue);outline-offset:1px;border-radius:3px}
@media (prefers-reduced-motion:reduce){*{transition:none !important;animation:none !important}}

.hdr{background:linear-gradient(180deg,var(--surface) 0%,rgba(18,22,29,.6) 100%);
  border-bottom:1px solid var(--border);padding:11px 18px;display:flex;align-items:center;gap:11px;flex-shrink:0;position:relative;backdrop-filter:blur(8px)}
.hdr::after{content:'';position:absolute;left:0;right:0;bottom:-1px;height:2px;
  background:linear-gradient(90deg,var(--blue) 0 25%,var(--red) 25% 50%,var(--purple) 50% 75%,var(--green) 75% 100%);opacity:.55}
.hdr .dot{width:7px;height:7px;background:var(--green);border-radius:50%;box-shadow:0 0 0 3px #3fb95020,0 0 8px var(--green);flex-shrink:0}
.hdr .dot.down{background:var(--red);box-shadow:0 0 0 3px #f8514920,0 0 8px var(--red);animation:pulse 1.6s ease-in-out infinite}
.hdr .dot.live{animation:livepulse 2s ease-in-out infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
@keyframes livepulse{0%,100%{box-shadow:0 0 0 3px #3fb95020,0 0 8px var(--green)}50%{box-shadow:0 0 0 6px #3fb95030,0 0 12px var(--green)}}
.hdr h1{font-size:14px;font-weight:700;letter-spacing:.3px;display:flex;align-items:baseline;gap:1px}
.hdr h1 .crt{color:var(--blue);font-weight:600;animation:blink 1.1s steps(1) infinite}
@keyframes blink{0%,49%{opacity:1}50%,100%{opacity:0}}
.hdr .sub{color:var(--muted);font-size:10px;margin-left:auto;white-space:nowrap;opacity:.85;display:flex;gap:14px;align-items:center}
.hdr .live-tag{background:#3fb95022;border:1px solid #3fb95055;color:var(--green);padding:3px 8px;border-radius:4px;font-size:10px;font-weight:700;display:flex;align-items:center;gap:5px;cursor:pointer;user-select:none}
.hdr .live-tag.paused{background:#d2992222;border-color:#d2992255;color:var(--yellow)}
.hdr .live-tag .ld{width:5px;height:5px;border-radius:50%;background:currentColor;animation:ld 1s infinite}
.hdr .live-tag.paused .ld{animation:none}
@keyframes ld{0%{opacity:.3}50%{opacity:1}100%{opacity:.3}}

.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:1px;background:var(--border);flex-shrink:0}
.card{background:var(--surface);padding:11px 14px 10px;position:relative;overflow:hidden;transition:background .15s;border-top:2px solid transparent}
.card[data-tone="b"]{border-top-color:#58a6ff55}
.card[data-tone="g"]{border-top-color:#3fb95055}
.card[data-tone="y"]{border-top-color:#d2992255}
.card[data-tone="r"]{border-top-color:#f8514955}
.card[data-tone="m"]{border-top-color:#8b949e55}
.card[data-tone="p"]{border-top-color:#bc8cff55}
.card:hover{background:var(--surface2)}
.card .ic{font-size:10px;color:var(--muted);opacity:.55;margin-bottom:2px;letter-spacing:.5px;text-transform:uppercase}
.card .val{font-size:22px;font-weight:700;font-variant-numeric:tabular-nums;line-height:1.2;transition:color .2s}
.card .val.g{color:var(--green)}.card .val.y{color:var(--yellow)}.card .val.r{color:var(--red)}.card .val.b{color:var(--blue)}.card .val.p{color:var(--purple)}
.card .lbl{font-size:9px;color:var(--muted);margin-top:3px;text-transform:uppercase;letter-spacing:.7px}
.card .spark{position:absolute;bottom:0;left:0;right:0;height:24px;opacity:.7;pointer-events:none}
.card .spark svg{width:100%;height:100%;display:block}

.tabs{background:var(--surface);border-bottom:1px solid var(--border);display:flex;padding:7px 14px;gap:3px;flex-shrink:0;overflow-x:auto}
.tabs button{background:none;color:var(--muted);border:1px solid transparent;padding:6px 14px;cursor:pointer;font:inherit;font-size:12px;border-radius:var(--r-sm);white-space:nowrap;transition:all .15s}
.tabs button:hover{color:var(--text);background:var(--surface2)}
.tabs button.active{color:var(--bg);background:var(--blue);font-weight:700}
.tabs button .badge{background:var(--red);color:#fff;font-size:9px;padding:1px 5px;border-radius:8px;margin-left:5px;font-weight:700}
.tabs button.active .badge{background:#fff;color:var(--red)}

.ctrl{background:var(--surface);border-bottom:1px solid var(--border);padding:9px 18px;display:flex;align-items:center;gap:10px;flex-shrink:0;flex-wrap:wrap}
.ctrl .search{flex:1;min-width:160px;max-width:360px;position:relative}
.ctrl .search::before{content:'⌕';position:absolute;left:9px;top:50%;transform:translateY(-50%);color:var(--muted);font-size:13px;pointer-events:none}
.ctrl .search input{padding-left:26px}
.ctrl input,.ctrl select{background:var(--bg);color:var(--text);border:1px solid var(--border);padding:6px 10px;font:inherit;font-size:12px;border-radius:var(--r-sm);transition:border-color .15s,box-shadow .15s}
.ctrl input:focus,.ctrl select:focus{outline:none;border-color:var(--blue);box-shadow:0 0 0 3px #58a6ff1c}
.ctrl input::placeholder{color:var(--muted);opacity:.7}
.ctrl .tail-inp{width:70px;text-align:center}
.ctrl .refresh-btn{background:var(--green);color:#04220f;border:none;padding:6px 15px;border-radius:var(--r-sm);cursor:pointer;font:inherit;font-size:12px;font-weight:700;transition:filter .15s,transform .1s}
.ctrl .refresh-btn:hover{filter:brightness(1.1)}
.ctrl .refresh-btn:active{transform:scale(.96)}
.ctrl label.tail-lbl{color:var(--muted);font-size:10px}
.ctrl .window-sel{display:flex;gap:2px;background:var(--bg);padding:2px;border-radius:var(--r-sm);border:1px solid var(--border)}
.ctrl .window-sel button{background:none;border:none;color:var(--muted);padding:4px 9px;cursor:pointer;font:inherit;font-size:11px;border-radius:4px;transition:all .15s}
.ctrl .window-sel button.active{background:var(--blue);color:var(--bg);font-weight:700}

.cid-badge{background:#1f6feb14;border:1px solid #1f6feb44;color:var(--blue);padding:5px 9px;border-radius:var(--r-sm);font-size:11px;display:none;align-items:center;gap:7px}
.cid-badge .clr{cursor:pointer;color:var(--muted);font-weight:700;padding:0 2px;transition:color .15s}
.cid-badge .clr:hover{color:var(--red)}

.main{flex:1;overflow:auto;padding:0}
.main::-webkit-scrollbar{width:10px;height:10px}
.main::-webkit-scrollbar-thumb{background:var(--border);border-radius:5px}
.main::-webkit-scrollbar-thumb:hover{background:#3a4351}

.dash{padding:14px;display:grid;grid-template-columns:repeat(12,1fr);gap:14px;grid-auto-rows:min-content}
.panel{background:var(--surface);border:1px solid var(--border);border-radius:var(--r-lg);overflow:hidden;display:flex;flex-direction:column;transition:border-color .15s}
.panel:hover{border-color:#3a4351}
.panel-head{padding:11px 14px 6px;display:flex;align-items:center;justify-content:space-between;gap:10px}
.panel-head .title{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.6px;color:var(--text)}
.panel-head .sub{font-size:10px;color:var(--muted)}
.panel-head .legend{display:flex;gap:10px;font-size:10px;color:var(--muted)}
.panel-head .legend span{display:flex;align-items:center;gap:4px}
.panel-head .legend i{width:8px;height:8px;border-radius:2px;display:inline-block}
.panel-body{flex:1;padding:6px 14px 14px;position:relative;min-height:0}
.panel-body svg{display:block;width:100%;height:100%}
.col-6{grid-column:span 6}
.col-4{grid-column:span 4}
.col-3{grid-column:span 3}
.col-8{grid-column:span 8}
.col-12{grid-column:span 12}
.h-220{height:220px}.h-260{height:260px}.h-180{height:180px}.h-300{height:300px}

.chart-grid line{stroke:var(--border-soft);stroke-width:1}
.chart-axis text{fill:var(--muted);font-size:9px;font-family:inherit}
.chart-area{opacity:.25}
.chart-line{fill:none;stroke-width:1.8;stroke-linejoin:round;stroke-linecap:round;vector-effect:non-scaling-stroke}
.chart-dot{transition:r .15s;cursor:pointer}
.chart-dot:hover{r:4}
.chart-bar{transition:opacity .15s;cursor:pointer}
.chart-bar:hover{opacity:.8}
.chart-bar-label{fill:var(--muted);font-size:9px;text-anchor:middle}
.tooltip{position:fixed;background:var(--surface3);border:1px solid var(--border);border-radius:6px;padding:6px 10px;font-size:11px;pointer-events:none;z-index:100;box-shadow:0 4px 16px rgba(0,0,0,.5);display:none;white-space:nowrap}
.tooltip .tt-time{color:var(--muted);font-size:9px;margin-bottom:3px}
.tooltip .tt-row{display:flex;justify-content:space-between;gap:14px}
.tooltip .tt-val{font-weight:700;color:var(--text)}

.tbl{width:100%;border-collapse:collapse}
.tbl th{position:sticky;top:0;background:var(--surface);color:var(--muted);font-weight:700;font-size:10px;text-transform:uppercase;letter-spacing:.6px;padding:10px 14px;text-align:left;border-bottom:1px solid var(--border);cursor:pointer;user-select:none;z-index:1;white-space:nowrap;transition:color .15s}
.tbl th:hover{color:var(--text)}
.tbl th .arrow{color:var(--blue)}
.tbl td{padding:7px 14px;border-bottom:1px solid var(--border-soft);white-space:nowrap}
.tbl tr:hover td{background:var(--surface)}
.tbl .st2{color:var(--green)}.tbl .st3{color:var(--yellow)}.tbl .st4,.tbl .st5{color:var(--red);font-weight:700}
.tbl .dur{color:var(--muted)}.tbl .ip{color:var(--muted);font-size:11px}
.tbl .cid{color:var(--blue);cursor:pointer;font-size:11px;max-width:130px;overflow:hidden;text-overflow:ellipsis;display:inline-block;transition:opacity .15s}
.tbl .cid:hover{text-decoration:underline;opacity:.8}
.method{font-weight:700;font-size:10px;padding:2px 7px;border-radius:4px;background:#7d879417;color:var(--text);letter-spacing:.3px}
.method.GET{background:#3fb95022;color:var(--green)}
.method.POST{background:#58a6ff22;color:var(--blue)}
.method.PUT{background:#d2992222;color:var(--yellow)}
.method.DELETE{background:#f8514922;color:var(--red)}
.method.PATCH{background:#bc8cff22;color:var(--purple)}

.top-list{padding:4px 0}
.top-row{display:flex;align-items:center;gap:10px;padding:8px 14px;border-bottom:1px solid var(--border-soft);transition:background .12s}
.top-row:hover{background:var(--surface2)}
.top-row .rank{width:18px;color:var(--muted);font-size:10px;text-align:right;flex-shrink:0}
.top-row .name{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:11px}
.top-row .cnt{font-weight:700;font-variant-numeric:tabular-nums;font-size:12px;min-width:38px;text-align:right}
.top-row .br{flex-shrink:0;width:60px;height:5px;background:var(--bg);border-radius:3px;overflow:hidden;display:flex}
.top-row .br div{height:100%}

.err-list{padding:4px 0}
.err-row{display:flex;align-items:flex-start;padding:10px 18px;border-bottom:1px solid var(--border-soft);cursor:pointer;gap:11px;transition:background .12s}
.err-row:hover{background:var(--surface)}
.err-row .count{background:var(--red);color:#fff;font-size:10px;font-weight:700;padding:2px 8px;border-radius:10px;min-width:26px;text-align:center;flex-shrink:0}
.err-row .count.w1{background:#f8514926;color:var(--red)}
.err-row .body{flex:1;min-width:0}
.err-row .msg{word-break:break-all}
.err-row .meta{color:var(--muted);font-size:10px;margin-top:4px;display:flex;gap:6px;flex-wrap:wrap;align-items:center}
.err-row .cid-s{color:var(--blue);cursor:pointer;font-size:10px;padding:2px 7px;background:#1f6feb12;border-radius:4px;transition:background .15s}
.err-row .cid-s:hover{text-decoration:underline;background:#1f6feb20}

.stat-cont{display:flex;flex-direction:column;gap:7px;padding:14px 18px}
.stat-row{display:flex;align-items:center;gap:12px;padding:9px 14px;background:var(--surface2);border-radius:var(--r-md);border:1px solid var(--border);transition:border-color .15s}
.stat-row:hover{border-color:#3a4351}
.stat-row .sn{width:180px;font-weight:700;flex-shrink:0;overflow:hidden;text-overflow:ellipsis}
.stat-row .sg{flex:1;display:flex;align-items:center;gap:10px;min-width:0}
.stat-bar-wrap{flex:1;height:7px;background:var(--bg);border-radius:4px;overflow:hidden}
.stat-bar{height:100%;border-radius:4px;transition:width .5s ease}
.stat-bar.cpu{background:linear-gradient(90deg,#3fb950,#58a6ff)}
.stat-bar.mem{background:linear-gradient(90deg,#d29922,#f85149)}
.stat-val{min-width:62px;text-align:right;font-size:11px;font-variant-numeric:tabular-nums;color:var(--muted);flex-shrink:0}
.stat-row .sx{color:var(--muted);font-size:10px;flex-shrink:0;min-width:90px;text-align:right}

.docker-topbar{display:flex;align-items:center;gap:8px;padding:9px 18px;border-bottom:1px solid var(--border);flex-wrap:wrap}
.docker-list{padding:2px 0}
.docker-entry{padding:6px 18px;border-bottom:1px solid var(--border-soft);white-space:pre-wrap;word-break:break-all;font-size:11px}
.docker-entry:hover{background:var(--surface)}
.docker-entry .ctn{color:var(--purple);margin-right:8px;font-weight:700}

.log-row{display:flex;padding:6px 18px;border-bottom:1px solid var(--border-soft);gap:9px;align-items:flex-start;transition:background .1s}
.log-row:hover{background:var(--surface)}
.log-row .lv{width:46px;flex-shrink:0;font-size:9px;font-weight:700;text-align:center;padding:2px 4px;border-radius:3px}
.log-row .lv.INFO{background:#1f6feb1e;color:var(--blue)}.log-row .lv.ERROR{background:#f851491e;color:var(--red)}
.log-row .lv.WARN{background:#d299221e;color:var(--yellow)}.log-row .lv.DEBUG{background:#7d87941e;color:var(--muted)}
.log-row .ts{color:var(--muted);font-size:10px;flex-shrink:0;padding-top:1px}
.log-row .src{color:var(--purple);font-size:10px;flex-shrink:0;padding-top:1px;min-width:44px}
.log-row .msg{flex:1;word-break:break-all}

.bar{background:var(--surface);border-top:1px solid var(--border);padding:7px 18px;font-size:10px;color:var(--muted);display:flex;gap:16px;flex-shrink:0;align-items:center}
.bar span:first-child::before{content:'●';color:var(--green);margin-right:6px;font-size:8px}
.bar .last-upd{margin-left:auto;color:var(--muted)}

.loading{padding:56px 24px;text-align:center;color:var(--muted);position:relative}
.loading::before{content:'';display:block;width:20px;height:20px;margin:0 auto 14px;border-radius:50%;
  border:2px solid var(--border);border-top-color:var(--blue);animation:spin .7s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
.empty{margin:22px;padding:52px 24px;text-align:center;color:var(--muted);border:1px dashed var(--border);border-radius:var(--r-lg)}
.empty .big{font-size:20px;margin-bottom:7px;color:var(--text);font-weight:700}
.empty .hint{font-size:11px;margin-top:4px;opacity:.85}

@media (max-width:1100px){
  .col-6,.col-4,.col-8{grid-column:span 12}
  .col-3{grid-column:span 6}
}
@media (max-width:820px){
  .cards{grid-template-columns:repeat(4,1fr)}
  .card .val{font-size:14px}
  .hdr .sub{display:none}
  .stat-row{flex-wrap:wrap}
  .stat-row .sn{width:100%}
  .col-3{grid-column:span 12}
}
</style>
</head>
<body>
<div class="hdr">
  <span class="dot live" id="statusDot"></span>
  <h1>logdash<span class="crt">_</span></h1>
  <span class="sub">
    <span class="live-tag" id="liveTag" onclick="toggleAutoRefresh()"><span class="ld"></span><span id="liveTagText">LIVE</span></span>
    <span>127.0.0.1:6060 &mdash; SSH only</span>
  </span>
</div>
<div class="cards">
  <div class="card" data-tone="b"><div class="ic">requests</div><div class="val b" id="sReq">-</div><div class="lbl">In window</div><div class="spark" id="spReq"></div></div>
  <div class="card" data-tone="g"><div class="ic">p50 latency</div><div class="val g" id="sP50">-</div><div class="lbl">Median ms</div><div class="spark" id="spLat"></div></div>
  <div class="card" data-tone="p"><div class="ic">p95 latency</div><div class="val p" id="sP95">-</div><div class="lbl">95th pct ms</div><div class="spark" id="spP95"></div></div>
  <div class="card" data-tone="r"><div class="ic">5xx errors</div><div class="val r" id="s5xx">-</div><div class="lbl">Server errors</div><div class="spark" id="sp5xx"></div></div>
  <div class="card" data-tone="y"><div class="ic">4xx client</div><div class="val y" id="s4xx">-</div><div class="lbl">Client errors</div><div class="spark" id="sp4xx"></div></div>
  <div class="card" data-tone="g"><div class="ic">cpu</div><div class="val g" id="sCpu">-</div><div class="lbl">Total %</div><div class="spark" id="spCpu"></div></div>
  <div class="card" data-tone="b"><div class="ic">memory</div><div class="val b" id="sMem">-</div><div class="lbl">Used / host</div><div class="spark" id="spMem"></div></div>
  <div class="card" data-tone="m"><div class="ic">docker</div><div class="val" id="sCont">-</div><div class="lbl">Containers</div></div>
</div>
<div class="tabs">
  <button class="active" data-tab="overview">Overview</button>
  <button data-tab="access">Access</button>
  <button data-tab="errors">Errors<span class="badge" id="errBadge" style="display:none"></span></button>
  <button data-tab="audit">Audit</button>
  <button data-tab="docker">Docker</button>
  <button data-tab="stats">Stats</button>
  <button data-tab="all">All</button>
</div>
<div class="ctrl">
  <div class="search"><input type="text" id="searchInp" placeholder="Search across all logs..." oninput="onSearchDebounced()" style="width:100%"></div>
  <span class="cid-badge" id="cidBadge">CID <span id="cidText"></span><span class="clr" onclick="clearCid()" title="Clear trace">&times;</span></span>
  <div class="window-sel" id="windowSel">
    <button data-w="60">1m</button>
    <button data-w="300" class="active">5m</button>
    <button data-w="900">15m</button>
    <button data-w="3600">1h</button>
  </div>
  <label class="tail-lbl" for="tailInp">lines</label>
  <input type="number" class="tail-inp" id="tailInp" value="200" min="10" max="5000" onchange="onSearch()" title="Lines to fetch">
  <button class="refresh-btn" onclick="onSearch()">Refresh</button>
</div>
<div class="main" id="main">Loading...</div>
<div class="bar">
  <span id="statusText">Ready</span>
  <span id="entryCount"></span>
  <span class="last-upd" id="lastUpd"></span>
</div>
<div class="tooltip" id="tooltip"></div>

<script>
let tab = 'overview', sortCol = null, sortDir = 1, searchDebounce = null;
let autoRefresh = true, refreshTimer = null, windowSec = 300;
let lastValues = {};

const COL = { blue:'#58a6ff', green:'#3fb950', yellow:'#d29922', red:'#f85149', purple:'#bc8cff', cyan:'#39c5cf', orange:'#e88758', muted:'#7d8794' };

// ── SVG helpers ─────────────────────────────────────────────────
const NS = 'http://www.w3.org/2000/svg';
function el(name, attrs) {
  const e = document.createElementNS(NS, name);
  if (attrs) for (const k in attrs) e.setAttribute(k, attrs[k]);
  return e;
}

// Measure actual container pixel size to avoid ANY stretching/distortion
function getContainerSize(container, defW, defH) {
  const rect = container.getBoundingClientRect();
  const cs = window.getComputedStyle(container);
  const padX = parseFloat(cs.paddingLeft) + parseFloat(cs.paddingRight);
  const padY = parseFloat(cs.paddingTop) + parseFloat(cs.paddingBottom);
  const w = Math.max(50, rect.width - padX) || defW;
  const h = Math.max(50, rect.height - padY) || defH;
  return { w, h };
}

function smoothPath(pts, tension=0.5) {
  if (pts.length < 2) return '';
  let d = `M ${pts[0][0]} ${pts[0][1]}`;
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[i-1] || pts[i];
    const p1 = pts[i];
    const p2 = pts[i+1];
    const p3 = pts[i+2] || p2;
    const cp1x = p1[0] + (p2[0] - p0[0]) / 6 * tension;
    const cp1y = p1[1] + (p2[1] - p0[1]) / 6 * tension;
    const cp2x = p2[0] - (p3[0] - p1[0]) / 6 * tension;
    const cp2y = p2[1] - (p3[1] - p1[1]) / 6 * tension;
    d += ` C ${cp1x} ${cp1y}, ${cp2x} ${cp2y}, ${p2[0]} ${p2[1]}`;
  }
  return d;
}

function emptyChart(msg) {
  const s = el('div', { style: 'display:flex;align-items:center;justify-content:center;height:100%;color:var(--muted);font-size:10px;' });
  s.textContent = msg;
  s.classList.add('empty-chart');
  return s;
}

// ── Sparkline ─────────────────────────────────────────────────
function sparkline(container, values, color) {
  if (!container) return;
  let svg = container.querySelector('svg');
  
  if (!values || values.length < 2) {
    if (svg) svg.remove();
    return;
  }
  
  const w = 200, h = 24, pad = 2;
  const min = Math.min(...values), max = Math.max(...values);
  const range = (max - min) || 1;
  const pts = values.map((v, i) => [
    pad + (i / (values.length - 1)) * (w - pad * 2),
    h - pad - ((v - min) / range) * (h - pad * 2)
  ]);
  
  if (!svg) {
    svg = el('svg', { viewBox: `0 0 ${w} ${h}`, preserveAspectRatio: 'none' });
    svg.style.width = '100%'; svg.style.height = '100%';
    const gid = `sp-${color.slice(1)}`;
    svg.innerHTML = `
      <defs>
        <linearGradient id="${gid}" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="${color}" stop-opacity="0.5"></stop>
          <stop offset="100%" stop-color="${color}" stop-opacity="0.05"></stop>
        </linearGradient>
      </defs>
      <path class="sp-area" fill="url(#${gid})" stroke="none"></path>
      <path class="sp-line" fill="none" stroke="${color}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" vector-effect="non-scaling-stroke"></path>
      <circle class="sp-dot" r="2" fill="${color}"></circle>
    `;
    container.appendChild(svg);
  }
  
  const areaD = smoothPath(pts) + ` L ${pts[pts.length-1][0]} ${h} L ${pts[0][0]} ${h} Z`;
  svg.querySelector('.sp-area').setAttribute('d', areaD);
  svg.querySelector('.sp-line').setAttribute('d', smoothPath(pts));
  svg.querySelector('.sp-dot').setAttribute('cx', pts[pts.length-1][0]);
  svg.querySelector('.sp-dot').setAttribute('cy', pts[pts.length-1][1]);
}

// ── Line Chart ────────────────────────────────────────────────
function lineChart(container, series, opts={}) {
  const defW = 600, defH = 200, padL = 40, padR = 12, padT = 10, padB = 22;
  const { w, h } = getContainerSize(container, defW, defH);
  
  let svg = container.querySelector('svg');
  const allVals = series.flatMap(s => s.values);
  if (!allVals.length) {
    if (svg) svg.remove();
    if (!container.querySelector('.empty-chart')) container.appendChild(emptyChart('No data'));
    return;
  }
  const emptyEl = container.querySelector('.empty-chart');
  if (emptyEl) emptyEl.remove();

  if (!svg) {
    svg = el('svg', { viewBox: `0 0 ${w} ${h}`, preserveAspectRatio: 'xMidYMid meet' });
    svg.style.width = '100%'; svg.style.height = '100%';
    svg.innerHTML = `<g class="chart-grid"></g><g class="chart-axis"></g><defs></defs>`;
    series.forEach((s, i) => {
      if (opts.area) svg.appendChild(el('path', { class: `chart-area-${i}` }));
      svg.appendChild(el('path', { class: `chart-line-${i}`, fill: 'none', 'stroke-width': '1.8', 'stroke-linecap':'round','stroke-linejoin':'round', 'vector-effect':'non-scaling-stroke' }));
      svg.appendChild(el('g', { class: `chart-dots-${i}` }));
    });
    container.appendChild(svg);
  } else {
    svg.setAttribute('viewBox', `0 0 ${w} ${h}`);
  }
  
  const minV = opts.minY !== undefined ? opts.minY : 0;
  const maxV = Math.max(opts.maxY !== undefined ? opts.maxY : 0, ...allVals) * 1.1 || 1;
  const n = series[0].values.length;
  const xFor = i => padL + (n > 1 ? (i / (n - 1)) * (w - padL - padR) : 0);
  const yFor = v => padT + (h - padT - padB) - ((v - minV) / (maxV - minV)) * (h - padT - padB);
  
  const grid = svg.querySelector('.chart-grid');
  const axis = svg.querySelector('.chart-axis');
  const defs = svg.querySelector('defs');
  grid.innerHTML = ''; axis.innerHTML = ''; defs.innerHTML = '';
  
  const ticks = 4;
  for (let i = 0; i <= ticks; i++) {
    const v = minV + (maxV - minV) * (i / ticks);
    const y = yFor(v);
    grid.appendChild(el('line', { x1: padL, y1: y, x2: w - padR, y2: y, 'stroke-dasharray': i === 0 ? '0' : '2,3' }));
    const t = el('text', { x: padL - 5, y: y + 3, 'text-anchor': 'end' });
    t.textContent = opts.fmtY ? opts.fmtY(v) : Math.round(v);
    axis.appendChild(t);
  }
  if (opts.xLabels) {
    opts.xLabels.forEach((lbl, i) => {
      const x = i === 0 ? padL : i === opts.xLabels.length - 1 ? w - padR : padL + (w - padL - padR) / 2;
      const t = el('text', { x, y: h - 6, 'text-anchor': i === 0 ? 'start' : i === opts.xLabels.length - 1 ? 'end' : 'middle' });
      t.textContent = lbl;
      axis.appendChild(t);
    });
  }
  
  series.forEach((s, i) => {
    const pts = s.values.map((v, i) => [xFor(i), yFor(v)]);
    if (opts.area) {
      const areaD = smoothPath(pts) + ` L ${pts[pts.length-1][0]} ${h - padB} L ${pts[0][0]} ${h - padB} Z`;
      const areaPath = svg.querySelector(`.chart-area-${i}`);
      const gid = `lg-${i}-${Math.random().toString(36).slice(2,8)}`;
      const lg = el('linearGradient', { id: gid, x1: 0, y1: 0, x2: 0, y2: 1 });
      lg.appendChild(el('stop', { offset: '0%', 'stop-color': s.color, 'stop-opacity': '0.35' }));
      lg.appendChild(el('stop', { offset: '100%', 'stop-color': s.color, 'stop-opacity': '0.02' }));
      defs.appendChild(lg);
      areaPath.setAttribute('d', areaD);
      areaPath.setAttribute('fill', `url(#${gid})`);
    }
    svg.querySelector(`.chart-line-${i}`).setAttribute('d', smoothPath(pts));
    svg.querySelector(`.chart-line-${i}`).setAttribute('stroke', s.color);
    
    const dotsG = svg.querySelector(`.chart-dots-${i}`);
    dotsG.innerHTML = '';
    pts.forEach((p, idx) => {
      const dot = el('circle', { cx: p[0], cy: p[1], r: 2.5, fill: s.color, class: 'chart-dot' });
      dot.addEventListener('mouseenter', e => showTooltip(e, s.label + ': ' + (opts.fmtY ? opts.fmtY(s.values[idx]) : s.values[idx]), opts.xLabels ? xLabelAt(idx, opts.xLabels, n) : ''));
      dot.addEventListener('mouseleave', hideTooltip);
      dotsG.appendChild(dot);
    });
  });
}

function xLabelAt(i, labels, n) {
  if (!labels || !labels.length) return '';
  if (n === 1) return labels[0];
  const t = i / (n - 1);
  const idx = Math.round(t * (labels.length - 1));
  return labels[idx];
}

// ── Stacked Area ──────────────────────────────────────────────
function stackedArea(container, series, opts={}) {
  const defW = 600, defH = 200, padL = 40, padR = 12, padT = 10, padB = 22;
  const { w, h } = getContainerSize(container, defW, defH);
  
  let svg = container.querySelector('svg');
  const n = series[0].values.length;
  if (!n) {
    if (svg) svg.remove();
    if (!container.querySelector('.empty-chart')) container.appendChild(emptyChart('No data'));
    return;
  }
  const emptyEl = container.querySelector('.empty-chart');
  if (emptyEl) emptyEl.remove();

  if (!svg) {
    svg = el('svg', { viewBox: `0 0 ${w} ${h}`, preserveAspectRatio: 'xMidYMid meet' });
    svg.style.width = '100%'; svg.style.height = '100%';
    svg.innerHTML = `<g class="chart-grid"></g><g class="chart-axis"></g><g class="chart-areas"></g>`;
    container.appendChild(svg);
  } else {
    svg.setAttribute('viewBox', `0 0 ${w} ${h}`);
  }
  
  const stacks = series.map(s => [...s.values]);
  for (let i = 1; i < stacks.length; i++) {
    for (let j = 0; j < n; j++) stacks[i][j] += stacks[i-1][j];
  }
  const maxV = Math.max(...stacks[stacks.length-1], 1);
  const xFor = i => padL + (n > 1 ? (i / (n - 1)) * (w - padL - padR) : 0);
  const yFor = v => padT + (h - padT - padB) - (v / maxV) * (h - padT - padB);
  
  const grid = svg.querySelector('.chart-grid');
  const axis = svg.querySelector('.chart-axis');
  const areasG = svg.querySelector('.chart-areas');
  grid.innerHTML = ''; axis.innerHTML = ''; areasG.innerHTML = '';
  
  for (let i = 0; i <= 4; i++) {
    const v = maxV * i / 4;
    const y = yFor(v);
    grid.appendChild(el('line', { x1: padL, y1: y, x2: w - padR, y2: y, 'stroke-dasharray': i === 0 ? '0' : '2,3' }));
    const t = el('text', { x: padL - 5, y: y + 3, 'text-anchor': 'end' });
    t.textContent = Math.round(v);
    axis.appendChild(t);
  }
  if (opts.xLabels) {
    opts.xLabels.forEach((lbl, i) => {
      const x = i === 0 ? padL : i === opts.xLabels.length - 1 ? w - padR : padL + (w - padL - padR) / 2;
      const t = el('text', { x, y: h - 6, 'text-anchor': i === 0 ? 'start' : i === opts.xLabels.length - 1 ? 'end' : 'middle' });
      t.textContent = lbl;
      axis.appendChild(t);
    });
  }
  
  for (let si = series.length - 1; si >= 0; si--) {
    const top = stacks[si];
    const bot = si > 0 ? stacks[si-1] : new Array(n).fill(0);
    let d = '';
    for (let i = 0; i < n; i++) {
      const x = xFor(i), y = yFor(top[i]);
      d += (i === 0 ? 'M ' : 'L ') + x + ' ' + y + ' ';
    }
    for (let i = n - 1; i >= 0; i--) {
      const x = xFor(i), y = yFor(bot[i]);
      d += 'L ' + x + ' ' + y + ' ';
    }
    d += 'Z';
    const path = el('path', { d, fill: series[si].color, 'fill-opacity': '0.85', stroke: 'none', class: 'chart-bar' });
    path.addEventListener('mouseenter', e => showTooltip(e, series[si].label + ': ' + series[si].values[Math.floor(n/2)]));
    path.addEventListener('mouseleave', hideTooltip);
    areasG.appendChild(path);
  }
}

// ── Donut Chart (Strict 100x100 ViewBox for Perfect Circle) ────
function donutChart(container, data, opts={}) {
  container.innerHTML = '';
  const svg = el('svg', { viewBox: '0 0 100 100', preserveAspectRatio: 'xMidYMid meet' });
  svg.style.width = '100%'; svg.style.height = '100%';
  const total = data.reduce((s, d) => s + d.value, 0);
  if (!total) { container.appendChild(emptyChart('No data')); return; }
  
  let angle = -Math.PI / 2;
  const r = 42, ri = 28, cx = 50, cy = 50;
  
  data.forEach(d => {
    const frac = d.value / total;
    const a2 = angle + frac * Math.PI * 2;
    const x1 = cx + Math.cos(angle) * r, y1 = cy + Math.sin(angle) * r;
    const x2 = cx + Math.cos(a2) * r, y2 = cy + Math.sin(a2) * r;
    const xi1 = cx + Math.cos(a2) * ri, yi1 = cy + Math.sin(a2) * ri;
    const xi2 = cx + Math.cos(angle) * ri, yi2 = cy + Math.sin(angle) * ri;
    const large = frac > 0.5 ? 1 : 0;
    const d_ = `M ${x1} ${y1} A ${r} ${r} 0 ${large} 1 ${x2} ${y2} L ${xi1} ${yi1} A ${ri} ${ri} 0 ${large} 0 ${xi2} ${yi2} Z`;
    const path = el('path', { d: d_, fill: d.color, class: 'chart-bar', 'data-label': d.label, 'data-value': d.value });
    path.addEventListener('mouseenter', e => showTooltip(e, d.label + ': ' + d.value + ' (' + (frac*100).toFixed(1) + '%)'));
    path.addEventListener('mouseleave', hideTooltip);
    if (opts.onClick) path.addEventListener('click', () => opts.onClick(d));
    svg.appendChild(path);
    angle = a2;
  });
  
  const t1 = el('text', { x: 50, y: 52, 'text-anchor': 'middle', fill: 'var(--text)', 'font-size': '14', 'font-weight': '700' });
  t1.textContent = total;
  const t2 = el('text', { x: 50, y: 62, 'text-anchor': 'middle', fill: 'var(--muted)', 'font-size': '6' });
  t2.textContent = opts.centerLabel || 'total';
  svg.appendChild(t1); svg.appendChild(t2);
  container.appendChild(svg);
}

// ── Bar Chart ─────────────────────────────────────────────────
function barChart(container, data, opts={}) {
  container.innerHTML = '';
  const defW = 500, defH = 180, padL = 30, padR = 8, padT = 8, padB = 30;
  const { w, h } = getContainerSize(container, defW, defH);
  const svg = el('svg', { viewBox: `0 0 ${w} ${h}`, preserveAspectRatio: 'xMidYMid meet' });
  svg.style.width = '100%'; svg.style.height = '100%';
  const maxV = Math.max(...data.map(d => d.value), 1);
  const bw = (w - padL - padR) / data.length;
  const grid = el('g', { class: 'chart-grid' });
  const axis = el('g', { class: 'chart-axis' });
  for (let i = 0; i <= 3; i++) {
    const v = maxV * i / 3;
    const y = padT + (h - padT - padB) - (v / maxV) * (h - padT - padB);
    grid.appendChild(el('line', { x1: padL, y1: y, x2: w - padR, y2: y, 'stroke-dasharray': i === 0 ? '0' : '2,3' }));
    axis.appendChild(el('text', { x: padL - 5, y: y + 3, 'text-anchor': 'end' })).textContent = Math.round(v);
  }
  svg.appendChild(grid); svg.appendChild(axis);
  data.forEach((d, i) => {
    const x = padL + i * bw + 2;
    const bh = (d.value / maxV) * (h - padT - padB);
    const y = padT + (h - padT - padB) - bh;
    const bar = el('rect', { x, y, width: bw - 4, height: bh, fill: d.color || COL.blue, rx: 2, class: 'chart-bar' });
    bar.addEventListener('mouseenter', e => showTooltip(e, d.label + ': ' + d.value));
    bar.addEventListener('mouseleave', hideTooltip);
    svg.appendChild(bar);
    if (opts.labels !== false) {
      const t = el('text', { x: x + (bw - 4) / 2, y: h - 10, class: 'chart-bar-label', 'text-anchor': 'middle' });
      t.textContent = d.label.length > 8 ? d.label.slice(0, 7) + '…' : d.label;
      axis.appendChild(t);
    }
  });
  container.appendChild(svg);
}

// ── Tooltip ─────────────────────────────────────────────────
const tooltip = document.getElementById('tooltip');
function showTooltip(e, text, time) {
  tooltip.innerHTML = (time ? `<div class="tt-time">${time}</div>` : '') + `<div class="tt-row"><span>${text}</span></div>`;
  tooltip.style.display = 'block';
  const r = tooltip.getBoundingClientRect();
  let x = e.clientX + 12, y = e.clientY + 12;
  if (x + r.width > window.innerWidth) x = e.clientX - r.width - 12;
  if (y + r.height > window.innerHeight) y = e.clientY - r.height - 12;
  tooltip.style.left = x + 'px'; tooltip.style.top = y + 'px';
}
function hideTooltip() { tooltip.style.display = 'none'; }

// ── Data Loading ────────────────────────────────────────────
async function fetchJSON(url) {
  const r = await fetch(url);
  return r.json();
}

function fmtTimeShort(ts) {
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function timeLabels(history) {
  if (!history.length) return [];
  return [fmtTimeShort(history[0].ts), fmtTimeShort(history[Math.floor(history.length/2)].ts), fmtTimeShort(history[history.length-1].ts)];
}

function formatBytes(b) {
  if (b >= 1024**4) return (b/1024**4).toFixed(2) + ' TiB';
  if (b >= 1024**3) return (b/1024**3).toFixed(2) + ' GiB';
  if (b >= 1024**2) return (b/1024**2).toFixed(1) + ' MiB';
  if (b >= 1024) return (b/1024).toFixed(1) + ' KiB';
  return b + ' B';
}

async function loadSummary() {
  try {
    const [r, hist] = await Promise.all([
      fetchJSON('/api/summary'),
      fetchJSON('/api/metrics-history?window=' + windowSec),
    ]);
    setVal('sReq', r.requests);
    setVal('sP50', hist.length ? hist[hist.length-1].p50 + 'ms' : '-');
    setVal('sP95', hist.length ? hist[hist.length-1].p95 + 'ms' : '-');
    setVal('s5xx', r.status_5xx);
    setVal('s4xx', r.status_4xx);
    
    const last = hist.length ? hist[hist.length-1] : null;
    setVal('sCpu', last ? last.cpu + '%' : '-');
    setVal('sMem', last ? formatBytes(last.mem_bytes) + ' / ' + formatBytes(last.host_mem_bytes) : '-');
    setVal('sCont', r.containers);
    
    document.getElementById('statusDot').className = 'dot live' + (r.containers_down > 0 ? ' down' : '');
    const badge = document.getElementById('errBadge');
    if (r.errors > 0) { badge.textContent = r.errors; badge.style.display = ''; }
    else badge.style.display = 'none';
    
    sparkline(document.getElementById('spReq'), hist.map(h => h.req_count), COL.blue);
    sparkline(document.getElementById('spLat'), hist.map(h => h.p50), COL.green);
    sparkline(document.getElementById('spP95'), hist.map(h => h.p95), COL.purple);
    sparkline(document.getElementById('sp5xx'), hist.map(h => h.status_5xx), COL.red);
    sparkline(document.getElementById('sp4xx'), hist.map(h => h.status_4xx), COL.yellow);
    sparkline(document.getElementById('spCpu'), hist.map(h => h.cpu), COL.green);
    sparkline(document.getElementById('spMem'), hist.map(h => h.mem_bytes), COL.blue);
    
    document.getElementById('lastUpd').textContent = 'Updated ' + new Date().toLocaleTimeString('en-US', { hour12: false });
  } catch(e) { console.warn('summary error', e); }
}

function setVal(id, v) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = v;
}

// ── Tab Switching ───────────────────────────────────────────
function switchTab(t) {
  tab = t;
  sortCol = null; sortDir = 1;
  document.querySelectorAll('.tabs button').forEach(b => b.classList.remove('active'));
  document.querySelector('[data-tab="' + t + '"]').classList.add('active');
  onSearch();
}

function getParams() {
  const search = document.getElementById('searchInp').value;
  const tail = document.getElementById('tailInp').value;
  const cid = document.getElementById('cidBadge').style.display === 'flex' ? document.getElementById('cidText').textContent : '';
  let p = 'tail=' + encodeURIComponent(tail) + '&window=' + windowSec;
  if (search) p += '&search=' + encodeURIComponent(search);
  if (cid) p += '&cid=' + encodeURIComponent(cid);
  return p;
}

function setCid(cid) {
  document.getElementById('cidBadge').style.display = 'flex';
  document.getElementById('cidText').textContent = cid;
  switchTab('all');
}

function clearCid() {
  document.getElementById('cidBadge').style.display = 'none';
  document.getElementById('cidText').textContent = '';
  onSearch();
}

function onSearchDebounced() {
  clearTimeout(searchDebounce);
  searchDebounce = setTimeout(onSearch, 250);
}

function onSearch() {
  loadSummary();
  const p = getParams();
  switch (tab) {
    case 'overview': renderOverview(p, false); break;
    case 'access': renderAccess(p); break;
    case 'errors': renderErrors(p); break;
    case 'audit': renderAudit(p); break;
    case 'stats': renderStats(); break;
    case 'docker': renderDocker(p); break;
    case 'all': renderAll(p); break;
  }
}

// ── Escape Helpers ──────────────────────────────────────────
function esc(s) { return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }
function cidHtml(cid) { var c = String(cid||''); if (!c || c === '-') return '-'; return '<span class="cid" data-cid="' + esc(c) + '" title="Trace this request across all logs">' + esc(c.substring(0,12)) + '&hellip;</span>'; }
function statusClass(s) { if (s < 300) return 'st2'; if (s < 400) return 'st3'; return 'st4'; }

function emptyState(label, hint) {
  return '<div class="empty"><div class="big">' + esc(label) + '</div><div class="hint">' + esc(hint || '') + '</div></div>';
}

function th(label, col) {
  const arrow = sortCol === col ? '<span class="arrow">' + (sortDir === 1 ? ' ▲' : ' ▼') + '</span>' : '';
  return '<th data-sort="' + col + '">' + label + arrow + '</th>';
}

function doSort(col) {
  if (sortCol === col) sortDir *= -1; else { sortCol = col; sortDir = 1; }
  onSearch();
}

// ── OVERVIEW (Zero-Flicker Real-time Updates) ───────────────
function buildOverviewHTML() {
  let html = '<div class="dash">';
  html += `<div class="panel col-8 h-260"><div class="panel-head">
    <div><div class="title">Request Volume</div><div class="sub">Total requests in window</div></div>
    <div class="legend"><span><i style="background:${COL.blue}"></i>requests</span></div>
    </div><div class="panel-body" id="chartReq"></div></div>`;
  
  html += `<div class="panel col-4 h-260"><div class="panel-head">
    <div><div class="title">Status Codes</div><div class="sub">Distribution</div></div>
    </div><div class="panel-body" id="chartDonut"></div></div>`;
    
  html += `<div class="panel col-8 h-260"><div class="panel-head">
    <div><div class="title">Latency Percentiles</div><div class="sub">ms over time</div></div>
    <div class="legend">
      <span><i style="background:${COL.green}"></i>P50</span>
      <span><i style="background:${COL.yellow}"></i>P95</span>
      <span><i style="background:${COL.red}"></i>P99</span>
    </div></div><div class="panel-body" id="chartLat"></div></div>`;
    
  html += `<div class="panel col-4 h-260"><div class="panel-head">
    <div><div class="title">Latency Distribution</div><div class="sub">request count by bucket</div></div>
    </div><div class="panel-body" id="chartLatHist"></div></div>`;
    
  html += `<div class="panel col-6 h-220"><div class="panel-head">
    <div><div class="title">CPU &amp; Memory</div><div class="sub">host resource trends</div></div>
    <div class="legend"><span><i style="background:${COL.green}"></i>CPU %</span><span><i style="background:${COL.blue}"></i>Mem (B)</span></div>
    </div><div class="panel-body" id="chartCpuMem"></div></div>`;
    
  html += `<div class="panel col-6 h-220"><div class="panel-head">
    <div><div class="title">Error Counts</div><div class="sub">4xx vs 5xx in log window</div></div>
    <div class="legend"><span><i style="background:${COL.yellow}"></i>4xx</span><span><i style="background:${COL.red}"></i>5xx</span></div>
    </div><div class="panel-body" id="chartErr"></div></div>`;
    
  html += `<div class="panel col-6 h-300"><div class="panel-head">
    <div><div class="title">Top Endpoints</div><div class="sub">by request count</div></div>
    </div><div class="panel-body" style="padding:0" id="topPaths"></div></div>`;
    
  html += `<div class="panel col-6 h-300"><div class="panel-head">
    <div><div class="title">Top Client IPs</div><div class="sub">by request count</div></div>
    </div><div class="panel-body" style="padding:0" id="topIps"></div></div>`;
    
  html += '</div>';
  return html;
}

async function renderOverview(p, isAutoRefresh = false) {
  const m = document.getElementById('main');
  
  if (!isAutoRefresh) {
    m.innerHTML = '<div class="loading">Loading real-time metrics&hellip;</div>';
  }
  
  try {
    const [hist, topP, topI, latH, sd] = await Promise.all([
      fetchJSON('/api/metrics-history?window=' + windowSec),
      fetchJSON('/api/top-paths?' + p),
      fetchJSON('/api/top-ips?' + p),
      fetchJSON('/api/latency-hist?' + p),
      fetchJSON('/api/status-dist?' + p),
    ]);
    const labels = timeLabels(hist);

    if (isAutoRefresh && tab !== 'overview') return;
    if (!m.querySelector('.dash')) {
      m.innerHTML = buildOverviewHTML();
    }
    
    lineChart(document.getElementById('chartReq'),
      [{ label: 'requests', values: hist.map(h => h.req_count), color: COL.blue }],
      { area: true, xLabels: labels });

    const sdColors = { 200: COL.green, 300: COL.blue, 400: COL.yellow, 500: COL.red };
    const sdData = sd.map(s => ({ label: s.code + 'xx', value: s.count, color: sdColors[s.code] || COL.muted }));
    donutChart(document.getElementById('chartDonut'), sdData, { centerLabel: 'requests', onClick: d => {
      switchTab('access');
    }});

    lineChart(document.getElementById('chartLat'), [
      { label: 'P50', values: hist.map(h => h.p50), color: COL.green },
      { label: 'P95', values: hist.map(h => h.p95), color: COL.yellow },
      { label: 'P99', values: hist.map(h => h.p99), color: COL.red },
    ], { xLabels: labels, fmtY: v => Math.round(v) + 'ms' });

    barChart(document.getElementById('chartLatHist'),
      latH.map(l => ({ label: l.label, value: l.count, color: COL.purple })));

    const memVals = hist.map(h => h.mem_bytes);
    const maxMem = Math.max(...memVals, 1);
    lineChart(document.getElementById('chartCpuMem'), [
      { label: 'CPU %', values: hist.map(h => h.cpu), color: COL.green },
      { label: 'Mem', values: memVals.map(v => (v / maxMem) * 100), color: COL.blue },
    ], { area: true, xLabels: labels, fmtY: v => Math.round(v) });

    stackedArea(document.getElementById('chartErr'), [
      { label: '4xx', values: hist.map(h => h.status_4xx), color: COL.yellow },
      { label: '5xx', values: hist.map(h => h.status_5xx), color: COL.red },
    ], { xLabels: labels });

    // Top paths list
    const tpEl = document.getElementById('topPaths');
    if (!topP.length) {
      tpEl.innerHTML = emptyState('No path data', 'No access log entries in window.');
    } else {
      const maxC = topP[0].count;
      let tpHtml = '<div class="top-list">';
      topP.forEach((p, i) => {
        const s2 = (p.status['200'] || 0);
        const s4 = (p.status['400'] || 0);
        const s5 = (p.status['500'] || 0);
        const tot = p.count || 1;
        tpHtml += `<div class="top-row">
          <span class="rank">${i+1}</span>
          <span class="name" title="${esc(p.path)}">${esc(p.path)}</span>
          <div class="br" title="2xx:${s2} 4xx:${s4} 5xx:${s5}">
            <div style="width:${(s2/tot)*100}%;background:${COL.green}"></div>
            <div style="width:${(s4/tot)*100}%;background:${COL.yellow}"></div>
            <div style="width:${(s5/tot)*100}%;background:${COL.red}"></div>
          </div>
          <span class="cnt">${p.count}</span>
        </div>`;
      });
      tpHtml += '</div>';
      tpEl.innerHTML = tpHtml;
    }

    // Top IPs list
    const tiEl = document.getElementById('topIps');
    if (!topI.length) {
      tiEl.innerHTML = emptyState('No IP data', 'No access log entries in window.');
    } else {
      const maxC = topI[0].count;
      let tiHtml = '<div class="top-list">';
      topI.forEach((p, i) => {
        const s2 = (p.status['200'] || 0);
        const s4 = (p.status['400'] || 0);
        const s5 = (p.status['500'] || 0);
        const tot = p.count || 1;
        tiHtml += `<div class="top-row">
          <span class="rank">${i+1}</span>
          <span class="name" title="${esc(p.ip)}">${esc(p.ip)}</span>
          <div class="br" title="2xx:${s2} 4xx:${s4} 5xx:${s5}">
            <div style="width:${(s2/tot)*100}%;background:${COL.green}"></div>
            <div style="width:${(s4/tot)*100}%;background:${COL.yellow}"></div>
            <div style="width:${(s5/tot)*100}%;background:${COL.red}"></div>
          </div>
          <span class="cnt">${p.count}</span>
        </div>`;
      });
      tiHtml += '</div>';
      tiEl.innerHTML = tiHtml;
    }

    document.getElementById('entryCount').textContent = hist.length + ' samples';
  } catch(e) {
    if (!isAutoRefresh) {
      document.getElementById('main').innerHTML = emptyState('Failed to load overview', e.message);
    }
  }
}

// ── ACCESS ──────────────────────────────────────────────────
async function renderAccess(p) {
  const m = document.getElementById('main');
  m.innerHTML = '<div class="loading">Loading access log&hellip;</div>';
  try {
    const data = await fetchJSON('/api/access?' + p);
    document.getElementById('entryCount').textContent = data.length + ' entries';
    if (!data.length) { m.innerHTML = emptyState('No access log entries', 'Try widening the search or increasing the line count.'); return; }
    let html = '<table class="tbl"><thead><tr>';
    html += th('Time', 'time') + th('Method', 'method') + th('Path', 'path') + th('Status', 'status') + th('Duration', 'duration_ms') + th('IP', 'ip') + '<th>CID</th>';
    html += '</tr></thead><tbody>';
    let srt = [...data];
    if (sortCol) srt.sort((a,b) => {
      let va = a[sortCol], vb = b[sortCol];
      if (typeof va === 'number') return (va - vb) * sortDir;
      return String(va||'').localeCompare(String(vb||'')) * sortDir;
    });
    for (const e of srt) {
      html += '<tr>';
      html += '<td>' + esc(e.time) + '</td>';
      html += '<td><span class="method ' + esc(e.method) + '">' + esc(e.method) + '</span></td>';
      html += '<td>' + esc(e.path) + '</td>';
      html += '<td class="' + statusClass(e.status) + '">' + e.status + '</td>';
      html += '<td class="dur">' + e.duration_ms + 'ms</td>';
      html += '<td class="ip">' + esc(e.ip) + '</td>';
      html += '<td>' + cidHtml(e.correlation_id) + '</td>';
      html += '</tr>';
    }
    html += '</tbody></table>';
    m.innerHTML = html;
  } catch(e) { m.innerHTML = emptyState('Failed to load', e.message); }
}

// ── ERRORS ──────────────────────────────────────────────────
async function renderErrors(p) {
  const m = document.getElementById('main');
  m.innerHTML = '<div class="loading">Loading errors&hellip;</div>';
  try {
    const data = await fetchJSON('/api/errors?' + p);
    document.getElementById('entryCount').textContent = data.length + ' unique errors';
    if (!data.length) { m.innerHTML = emptyState('No errors &#127881;', 'Nothing matched in the current window.'); return; }
    let html = '<div class="err-list">';
    for (const e of data) {
      const c1 = e.count === 1 ? ' w1' : '';
      html += '<div class="err-row">';
      html += '<span class="count' + c1 + '">' + e.count + '</span>';
      html += '<div class="body">';
      html += '<div class="msg">' + esc(e.message) + '</div>';
      html += '<div class="meta"><span>' + esc(e.source) + '</span><span>·</span><span>' + esc((e.first||'').substring(11,19));
      if (e.last && e.last !== e.first) html += ' &ndash; ' + esc(e.last.substring(11,19));
      html += '</span>';
      for (const c of (e.cids || [])) {
        html += '<span class="cid-s" data-cid="' + esc(c) + '">' + esc(String(c||'').substring(0,14)) + '&hellip;</span>';
      }
      html += '</div></div></div>';
    }
    html += '</div>';
    m.innerHTML = html;
  } catch(e) { m.innerHTML = emptyState('Failed to load', e.message); }
}

// ── AUDIT ───────────────────────────────────────────────────
async function renderAudit(p) {
  const m = document.getElementById('main');
  m.innerHTML = '<div class="loading">Loading audit log&hellip;</div>';
  try {
    const data = await fetchJSON('/api/audit?' + p);
    document.getElementById('entryCount').textContent = data.length + ' entries';
    if (!data.length) { m.innerHTML = emptyState('No audit entries', 'Try widening the search or increasing the line count.'); return; }
    let html = '<table class="tbl"><thead><tr><th>Time</th><th>Event</th><th>Source</th><th>CID</th></tr></thead><tbody>';
    for (const e of data) {
      html += '<tr>';
      html += '<td>' + esc(e.time) + '</td>';
      html += '<td>' + esc(e.message) + '</td>';
      html += '<td class="ip">' + esc(e.source) + '</td>';
      html += '<td>' + cidHtml(e.correlation_id) + '</td>';
      html += '</tr>';
    }
    html += '</tbody></table>';
    m.innerHTML = html;
  } catch(e) { m.innerHTML = emptyState('Failed to load', e.message); }
}

// ── ALL ─────────────────────────────────────────────────────
async function renderAll(p) {
  const m = document.getElementById('main');
  m.innerHTML = '<div class="loading">Loading combined timeline&hellip;</div>';
  try {
    const data = await fetchJSON('/api/all?' + p);
    document.getElementById('entryCount').textContent = data.length + ' entries';
    if (!data.length) { m.innerHTML = emptyState('No matching log lines', 'This correlation ID or search may not appear in the current line window.'); return; }
    let html = '';
    for (const e of data) {
      html += '<div class="log-row">';
      html += '<span class="lv ' + esc(e.level) + '">' + esc(e.level) + '</span>';
      html += '<span class="ts">' + esc(e.time_short) + '</span>';
      html += '<span class="src">' + esc(e.source) + '</span>';
      html += '<span class="msg">' + esc(e.message) + '</span>';
      if (e.correlation_id && e.correlation_id !== '-') html += '<span class="cid" style="font-size:10px" data-cid="' + esc(e.correlation_id) + '">' + esc(String(e.correlation_id||'').substring(0,12)) + '&hellip;</span>';
      html += '</div>';
    }
    m.innerHTML = html;
  } catch(e) { m.innerHTML = emptyState('Failed to load', e.message); }
}

// ── STATS ───────────────────────────────────────────────────
async function renderStats() {
  const m = document.getElementById('main');
  m.innerHTML = '<div class="loading">Loading container stats&hellip;</div>';
  try {
    const data = await fetchJSON('/api/stats');
    if (!data.containers || !data.containers.length) {
      m.innerHTML = emptyState('No stats', data.error || 'docker stats returned nothing.');
      return;
    }
    let html = '<div class="stat-cont">';
    for (const c of data.containers) {
      const cpuW = Math.min(c.cpu, 100);
      const memW = Math.min(c.mem_pct, 100);
      html += '<div class="stat-row">';
      html += '<span class="sn">' + esc(c.name) + '</span>';
      html += '<div class="sg"><span class="stat-val" style="min-width:44px">CPU</span><div class="stat-bar-wrap"><div class="stat-bar cpu" style="width:' + cpuW + '%"></div></div><span class="stat-val">' + esc(c.cpu_str) + '</span></div>';
      html += '<div class="sg"><span class="stat-val" style="min-width:44px">MEM</span><div class="stat-bar-wrap"><div class="stat-bar mem" style="width:' + memW + '%"></div></div><span class="stat-val">' + esc(c.mem_str) + '</span></div>';
      html += '<span class="sx">' + esc(c.net) + '</span>';
      html += '<span class="sx">' + esc(c.block) + '</span>';
      html += '</div>';
    }
    html += '</div>';
    m.innerHTML = html;
    document.getElementById('entryCount').textContent = data.containers.length + ' containers';
  } catch(e) { m.innerHTML = emptyState('Failed to load', e.message); }
}

// ── DOCKER ──────────────────────────────────────────────────
async function renderDocker(p) {
  const m = document.getElementById('main');
  m.innerHTML = '<div class="loading">Loading containers&hellip;</div>';
  try {
    const containers = await fetchJSON('/api/docker-containers');
    let html = '<div class="docker-topbar">';
    html += '<select id="dockerSel">';
    html += '<option value="">-- select container --</option>';
    for (const c of containers) {
      html += '<option value="' + esc(c.name) + '">' + esc(c.name) + ' (' + esc(c.status) + ')</option>';
    }
    html += '</select></div>';
    html += '<div id="dockerOut"></div>';
    m.innerHTML = html;
    document.getElementById('entryCount').textContent = containers.length + ' containers';
    if (!containers.length) { document.getElementById('dockerOut').innerHTML = emptyState('No running containers', 'docker ps returned nothing.'); }
  } catch(e) { m.innerHTML = emptyState('Failed to load', e.message); }
}

async function renderDockerFromSel() {
  const sel = document.getElementById('dockerSel');
  const out = document.getElementById('dockerOut');
  const tail = document.getElementById('tailInp').value;
  const search = document.getElementById('searchInp').value;
  if (!sel.value) { out.innerHTML = ''; return; }
  out.innerHTML = '<div class="loading">Loading&hellip;</div>';
  try {
    let url = '/api/docker-logs?container=' + encodeURIComponent(sel.value) + '&tail=' + encodeURIComponent(tail);
    const data = await fetchJSON(url);
    let lines = data;
    if (search) lines = lines.filter(l => l.toLowerCase().includes(search.toLowerCase()));
    if (!lines.length) { out.innerHTML = emptyState('No matching lines', 'Nothing from ' + sel.value + ' matches the current search.'); document.getElementById('entryCount').textContent = '0 lines'; return; }
    let html = '<div class="docker-list">';
    for (const line of lines) {
      html += '<div class="docker-entry"><span class="ctn">' + esc(sel.value) + '</span>' + esc(line) + '</div>';
    }
    html += '</div>';
    out.innerHTML = html;
    document.getElementById('entryCount').textContent = lines.length + ' lines';
  } catch(e) { out.innerHTML = emptyState('Failed to load', e.message); }
}

// ── Auto-Refresh ────────────────────────────────────────────
function toggleAutoRefresh() {
  autoRefresh = !autoRefresh;
  const tag = document.getElementById('liveTag');
  const txt = document.getElementById('liveTagText');
  if (autoRefresh) {
    tag.classList.remove('paused');
    txt.textContent = 'LIVE';
    scheduleRefresh();
  } else {
    tag.classList.add('paused');
    txt.textContent = 'PAUSED';
    if (refreshTimer) { clearTimeout(refreshTimer); refreshTimer = null; }
  }
}

function scheduleRefresh() {
  if (!autoRefresh) return;
  if (refreshTimer) clearTimeout(refreshTimer);
  refreshTimer = setTimeout(() => {
    loadSummary();
    if (tab === 'overview') renderOverview(getParams(), true);
    scheduleRefresh();
  }, 3000);
}

// ── Event Delegation ────────────────────────────────────────
document.querySelector('.tabs').addEventListener('click', function(e) {
  var btn = e.target.closest('[data-tab]');
  if (btn) switchTab(btn.dataset.tab);
});

document.getElementById('main').addEventListener('click', function(e) {
  var cidEl = e.target.closest('[data-cid]');
  if (cidEl) { setCid(cidEl.dataset.cid); return; }
  var sortEl = e.target.closest('[data-sort]');
  if (sortEl) { doSort(sortEl.dataset.sort); return; }
});

document.getElementById('main').addEventListener('change', function(e) {
  if (e.target.id === 'dockerSel') renderDockerFromSel();
});

document.getElementById('windowSel').addEventListener('click', function(e) {
  var btn = e.target.closest('[data-w]');
  if (!btn) return;
  windowSec = parseInt(btn.dataset.w);
  document.querySelectorAll('#windowSel button').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  onSearch();
});

document.addEventListener('keydown', function(e) {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT') return;
  if (e.key === '/') { e.preventDefault(); document.getElementById('searchInp').focus(); }
  if (e.key === 'p' || e.key === 'P') toggleAutoRefresh();
  if (e.key === 'r' || e.key === 'R') onSearch();
});

// Initial Load
loadSummary();
onSearch();
scheduleRefresh();
</script>
</body>
</html>"""


if __name__ == "__main__":
    server = http.server.ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"logdash v5 → http://{HOST}:{PORT}", flush=True)
    print("SSH tunnel:  ssh -L 6060:localhost:6060 app-backend@192.168.122.101", flush=True)
    print(f"Background sampler: every {SAMPLE_INTERVAL}s, history {HISTORY_MAX} samples", flush=True)

    threading.Thread(target=_sampler_loop, daemon=True).start()

    def _shutdown(signum, frame):
        _log(f"received signal {signum}, shutting down...")
        server.shutdown()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
    print("[logdash] stopped", flush=True)