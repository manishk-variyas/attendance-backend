#!/usr/bin/env python3
"""
logdash — Internal log dashboard for Docker + application logs.
Zero dependencies. Binds to 127.0.0.1:6060 (SSH tunnel access only).

Features:
  - Sortable access log table (time/method/path/status/duration/IP)
  - Deduplicated error view with occurrence counts
  - Cross-source search (access + errors + audit + all Docker containers)
  - Click a correlation ID to trace a single request across all log types
  - Container status dashboard
"""

import http.server
import json
import subprocess
import urllib.parse
from collections import defaultdict

BACKEND_CONTAINER = "infra-backend-1"
LOG_DIR = "/app/logs"
PORT = 6060
HOST = "127.0.0.1"


# ─── docker helpers ──────────────────────────────────────────────

def run(cmd, timeout=10):
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return (p.stdout or "") + (p.stderr or "")


def docker_containers():
    out = run(["docker", "ps", "--format", "{{.Names}}\t{{.Status}}\t{{.Image}}"])
    containers = []
    for line in out.strip().split("\n"):
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) >= 3:
            containers.append({"name": parts[0], "status": parts[1], "image": parts[2]})
    return containers


def docker_stats():
    try:
        raw = run(["docker", "stats", "--no-stream", "--format", "{{json .}}"])
        containers = []
        total_cpu = 0.0
        total_mem_bytes = 0
        host_mem = 0
        for line in raw.strip().split("\n"):
            if not line:
                continue
            try:
                s = json.loads(line)
            except json.JSONDecodeError:
                continue
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
            net = s.get("NetIO", "-")
            block = s.get("BlockIO", "-")
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
                "net": net,
                "block": block,
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
    if not s:
        return 0
    try:
        if s.endswith("TiB"):
            return int(float(s[:-3]) * 1024 ** 4)
        if s.endswith("GiB"):
            return int(float(s[:-3]) * 1024 ** 3)
        if s.endswith("MiB"):
            return int(float(s[:-3]) * 1024 ** 2)
        if s.endswith("KiB"):
            return int(float(s[:-3]) * 1024)
        if s.endswith("B"):
            return int(s[:-1]) if s[:-1].isdigit() else 0
        return int(s) if s.isdigit() else 0
    except (ValueError, IndexError):
        return 0


def _format_bytes(b):
    if b >= 1024 ** 4:
        return f"{b / 1024 ** 4:.2f} TiB"
    if b >= 1024 ** 3:
        return f"{b / 1024 ** 3:.2f} GiB"
    if b >= 1024 ** 2:
        return f"{b / 1024 ** 2:.1f} MiB"
    if b >= 1024:
        return f"{b / 1024:.1f} KiB"
    return f"{b} B"


def docker_logs(container, tail=200):
    try:
        raw = run(["docker", "logs", "--tail", str(tail), "-t", container])
        return [s for s in raw.strip().split("\n") if s]
    except Exception as e:
        return [str(e)]


def file_lines(filename, tail=200):
    try:
        return run(
            ["docker", "exec", BACKEND_CONTAINER, "tail", "-n", str(tail), f"{LOG_DIR}/{filename}"]
        ).strip().split("\n")
    except Exception:
        return []


# ─── log parsing ─────────────────────────────────────────────────

