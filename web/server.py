#!/usr/bin/env python3
"""
📊 投资系统 Web 看板服务器
启动: python3 web/server.py
访问: http://localhost:8899
"""
import json
import http.server
import urllib.parse
import threading
import webbrowser
from pathlib import Path
from datetime import datetime

# 路径
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
CONFIG_DIR = BASE_DIR / "config"
PORT = 8899

# ---- Fast embed the engine ----
import sys
sys.path.insert(0, str(BASE_DIR / "engine"))
from dimension_engine import DimensionEngine


def load_industry_names():
    try:
        with open(CONFIG_DIR / "industries.json") as f:
            cfg = json.load(f)
        return {ind["code"]: ind["name"] for ind in cfg["industries"]}
    except Exception:
        return {}


def get_api_data():
    """组装所有看板数据"""
    engine = DimensionEngine()
    ind_names = load_industry_names()

    # 最新快照
    latest_date = None
    snap_dir = DATA_DIR / "snapshots"
    if snap_dir.exists():
        snaps = sorted(snap_dir.glob("*.json"))
        if snaps:
            engine.load_snapshot(snaps[-1].stem)
            latest_date = snaps[-1].stem

    # 最新推荐
    rec = {}
    rec_dir = DATA_DIR / "recommendations"
    if rec_dir.exists():
        recs = sorted(rec_dir.glob("*.json"))
        if recs:
            with open(recs[-1]) as f:
                rec = json.load(f)

    # 最新学习日志
    learning = {}
    learn_dir = DATA_DIR / "learning_log"
    if learn_dir.exists():
        logs = sorted(learn_dir.glob("*.json"))
        if logs:
            with open(logs[-1]) as f:
                learning = json.load(f)

    # 最新微观层数据
    stock_vals = {}
    stock_dir = DATA_DIR / "stock_values"
    if stock_dir.exists():
        svs = sorted(stock_dir.glob("*.json"))
        if svs:
            with open(svs[-1]) as f:
                stock_vals = json.load(f)

    # 宏观维度
    macro_dims = []
    for dim_id in ["M01", "M02", "M03", "M04"]:
        t = engine.dim_templates.get(dim_id, {})
        macro_dims.append({
            "id": dim_id,
            "name": t.get("name", dim_id),
            "value": round(engine.macro_values.get(dim_id, 0), 3),
            "weight": round(engine.dim_weights.get(dim_id, 0), 4),
            "confidence": round(engine.dim_confidences.get(dim_id, 0), 3),
            "desc": t.get("desc", ""),
        })

    # 行业层维度 (带权重/置信度)
    ind_dim_config = []
    for dim_id in ["I01", "I02", "I03", "I04", "I05"]:
        t = engine.dim_templates.get(dim_id, {})
        ind_dim_config.append({
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

    # 活跃事件（聚合）
    events = {}
    for imp in engine.active_event_impacts:
        src = imp["source"]
        if src not in events:
            events[src] = {"source": src, "remaining_days": imp["remaining_days"], "count": 0}
        events[src]["count"] += 1
        events[src]["remaining_days"] = max(events[src]["remaining_days"], imp["remaining_days"])

    return {
        "date": latest_date or "无",
        "recommendation": {
            "market_view": rec.get("market_view", "—"),
            "macro_score": rec.get("macro_score", 0),
            "position_limit": rec.get("position_limit", 1.0),
            "industries": [
                {
                    "code": ind["industry_code"],
                    "name": ind_names.get(ind["industry_code"], ind["industry_code"]),
                    "score": round(ind["score"], 4),
                    "details": {k: round(v, 3) for k, v in ind.get("details", {}).items()},
                }
                for ind in rec.get("recommended_industries", [])
            ],
        },
        "macro": macro_dims,
        "industry_config": ind_dim_config,
        "industry_ranking": industry_ranking[:10],
        "active_events": sorted(events.values(), key=lambda x: x["remaining_days"], reverse=True),
        "learning": {
            "accuracy": round(learning.get("accuracy", 0) * 100, 1) if learning.get("accuracy") else None,
            "adjustments": learning.get("adjustments", {}),
        },
        "weights": {k: round(v, 4) for k, v in engine.dim_weights.items()},
        "confidences": {k: round(v, 3) for k, v in engine.dim_confidences.items()},
        "stock_layer": {
            "active": bool(stock_vals),
            "industries": [
                {
                    "code": code,
                    "name": ind_names.get(code, code),
                    "values": {k: round(v, 3) for k, v in dims.items()},
                }
                for code, dims in stock_vals.items()
            ],
        },
    }


# ---- HTML 看板（内嵌） ----
HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>投资系统看板</title>
<style>
:root { --bg: #0f1119; --card: #1a1d2e; --border: #2a2d3e; --text: #e0e2f0; --muted: #7a7d8e; --accent: #60a5fa; --green: #34d399; --red: #f87171; --yellow: #fbbf24; }
* { margin:0; padding:0; box-sizing:border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif; background: var(--bg); color: var(--text); padding: 20px; min-height: 100vh; }
.container { max-width: 1400px; margin: 0 auto; }

/* Header */
.header { display:flex; justify-content:space-between; align-items:center; margin-bottom:24px; flex-wrap:wrap; gap:12px; }
.header h1 { font-size:22px; font-weight:600; background: linear-gradient(135deg, #60a5fa, #a78bfa); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
.header-right { display:flex; align-items:center; gap:16px; font-size:13px; color:var(--muted); }
.badge { padding:4px 12px; border-radius:20px; font-size:12px; font-weight:500; }
.badge-green { background:#065f4622; color:var(--green); border:1px solid var(--green); }
.badge-yellow { background:#b4530922; color:var(--yellow); border:1px solid var(--yellow); }
.badge-red { background:#dc262622; color:var(--red); border:1px solid var(--red); }
.badge-blue { background:#3b82f622; color:var(--accent); border:1px solid var(--accent); }

/* Grid */
.grid { display:grid; gap:16px; }
.grid-3 { grid-template-columns: repeat(3,1fr); }
.grid-2 { grid-template-columns: repeat(2,1fr); }
@media (max-width:900px) { .grid-3 { grid-template-columns:1fr; } .grid-2 { grid-template-columns:1fr; } }

/* Cards */
.card { background:var(--card); border:1px solid var(--border); border-radius:12px; padding:20px; }
.card-title { font-size:13px; font-weight:600; color:var(--muted); text-transform:uppercase; letter-spacing:0.5px; margin-bottom:16px; display:flex; justify-content:space-between; align-items:center; }
.stat-value { font-size:32px; font-weight:700; line-height:1.2; }
.stat-label { font-size:12px; color:var(--muted); margin-top:4px; }
.stat-bar { height:6px; border-radius:3px; background:var(--border); margin-top:12px; overflow:hidden; }
.stat-bar-fill { height:100%; border-radius:3px; transition:width 0.5s; }

/* Dimension bars */
.dim-item { display:flex; justify-content:space-between; align-items:center; padding:8px 0; border-bottom:1px solid #ffffff08; font-size:13px; }
.dim-item:last-child { border-bottom:0; }
.dim-label { display:flex; gap:4px; align-items:center; }
.dim-code { color:var(--muted); font-size:11px; }
.dim-value { font-weight:600; }
.dim-sub { font-size:11px; color:var(--muted); margin-top:2px; }

/* Industry ranking */
.industry-row { display:flex; justify-content:space-between; align-items:center; padding:10px 0; border-bottom:1px solid #ffffff08; }
.industry-row:first-child { padding-top:0; }
.industry-row:last-child { border-bottom:0; padding-bottom:0; }
.rank-num { width:24px; height:24px; border-radius:6px; display:flex; align-items:center; justify-content:center; font-size:11px; font-weight:700; background:var(--border); }
.rank-1 { background:#fbbf2433; color:var(--yellow); }
.rank-2 { background:#94a3b833; color:#94a3b8; }
.rank-3 { background:#b4530933; color:#d97706; }
.industry-name { flex:1; margin-left:12px; font-size:14px; font-weight:500; }
.industry-score { font-size:14px; font-weight:600; }
.detail-chips { display:flex; gap:4px; margin-top:6px; flex-wrap:wrap; }
.chip { padding:2px 8px; border-radius:4px; font-size:10px; background:var(--border); color:var(--muted); }
.chip-pos { background:#065f4622; color:var(--green); }
.chip-neg { background:#dc262622; color:var(--red); }

/* Table */
.info-table { width:100%; font-size:13px; border-collapse:collapse; }
.info-table td { padding:8px 4px; border-bottom:1px solid #ffffff08; }
.info-table td:last-child { text-align:right; font-weight:600; }
.info-table tr:last-child td { border-bottom:0; }

/* Event list */
.event-item { display:flex; justify-content:space-between; padding:8px 0; border-bottom:1px solid #ffffff08; font-size:13px; }
.event-item:last-child { border-bottom:0; }

/* Loading */
#loading { display:flex; justify-content:center; align-items:center; height:60vh; }
.spinner { width:40px; height:40px; border:3px solid var(--border); border-top:3px solid var(--accent); border-radius:50%; animation:spin 0.8s linear infinite; }
@keyframes spin { to { transform:rotate(360deg); } }

/* Refresh button */
.refresh-btn { background:var(--card); border:1px solid var(--border); color:var(--text); padding:6px 14px; border-radius:8px; cursor:pointer; font-size:13px; transition:0.2s; }
.refresh-btn:hover { background:var(--border); }

/* Event source tag */
.source-tag { font-size:11px; padding:2px 8px; border-radius:4px; background:var(--border); }
</style>
</head>
<body>

<div class="container" id="app">
  <div id="loading"><div class="spinner"></div></div>
</div>

<script>
async function loadData() {
  try {
    const res = await fetch('/api/data');
    return await res.json();
  } catch(e) {
    return null;
  }
}

function scoreColor(v) {
  if (v > 0.2) return 'var(--green)';
  if (v < -0.1) return 'var(--red)';
  return 'var(--yellow)';
}

function scoreBar(v, lo=-1, hi=1) {
  const pct = ((v - lo) / (hi - lo)) * 100;
  const color = scoreColor(v);
  return `<div class="stat-bar"><div class="stat-bar-fill" style="width:${Math.max(2,Math.min(98,pct))}%;background:${color}"></div></div>`;
}

function chip(v) {
  if (v === 0) return '';
  const cls = v > 0 ? 'chip-pos' : 'chip-neg';
  return `<span class="chip ${cls}">${v > 0 ? '+' : ''}${v.toFixed(2)}</span>`;
}

function render(data) {
  const r = data.recommendation;
  const macro = data.macro;
  const icfg = data.industry_config;
  const ranking = data.industry_ranking;
  const events = data.active_events;
  const learn = data.learning;

  const viewColor = r.market_view === '看多' ? 'badge-green' : r.market_view === '谨慎' ? 'badge-red' : 'badge-yellow';
  const posPct = Math.round(r.position_limit * 100);

  // Market overview card
  const marketCard = `
    <div class="card">
      <div class="card-title">市场总览</div>
      <div style="display:flex;gap:24px;flex-wrap:wrap;">
        <div>
          <div class="stat-value" style="color:${scoreColor(r.macro_score)}">${r.macro_score.toFixed(3)}</div>
          <div class="stat-label">宏观评分</div>
          ${scoreBar(r.macro_score)}
        </div>
        <div>
          <div class="stat-value">${r.market_view}</div>
          <div class="stat-label">市场判断 <span class="badge ${viewColor}" style="margin-left:6px">${posPct}%仓位</span></div>
        </div>
        <div>
          <div class="stat-value" style="color:${learn.accuracy != null ? 'var(--green)' : 'var(--muted)'}">${learn.accuracy != null ? learn.accuracy + '%' : '—'}</div>
          <div class="stat-label">预测准确率</div>
        </div>
      </div>
    </div>`;

  // Macro dimensions
  const macroItems = macro.map(d => {
    const c = scoreColor(d.value);
    return `<div class="dim-item">
      <div class="dim-label"><span class="dim-code">${d.id}</span> ${d.name}</div>
      <div>
        <span class="dim-value" style="color:${c}">${d.value >= 0 ? '+' : ''}${d.value.toFixed(3)}</span>
        <span style="font-size:11px;color:var(--muted);margin-left:8px">w:${d.weight} c:${d.confidence}</span>
      </div>
    </div>`;
  }).join('');

  const macroCard = `
    <div class="card">
      <div class="card-title">宏观维度</div>
      ${macroItems}
    </div>`;

  // Industry dimensions config
  const indCfgItems = icfg.map(d => `
    <div class="dim-item">
      <div class="dim-label"><span class="dim-code">${d.id}</span> ${d.name}</div>
      <div><span style="font-size:12px">w:${d.weight} c:${d.confidence}</span></div>
    </div>`).join('');
  const indCfgCard = `<div class="card"><div class="card-title">行业维度权重</div>${indCfgItems}</div>`;

  // Active events
  const eventItems = events.length ? events.map(e => `
    <div class="event-item">
      <span><span class="source-tag">${e.source}</span> <span style="font-size:12px;color:var(--muted)">影响 ${e.count} 个维度</span></span>
      <span style="font-size:12px;color:var(--muted)">剩余 ${e.remaining_days} 天</span>
    </div>`).join('') : '<div style="color:var(--muted);font-size:13px;padding:8px 0">暂无活跃事件</div>';
  const eventCard = `<div class="card"><div class="card-title">活跃事件影响</div>${eventItems}</div>`;

  // Industry ranking
  const rankItems = ranking.slice(0, 10).map((ind, i) => {
    const rankCls = i === 0 ? 'rank-1' : i === 1 ? 'rank-2' : i === 2 ? 'rank-3' : '';
    const details = Object.entries(ind.details).map(([k,v]) => chip(v)).filter(Boolean).join('');
    return `<div class="industry-row">
      <span class="rank-num ${rankCls}">${i+1}</span>
      <div class="industry-name">
        ${ind.name}
        <span style="font-size:11px;color:var(--muted);margin-left:6px">${ind.code}</span>
        ${details ? `<div class="detail-chips">${details}</div>` : ''}
      </div>
      <span class="industry-score" style="color:${scoreColor(ind.score)}">${ind.score.toFixed(4)}</span>
    </div>`;
  }).join('');

  const rankCard = `
    <div class="card">
      <div class="card-title">行业排名 <span style="font-size:11px;font-weight:400">TOP ${Math.min(10, ranking.length)}</span></div>
      ${rankItems}
    </div>`;

  // Recommended industries (from recommendation)
  const recItems = r.industries.map((ind, i) => {
    const details = Object.entries(ind.details).map(([k,v]) => chip(v)).filter(Boolean).join('');
    const rankCls = i === 0 ? 'rank-1' : i === 1 ? 'rank-2' : i === 2 ? 'rank-3' : '';
    return `<div class="industry-row">
      <span class="rank-num ${rankCls}">${i+1}</span>
      <div class="industry-name">${ind.name}${details ? `<div class="detail-chips">${details}</div>` : ''}</div>
      <span class="industry-score" style="color:${scoreColor(ind.score)}">${ind.score.toFixed(4)}</span>
    </div>`;
  }).join('');
  const recCard = `<div class="card"><div class="card-title">今日推荐</div>${recItems}</div>`;

  // Learning log
  const adjustItems = Object.entries(learn.adjustments || {}).slice(0, 10).map(([k,v]) => `
    <div class="dim-item">
      <span class="dim-label">${k}</span>
      <span style="color:${v > 0 ? 'var(--green)' : 'var(--red)'}">${v > 0 ? '+' : ''}${v.toFixed(6)}</span>
    </div>`).join('');
  const learnCard = `<div class="card"><div class="card-title">最近权重调整</div>${adjustItems || '<div style="color:var(--muted);font-size:13px;padding:8px 0">暂无调整记录</div>'}</div>`;

  // 🔬 微观层（S01~S04）
  const stockLayer = data.stock_layer;
  let stockCard = '';
  if (stockLayer && stockLayer.active) {
    const stockRows = stockLayer.industries.slice(0, 8).map(ind => {
      const v = ind.values;
      const chips = ['S01','S02','S03','S04'].map(k => {
        const val = v[k] || 0;
        const cls = val > 0.1 ? 'chip-pos' : val < -0.1 ? 'chip-neg' : '';
        return `<span class="chip ${cls}">${k}:${val >= 0 ? '+' : ''}${val.toFixed(3)}</span>`;
      }).join('');
      return `<div class="industry-row">
        <div class="industry-name" style="font-size:12px">${ind.name}<span style="color:var(--muted);margin-left:4px">${ind.code}</span></div>
        <div class="detail-chips">${chips}</div>
      </div>`;
    }).join('');
    stockCard = `<div class="card"><div class="card-title">🔬 微观层(S01~S04) <span style="font-size:11px;font-weight:400;color:var(--green)">● 已激活</span></div>${stockRows}</div>`;
  } else {
    stockCard = `<div class="card"><div class="card-title">🔬 微观层(S01~S04) <span style="font-size:11px;font-weight:400;color:var(--muted)">○ 未激活</span></div><div style="color:var(--muted);font-size:13px;padding:8px 0">等待定时任务采集ETF数据后自动激活</div></div>`;
  }

  // Assemble
  document.getElementById('app').innerHTML = `
    <div class="header">
      <h1>📊 投资分析系统</h1>
      <div class="header-right">
        <span>🕐 ${data.date || '—'}</span>
        <button class="refresh-btn" onclick="location.reload()">🔄 刷新</button>
      </div>
    </div>
    <div class="grid grid-3">
      ${marketCard}
      ${recCard}
      ${macroCard}
    </div>
    <div style="margin-top:16px">
      <div class="grid grid-2">
        ${rankCard}
        <div class="grid" style="gap:16px">
          ${indCfgCard}
          ${eventCard}
          ${learnCard}
        </div>
      </div>
    </div>
    <!-- 🔬 微观层 -->
    <div style="margin-top:16px">
      ${stockCard}
    </div>
    <div style="margin-top:16px;text-align:center;font-size:12px;color:var(--muted);padding:20px">
      小艺投资分析系统 v1.0 · 数据自动更新
    </div>
  `;
}

// Initial load
loadData().then(data => {
  if (data) render(data);
  else document.getElementById('app').innerHTML = '<div style="text-align:center;padding:60px;color:var(--muted)">❌ 无法加载数据</div>';
});

// Auto refresh every 5 minutes
setInterval(async () => {
  const data = await loadData();
  if (data) render(data);
}, 300000);
</script>
</body>
</html>"""


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip('/')

        if path == '/api/data':
            self._send_json(get_api_data())
        elif path == '/api/health':
            self._send_json({"status": "ok", "time": datetime.now().isoformat()})
        elif path in ('/', ''):
            self._send_html()
        else:
            self.send_error(404, "Not found")

    def _send_json(self, obj):
        body = json.dumps(obj, ensure_ascii=False, indent=2).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self):
        body = HTML.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        print(f"  [{datetime.now().strftime('%H:%M:%S')}] {args[0]} {args[1]} {args[2]}")


def run():
    server = http.server.HTTPServer(('0.0.0.0', PORT), Handler)
    print(f"\n{'='*50}")
    print(f"  📊 投资系统看板")
    print(f"  {'='*50}")
    print(f"  本地访问: http://localhost:{PORT}")
    print(f"  API 接口: http://localhost:{PORT}/api/data")
    print(f"  内网访问: http://<服务器IP>:{PORT}")
    print(f"  {'='*50}")
    print(f"  按 Ctrl+C 停止\n")

    # Try to open browser
    try:
        webbrowser.open(f'http://localhost:{PORT}')
    except:
        pass

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n👋 服务器已停止")
        server.server_close()


if __name__ == '__main__':
    run()
