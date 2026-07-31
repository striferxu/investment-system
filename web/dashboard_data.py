#!/usr/bin/env python3
"""
投资系统 Web 看板 — 在本地启动 HTTP 服务
访问 http://localhost:8899 使用
"""
import json
import sys
import http.server
import os
import urllib.parse
from pathlib import Path
from datetime import date, datetime

# ---- 配置 ----
PORT = 8899
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "engine"))
from dimension_engine import DimensionEngine


def load_industry_names():
    try:
        with open(CONFIG_DIR / "industries.json") as f:
            cfg = json.load(f)
        return {ind["code"]: ind["name"] for ind in cfg["industries"]}
    except:
        return {}


def get_latest_data():
    """返回最新快照 + 推荐数据"""
    engine = DimensionEngine()
    ind_names = load_industry_names()

    # 找最新快照
    snap_dir = DATA_DIR / "snapshots"
    latest_snap = None
    if snap_dir.exists():
        snaps = sorted(snap_dir.glob("*.json"))
        if snaps:
            engine.load_snapshot(snaps[-1].stem)
            latest_snap = snaps[-1].stem

    # 找最新推荐
    rec_dir = DATA_DIR / "recommendations"
    latest_rec = None
    if rec_dir.exists():
        recs = sorted(rec_dir.glob("*.json"))
        if recs:
            with open(recs[-1]) as f:
                latest_rec = json.load(f)

    # 找学习日志
    learn_dir = DATA_DIR / "learning_log"
    learning = None
    if learn_dir.exists():
        logs = sorted(learn_dir.glob("*.json"))
        if logs:
            with open(logs[-1]) as f:
                learning = json.load(f)

    # 拼装
    macro_dims = []
    for dim_id, val in engine.macro_values.items():
        t = engine.dim_templates.get(dim_id, {})
        macro_dims.append({
            "id": dim_id,
            "name": t.get("name", dim_id),
            "value": round(val, 3),
            "range": t.get("range", [-1, 1]),
            "weight": round(engine.dim_weights.get(dim_id, 0), 4),
            "confidence": round(engine.dim_confidences.get(dim_id, 0), 3),
            "desc": t.get("desc", ""),
        })

    ind_dims = []
    for dim_id in ["I01", "I02", "I03", "I04", "I05"]:
        t = engine.dim_templates.get(dim_id, {})
        ind_dims.append({
            "id": dim_id,
            "name": t.get("name", dim_id),
            "weight": round(engine.dim_weights.get(dim_id, 0), 4),
            "confidence": round(engine.dim_confidences.get(dim_id, 0), 3),
            "desc": t.get("desc", ""),
        })

    # 行业排名
    industry_ranking = []
    for code, vals in engine.industry_values.items():
        score = engine.score_industry(code)
        name = ind_names.get(code, code)
        industry_ranking.append({
            "code": code,
            "name": name,
            "score": round(score, 4),
            "details": {k: round(v, 3) for k, v in vals.items()},
        })
    industry_ranking.sort(key=lambda x: x["score"], reverse=True)

    # 活跃事件（去重聚合）
    active_events = {}
    for imp in engine.active_event_impacts:
        key = imp["source"]
        if key not in active_events:
            active_events[key] = {
                "source": key,
                "remaining_days": imp["remaining_days"],
                "affected_count": 0,
            }
        active_events[key]["affected_count"] += 1

    return {
        "snapshot_date": latest_snap or "无",
        "recommendation": latest_rec or {},
        "macro_dimensions": macro_dims,
        "industry_dimensions": ind_dims,
        "industry_ranking": industry_ranking,
        "active_events": sorted(active_events.values(), key=lambda x: x["remaining_days"], reverse=True),
        "learning": learning or {},
        "dim_weights": {k: round(v, 4) for k, v in engine.dim_weights.items()},
        "dim_confidences": {k: round(v, 3) for k, v in engine.dim_confidences.items()},
    }


API_HANDLER_CODE = r"""
import json, sys
sys.path.insert(0, '.')
from web.dashboard_data import get_latest_data

class DashboardHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip('/')
        
        if path == '/api/data':
            self.send_json(get_latest_data())
        elif path == '/api/health':
            self.send_json({"status": "ok", "time": datetime.now().isoformat()})
        elif path == '/' or path == '':
            self.send_static('index.html')
        else:
            self.send_error(404, 'Not found')

    def send_json(self, data):
        body = json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_static(self, filename):
        static_dir = Path(__file__).resolve().parent / 'static'
        filepath = static_dir / filename
        if not filepath.exists():
            self.send_error(404, f'{filename} not found')
            return
        content = filepath.read_bytes()
        ctype = 'text/html; charset=utf-8' if filename.endswith('.html') else 'text/plain'
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format, *args):
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {args[0]} {args[1]} {args[2]}")

def run_server(port=8899):
    server = http.server.HTTPServer(('0.0.0.0', port), DashboardHandler)
    print(f"📊 投资系统看板 → http://localhost:{port}")
    print(f"   API: http://localhost:{port}/api/data")
    print(f"   按 Ctrl+C 停止\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n👋 服务器已停止")
        server.server_close()
"""

# Note: Python http.server module doesn't support BaseHTTPRequestHandler
# with imports reliably in __main__. Let me refactor to a single file server.

print("Designing the dashboard layout...")