def parse_json_log_lines(lines):
    """Yields parsed dicts from JSON log lines."""
    for line in lines:
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def access_entries(tail=200, search=None, cid=None):
    entries = []
    for e in parse_json_log_lines(file_lines("access.log", tail)):
        msg = e.get("message", "")
        meta = e.get("metadata", {})
        t = e.get("time", "")
        time_short = t[11:19] if len(t) >= 19 else t
        entry = {
            "time": time_short,
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
    groups = defaultdict(lambda: {"count": 0, "first": "", "last": "", "level": "ERROR",
                                    "source": "", "cids": []})
    for e in parse_json_log_lines(file_lines("error.log", tail)):
        msg = e.get("message", "")
        if not _match_text(msg, search, cid, e.get("correlation_id")):
            continue
        g = groups[msg]
        g["count"] += 1
        t = e.get("time", "")
        if not g["first"] or t < g["first"]:
            g["first"] = t
        g["last"] = t
        g["source"] = e.get("source", "")
        g["level"] = e.get("level", "ERROR")
        ci = e.get("correlation_id", "-")
        if ci != "-" and ci not in g["cids"]:
            g["cids"].append(ci)
    result = []
    for msg, g in groups.items():
        result.append({
            "message": msg,
            "count": g["count"],
            "first": g["first"],
            "last": g["last"],
            "source": g["source"],
            "level": g["level"],
            "cids": g["cids"],
        })
    result.sort(key=lambda x: -x["count"])
    return result


def audit_entries(tail=200, search=None, cid=None):
    entries = []
    for e in parse_json_log_lines(file_lines("audit.log", tail)):
        if _match(e, search, cid):
            entries.append({
                "time": e.get("time", "")[11:23] if len(e.get("time", "")) >= 23 else "",
                "message": e.get("message", ""),
                "level": e.get("level", "INFO"),
                "source": e.get("source", ""),
                "correlation_id": e.get("correlation_id", "-"),
            })
    return entries


def all_logs(tail=200, search=None, cid=None):
    items = []
    for e in parse_json_log_lines(file_lines("access.log", tail)):
        if _match(e, search, cid):
            items.append(_to_line(e, "access"))
    for e in parse_json_log_lines(file_lines("error.log", tail)):
        if _match(e, search, cid):
            items.append(_to_line(e, "error"))
    for e in parse_json_log_lines(file_lines("audit.log", tail)):
        if _match(e, search, cid):
            items.append(_to_line(e, "audit"))
    items.sort(key=lambda x: x["time"], reverse=True)
    return items


def search_docker(tail=200, query=None):
    if not query:
        return []
    q = query.lower()
    results = []
    for c in docker_containers():
        for line in docker_logs(c["name"], tail):
            if q in line.lower():
                results.append({"container": c["name"], "line": line})
    return results


def summary():
    access_lines = file_lines("access.log", 500)
    error_lines = file_lines("error.log", 500)
    containers = docker_containers()

    req_count = 0
    total_dur = 0
    errors = 0
    error_types = set()
    status_5xx = 0
    status_4xx = 0

    for e in parse_json_log_lines(access_lines):
        req_count += 1
        meta = e.get("metadata", {})
        total_dur += int(meta.get("duration_ms", 0))
        status = int(meta.get("status", 0) or 0)
        if status >= 500:
            status_5xx += 1
        elif status >= 400:
            status_4xx += 1
    for e in parse_json_log_lines(error_lines):
        errors += 1
        error_types.add(e.get("message", ""))

    return {
        "requests": req_count,
        "avg_duration_ms": round(total_dur / req_count) if req_count else 0,
        "errors": errors,
        "error_types": len(error_types),
        "status_5xx": status_5xx,
        
        "status_4xx": status_4xx,
        "containers": len(containers),
        "containers_down": sum(1 for c in containers if "Up" not in c["status"]),
    }


# ─── helpers ─────────────────────────────────────────────────────

def _match(entry, search, cid):
    if cid and entry.get("correlation_id") != cid:
        return False
    if search and search.lower() not in json.dumps(entry).lower():
        return False
    return True


def _match_text(text, search, cid, entry_cid):
    if cid and entry_cid != cid:
        return False
    if search and search.lower() not in (text or "").lower():
        return False
    return True


def _to_line(entry, source):
    t = entry.get("time", "")
    return {
        "time": t,
        "time_short": t[11:19] if len(t) >= 19 else "",
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

        try:
            if path == "/":
                self._serve_html()
            elif path == "/api/summary":
                self._json(summary())
            elif path == "/api/access":
                self._json(access_entries(tail, search, cid))
            elif path == "/api/errors":
                self._json(error_groups(tail, search, cid))
            elif path == "/api/audit":
                self._json(audit_entries(tail, search, cid))
            elif path == "/api/all":
                self._json(all_logs(tail, search, cid))
            elif path == "/api/stats":
                self._json(docker_stats())
            elif path == "/api/docker-containers":
                self._json(docker_containers())
            elif path == "/api/docker-logs":
                container = get("container")
                if not container:
                    self._json({"error": "container param required"}, 400); return
                self._json(docker_logs(container, tail))
            elif path == "/api/docker-search":
                self._json(search_docker(tail, get("q")))
            else:
                self.send_error(404)
        except Exception as e:
            self._json({"error": str(e)}, 500)

    def _json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_html(self):
        html = HTML.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html)))
        self.end_headers()
        self.wfile.write(html)

    def log_message(self, fmt, *args):
        pass


# ─── HTML ────────────────────────────────────────────────────────

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>logdash</title>
<style>
:root{--bg:#0a0d12;--surface:#12161d;--surface2:#1a2029;--border:#262c37;--border-soft:#1c222b;--text:#d5dbe3;--muted:#7d8794;
  --blue:#58a6ff;--green:#3fb950;--yellow:#d29922;--red:#f85149;--purple:#bc8cff;
  --r-sm:6px;--r-md:9px;--r-lg:12px;--shadow-1:0 1px 3px rgba(0,0,0,.5);--shadow-2:0 4px 16px rgba(0,0,0,.35)}
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
html{scrollbar-color:var(--border) var(--bg)}
body{background:var(--bg);color:var(--text);font:12px/1.55 'JetBrains Mono','Fira Code',ui-monospace,monospace;min-height:100vh;display:flex;flex-direction:column;letter-spacing:.1px}
:focus-visible{outline:2px solid var(--blue);outline-offset:1px;border-radius:3px}
@media (prefers-reduced-motion:reduce){*{transition:none !important;animation:none !important}}

/* header */
.hdr{background:var(--surface);border-bottom:1px solid var(--border);padding:11px 18px;display:flex;align-items:center;gap:11px;flex-shrink:0;position:relative}
.hdr::after{content:'';position:absolute;left:0;right:0;bottom:-1px;height:2px;
  background:linear-gradient(90deg,var(--blue) 0 25%,var(--red) 25% 50%,var(--purple) 50% 75%,var(--green) 75% 100%);opacity:.55}
.hdr .dot{width:7px;height:7px;background:var(--green);border-radius:50%;box-shadow:0 0 0 3px #3fb95020;flex-shrink:0}
.hdr .dot.down{background:var(--red);box-shadow:0 0 0 3px #f8514920;animation:pulse 1.6s ease-in-out infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
.hdr h1{font-size:14px;font-weight:700;color:var(--text);letter-spacing:.3px;display:flex;align-items:baseline;gap:1px}
.hdr h1 .crt{color:var(--blue);font-weight:600;animation:blink 1.1s steps(1) infinite}
@keyframes blink{0%,49%{opacity:1}50%,100%{opacity:0}}
.hdr .sub{color:var(--muted);font-size:10px;margin-left:auto;white-space:nowrap;opacity:.85}

/* summary cards */
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(112px,1fr));gap:1px;background:var(--border);flex-shrink:0}
.card{background:var(--surface);padding:11px 14px 10px;text-align:center;transition:background .15s,transform .15s;border-top:2px solid transparent;position:relative}
.card[data-tone="b"]{border-top-color:#58a6ff55}
.card[data-tone="g"]{border-top-color:#3fb95055}
.card[data-tone="y"]{border-top-color:#d2992255}
.card[data-tone="r"]{border-top-color:#f8514955}
.card[data-tone="m"]{border-top-color:#8b949e55}
.card:hover{background:var(--surface2)}
.card .ic{font-size:10px;color:var(--muted);opacity:.55;margin-bottom:2px;letter-spacing:.5px}
.card .val{font-size:22px;font-weight:700;font-variant-numeric:tabular-nums;line-height:1.2}
.card .val.g{color:var(--green)}.card .val.y{color:var(--yellow)}.card .val.r{color:var(--red)}.card .val.b{color:var(--blue)}
.card .lbl{font-size:9px;color:var(--muted);margin-top:3px;text-transform:uppercase;letter-spacing:.7px}

/* tabs */
.tabs{background:var(--surface);border-bottom:1px solid var(--border);display:flex;padding:7px 14px;gap:3px;flex-shrink:0;overflow-x:auto}
.tabs button{background:none;color:var(--muted);border:1px solid transparent;padding:6px 14px;cursor:pointer;font:inherit;font-size:12px;border-radius:var(--r-sm);white-space:nowrap;transition:color .15s,background .15s,border-color .15s}
.tabs button:hover{color:var(--text);background:var(--surface2)}
.tabs button.active{color:var(--bg);background:var(--blue);font-weight:700}
.tabs button.active:hover{background:var(--blue)}
.tabs button .badge{background:var(--red);color:#fff;font-size:9px;padding:1px 5px;border-radius:8px;margin-left:5px;font-weight:700}
.tabs button.active .badge{background:#fff;color:var(--red)}

/* controls bar */
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

/* cid badge */
.cid-badge{background:#1f6feb14;border:1px solid #1f6feb44;color:var(--blue);padding:5px 9px;border-radius:var(--r-sm);font-size:11px;display:none;align-items:center;gap:7px}
.cid-badge .clr{cursor:pointer;color:var(--muted);font-weight:700;padding:0 2px;transition:color .15s}
.cid-badge .clr:hover{color:var(--red)}

/* content */
.main{flex:1;overflow:auto;padding:0}
.main::-webkit-scrollbar{width:10px}
.main::-webkit-scrollbar-thumb{background:var(--border);border-radius:5px}
.main::-webkit-scrollbar-thumb:hover{background:#3a4351}

/* table */
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

/* error groups */
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

/* stats table */
.stat-cont{display:flex;flex-direction:column;gap:7px;padding:14px 18px}
.stat-row{display:flex;align-items:center;gap:12px;padding:9px 14px;background:var(--surface);border-radius:var(--r-md);border:1px solid var(--border);transition:border-color .15s}
.stat-row:hover{border-color:#3a4351}
.stat-row .sn{width:180px;font-weight:700;flex-shrink:0;overflow:hidden;text-overflow:ellipsis}
.stat-row .sg{flex:1;display:flex;align-items:center;gap:10px}
.stat-bar-wrap{flex:1;height:7px;background:var(--bg);border-radius:4px;overflow:hidden}
.stat-bar{height:100%;border-radius:4px;transition:width .3s}
.stat-bar.cpu{background:linear-gradient(90deg,#3fb950,#58a6ff)}
.stat-bar.mem{background:linear-gradient(90deg,#d29922,#f85149)}
.stat-val{min-width:62px;text-align:right;font-size:11px;font-variant-numeric:tabular-nums;color:var(--muted);flex-shrink:0}
.stat-row .sx{color:var(--muted);font-size:10px;flex-shrink:0;min-width:90px;text-align:right}

/* docker */
.docker-topbar{display:flex;align-items:center;gap:8px;padding:9px 18px;border-bottom:1px solid var(--border);flex-wrap:wrap}
.docker-list{padding:2px 0}
.docker-entry{padding:6px 18px;border-bottom:1px solid var(--border-soft);white-space:pre-wrap;word-break:break-all;font-size:11px}
.docker-entry:hover{background:var(--surface)}
.docker-entry .ctn{color:var(--purple);margin-right:8px;font-weight:700}

/* all logs */
.log-row{display:flex;padding:6px 18px;border-bottom:1px solid var(--border-soft);gap:9px;align-items:flex-start;transition:background .1s}
.log-row:hover{background:var(--surface)}
.log-row .lv{width:46px;flex-shrink:0;font-size:9px;font-weight:700;text-align:center;padding:2px 4px;border-radius:3px}
.log-row .lv.INFO{background:#1f6feb1e;color:var(--blue)}.log-row .lv.ERROR{background:#f851491e;color:var(--red)}
.log-row .lv.WARN{background:#d299221e;color:var(--yellow)}.log-row .lv.DEBUG{background:#7d87941e;color:var(--muted)}
.log-row .ts{color:var(--muted);font-size:10px;flex-shrink:0;padding-top:1px}
.log-row .src{color:var(--purple);font-size:10px;flex-shrink:0;padding-top:1px;min-width:44px}
.log-row .msg{flex:1;word-break:break-all}

/* status bar */
.bar{background:var(--surface);border-top:1px solid var(--border);padding:7px 18px;font-size:10px;color:var(--muted);display:flex;gap:16px;flex-shrink:0}
.bar span:first-child::before{content:'●';color:var(--green);margin-right:6px;font-size:8px}

/* empty / loading states */
.loading{padding:56px 24px;text-align:center;color:var(--muted);position:relative}
.loading::before{content:'';display:block;width:20px;height:20px;margin:0 auto 14px;border-radius:50%;
  border:2px solid var(--border);border-top-color:var(--blue);animation:spin .7s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
.empty{margin:22px;padding:52px 24px;text-align:center;color:var(--muted);border:1px dashed var(--border);border-radius:var(--r-lg)}
.empty .big{font-size:20px;margin-bottom:7px;color:var(--text);font-weight:700}
.empty .hint{font-size:11px;margin-top:4px;opacity:.85}

/* responsive */
@media (max-width:820px){
  .cards{grid-template-columns:repeat(4,1fr)}
  .card .val{font-size:14px}
  .hdr .sub{display:none}
  .stat-row{flex-wrap:wrap}
  .stat-row .sn{width:100%}
}
</style>
</head>
<body>
<div class="hdr"><span class="dot" id="statusDot"></span><h1>logdash<span class="crt">_</span></h1><span class="sub">127.0.0.1:6060 &mdash; SSH only</span></div>
<div class="cards">
  <div class="card" data-tone="b"><div class="ic">req/s</div><div class="val b" id="sReq">-</div><div class="lbl">Requests</div></div>
  <div class="card" data-tone="g"><div class="ic">latency</div><div class="val g" id="sAvg">-</div><div class="lbl">Avg Duration</div></div>
  <div class="card" data-tone="r"><div class="ic">server</div><div class="val r" id="s5xx">-</div><div class="lbl">5xx</div></div>
  <div class="card" data-tone="y"><div class="ic">client</div><div class="val y" id="s4xx">-</div><div class="lbl">4xx</div></div>
  <div class="card" data-tone="g"><div class="ic">compute</div><div class="val g" id="sCpu">-</div><div class="lbl">CPU</div></div>
  <div class="card" data-tone="b"><div class="ic">memory</div><div class="val b" id="sMem">-</div><div class="lbl">Memory</div></div>
  <div class="card" data-tone="r"><div class="ic">error.log</div><div class="val r" id="sErr">-</div><div class="lbl">Log Errors</div></div>
  <div class="card" data-tone="m"><div class="ic">docker</div><div class="val" id="sCont">-</div><div class="lbl">Containers</div></div>
</div>
<div class="tabs">
  <button class="active" data-tab="access">Access</button>
  <button data-tab="errors">Errors<span class="badge" id="errBadge" style="display:none"></span></button>
  <button data-tab="audit">Audit</button>
  <button data-tab="docker">Docker</button>
  <button data-tab="stats">Stats</button>
  <button data-tab="all">All</button>
</div>
<div class="ctrl">
  <div class="search"><input type="text" id="searchInp" placeholder="Search across all logs..." oninput="onSearchDebounced()" style="width:100%"></div>
  <span class="cid-badge" id="cidBadge">CID <span id="cidText"></span><span class="clr" onclick="clearCid()" title="Clear trace">&times;</span></span>
  <label class="tail-lbl" for="tailInp">lines</label>
  <input type="number" class="tail-inp" id="tailInp" value="200" min="10" max="5000" onchange="onSearch()" title="Lines to fetch">
  <button class="refresh-btn" onclick="onSearch()">Refresh</button>
</div>
<div class="main" id="main">Loading...</div>
<div class="bar"><span id="statusText">Ready</span><span id="entryCount"></span></div>

<script>
let tab = 'access', sortCol = null, sortDir = 1, searchDebounce = null;

async function loadSummary() {
  try {
    const [r, s] = await Promise.all([
      fetch('/api/summary').then(x=>x.json()),
      fetch('/api/stats').then(x=>x.json()),
    ]);
    document.getElementById('sReq').textContent = r.requests;
    document.getElementById('sAvg').textContent = r.avg_duration_ms + 'ms';
    document.getElementById('s5xx').textContent = r.status_5xx;
    document.getElementById('s4xx').textContent = r.status_4xx;
    document.getElementById('sCpu').textContent = s.total_cpu + '%';
    document.getElementById('sMem').textContent = s.total_mem_str + ' / ' + s.host_mem_str;
    document.getElementById('sErr').textContent = r.errors;
    document.getElementById('sCont').textContent = r.containers;
    document.getElementById('statusDot').className = 'dot' + (r.containers_down > 0 ? ' down' : '');
    const badge = document.getElementById('errBadge');
    if (r.errors > 0) { badge.textContent = r.errors; badge.style.display = ''; }
    else badge.style.display = 'none';
  } catch(e) {}
}

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
  let p = 'tail=' + encodeURIComponent(tail);
  if (search) p += '&search=' + encodeURIComponent(search);
  if (cid) p += '&cid=' + encodeURIComponent(cid);
  return p;
}

function setCid(cid) {
  document.getElementById('cidBadge').style.display = 'flex';
  document.getElementById('cidText').textContent = cid;
  // Tracing a request means seeing it across every log source at once.
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
    case 'access': renderAccess(p); break;
    case 'errors': renderErrors(p); break;
    case 'audit': renderAudit(p); break;
    case 'stats': renderStats(); break;
    case 'docker': renderDocker(p); break;
    case 'all': renderAll(p); break;
  }
}

function esc(s) { return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }
function cidHtml(cid) { var c = String(cid||''); if (!c || c === '-') return '-'; return '<span class="cid" data-cid="' + esc(c) + '" title="Trace this request across all logs">' + esc(c.substring(0,12)) + '&hellip;</span>'; }
function statusClass(s) { if (s < 300) return 'st2'; if (s < 400) return 'st3'; return 'st4'; }

function emptyState(label, hint) {
  return '<div class="empty"><div class="big">' + esc(label) + '</div><div class="hint">' + esc(hint || '') + '</div></div>';
}

async function renderAccess(p) {
  const m = document.getElementById('main');
  m.innerHTML = '<div class="loading">Loading access log&hellip;</div>';
  try {
    const data = await (await fetch('/api/access?' + p)).json();
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
      html += '<td><span class="method">' + esc(e.method) + '</span></td>';
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

async function renderErrors(p) {
  const m = document.getElementById('main');
  m.innerHTML = '<div class="loading">Loading errors&hellip;</div>';
  try {
    const data = await (await fetch('/api/errors?' + p)).json();
    document.getElementById('entryCount').textContent = data.length + ' unique errors';
    if (!data.length) { m.innerHTML = emptyState('No errors &#127881;', 'Nothing matched in the current window.'); return; }
    let html = '<div class="err-list">';
    for (const e of data) {
      const c1 = e.count === 1 ? ' w1' : '';
      html += '<div class="err-row">';
      html += '<span class="count' + c1 + '">' + e.count + '</span>';
      html += '<div class="body">';
      html += '<div class="msg">' + esc(e.message) + '</div>';
      html += '<div class="meta"><span>' + esc(e.source) + '</span><span>&middot;</span><span>' + esc((e.first||'').substring(11,19));
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

async function renderAudit(p) {
  const m = document.getElementById('main');
  m.innerHTML = '<div class="loading">Loading audit log&hellip;</div>';
  try {
    const data = await (await fetch('/api/audit?' + p)).json();
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

async function renderAll(p) {
  const m = document.getElementById('main');
  m.innerHTML = '<div class="loading">Loading combined timeline&hellip;</div>';
  try {
    const data = await (await fetch('/api/all?' + p)).json();
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

async function renderStats() {
  const m = document.getElementById('main');
  m.innerHTML = '<div class="loading">Loading container stats&hellip;</div>';
  try {
    const data = await (await fetch('/api/stats')).json();
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

async function renderDocker(p) {
  const m = document.getElementById('main');
  m.innerHTML = '<div class="loading">Loading containers&hellip;</div>';
  try {
    const containers = await (await fetch('/api/docker-containers')).json();
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
    const data = await (await fetch(url)).json();
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

function th(label, col) {
  const arrow = sortCol === col ? '<span class="arrow">' + (sortDir === 1 ? ' \u25B2' : ' \u25BC') + '</span>' : '';
  return '<th data-sort="' + col + '">' + label + arrow + '</th>';
}

function doSort(col) {
  if (sortCol === col) sortDir *= -1; else { sortCol = col; sortDir = 1; }
  onSearch();
}

// ── event delegation (no inline onclick, no escaping bugs) ──

document.querySelector('.tabs').addEventListener('click', function(e) {
  var btn = e.target.closest('[data-tab]');
  if (btn) switchTab(btn.dataset.tab);
});

document.getElementById('main').addEventListener('click', function(e) {
  var cidEl = e.target.closest('[data-cid]');
  if (cidEl) { setCid(cidEl.dataset.cid); return; }
  var sortEl = e.target.closest('[data-sort]');
  if (sortEl) { doSort(sortEl.dataset.sort); return; }
  var errRow = e.target.closest('.err-row');
  if (errRow) { errRow.classList.toggle('expanded'); return; }
});

document.getElementById('main').addEventListener('change', function(e) {
  if (e.target.id === 'dockerSel') renderDockerFromSel();
});

loadSummary();
onSearch();
</script>
</body>
</html>"""


if __name__ == "__main__":
    server = http.server.ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"logdash → http://{HOST}:{PORT}")
    print("SSH tunnel:  ssh -L 6060:localhost:6060 app-backend@192.168.122.101")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutdown.")
        server.shutdown()