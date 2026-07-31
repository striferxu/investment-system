#!/usr/bin/env python3
"""
生成静态 HTML 看板（所有数据内嵌，无需 API 服务）
"""
import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
CONFIG_DIR = BASE_DIR / "config"

import sys
sys.path.insert(0, str(BASE_DIR / "engine"))
from dimension_engine import DimensionEngine


def load_industry_names():
    try:
        with open(CONFIG_DIR / "industries.json") as f:
            cfg = json.load(f)
        return {ind["code"]: ind["name"] for ind in cfg["industries"]}
    except:
        return {}


def build_standalone_html():
    engine = DimensionEngine()
    ind_names = load_industry_names()

    # 最新快照
    snap_dir = DATA_DIR / "snapshots"
    if snap_dir.exists():
        snaps = sorted(snap_dir.glob("*.json"))
        if snaps:
            engine.load_snapshot(snaps[-1].stem)

    # 最新推荐
    rec = {}
    rec_dir = DATA_DIR / "recommendations"
    if rec_dir.exists():
        recs = sorted(rec_dir.glob("*.json"))
        if recs:
            with open(recs[-1]) as f:
                rec = json.load(f)

    # 最新学习日志 + 历史准确率序列
    learning = {}
    accuracy_history = []
    learn_dir = DATA_DIR / "learning_log"
    if learn_dir.exists():
        logs = sorted(learn_dir.glob("*.json"))
        if logs:
            with open(logs[-1]) as f:
                learning = json.load(f)
            # 加载所有日志构建准确率趋势
            for lp in logs:
                try:
                    with open(lp) as f:
                        log_entry = json.load(f)
                    acc = log_entry.get("accuracy", 0)
                    dt = log_entry.get("date", lp.stem)
                    accuracy_history.append({"date": dt, "accuracy": round(acc * 100, 1)})
                except:
                    pass
            accuracy_history.sort(key=lambda x: x["date"])

    # 聚合数据
    macro = []
    for dim_id in ["M01","M02","M03","M04"]:
        t = engine.dim_templates.get(dim_id, {})
        macro.append({
            "id": dim_id, "name": t.get("name", dim_id),
            "value": round(engine.macro_values.get(dim_id, 0), 3),
            "weight": round(engine.dim_weights.get(dim_id, 0), 4),
            "confidence": round(engine.dim_confidences.get(dim_id, 0), 3),
            "desc": t.get("desc", ""),
        })

    # 行业排名
    ranking = []
    for code, vals in engine.industry_values.items():
        score = engine.score_industry(code)
        name = ind_names.get(code, code)
        ranking.append({
            "code": code, "name": name, "score": round(score, 4),
            "details": {k: round(v, 3) for k, v in vals.items()},
        })
    ranking.sort(key=lambda x: x["score"], reverse=True)

    # 活跃事件
    events = {}
    for imp in engine.active_event_impacts:
        src = imp["source"]
        if src not in events:
            events[src] = {"source": src, "remaining": imp["remaining_days"], "count": 0}
        events[src]["count"] += 1
        events[src]["remaining"] = max(events[src]["remaining"], imp["remaining_days"])

    # 行业维度配置
    ind_cfg = []
    for dim_id in ["I01","I02","I03","I04","I05"]:
        t = engine.dim_templates.get(dim_id, {})
        ind_cfg.append({
            "id": dim_id, "name": t.get("name", dim_id),
            "weight": round(engine.dim_weights.get(dim_id, 0), 4),
            "confidence": round(engine.dim_confidences.get(dim_id, 0), 3),
        })

    # 推荐行业
    rec_inds = []
    for ind in rec.get("recommended_industries", []):
        entry = {
            "code": ind["industry_code"],
            "name": ind_names.get(ind["industry_code"], ind["industry_code"]),
            "score": round(ind["score"], 4),
            "details": {k: round(v, 3) for k, v in ind.get("details", {}).items()},
        }
        if "stock_details" in ind:
            entry["stock"] = {k: round(v, 3) for k, v in ind["stock_details"].items()}
        rec_inds.append(entry)

    # 微观层数据
    stock_vals = {}
    stock_dir = DATA_DIR / "stock_values"
    if stock_dir.exists():
        svs = sorted(stock_dir.glob("*.json"))
        if svs:
            with open(svs[-1]) as f:
                stock_vals = json.load(f)
    stock_layer_active = bool(stock_vals)
    stock_data = []
    for code, dims in stock_vals.items():
        name = ind_names.get(code, code)
        stock_data.append({
            "code": code, "name": name,
            "S01": round(dims.get("S01", 0), 3),
            "S02": round(dims.get("S02", 0), 3),
            "S03": round(dims.get("S03", 0), 3),
            "S04": round(dims.get("S04", 0), 3),
        })

    data = {
        "date": (snaps[-1].stem if snap_dir.exists() and snaps else "无"),
        "market_view": rec.get("market_view", "—"),
        "position_limit": rec.get("position_limit", 1.0),
        "macro_score": round(rec.get("macro_score", 0), 3),
        "accuracy": round(learning.get("accuracy", 0) * 100, 1) if learning.get("accuracy") else None,
        "macro": macro,
        "ind_cfg": ind_cfg,
        "ranking": ranking[:10],
        "rec_inds": rec_inds,
        "events": sorted(events.values(), key=lambda x: x["remaining"], reverse=True),
        "adjustments": learning.get("adjustments", {}),
        "accuracy_history": accuracy_history,
        "stock_active": stock_layer_active,
        "stock_data": stock_data,
    }

    data_json = json.dumps(data, ensure_ascii=False)

    HTML_TEMPLATE = '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0,maximum-scale=1.0">
<title>投资系统看板</title>
<style>
:root{--bg:#0f1119;--card:#1a1d2e;--border:#2a2d3e;--text:#e0e2f0;--muted:#7a7d8e;--accent:#60a5fa;--green:#34d399;--red:#f87171;--yellow:#fbbf24}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',system-ui,sans-serif;background:var(--bg);color:var(--text);padding:16px;min-height:100vh;-webkit-font-smoothing:antialiased}
.container{max-width:600px;margin:0 auto}

/* header */
.hdr{display:flex;justify-content:space-between;align-items:center;margin-bottom:16px}
.hdr h1{font-size:18px;font-weight:600;background:linear-gradient(135deg,#60a5fa,#a78bfa);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.hdr .date{font-size:12px;color:var(--muted)}
.badge{padding:3px 10px;border-radius:20px;font-size:11px;font-weight:500;display:inline-block}
.bg-green{background:#065f4622;color:var(--green);border:1px solid var(--green)}
.bg-yellow{background:#b4530922;color:var(--yellow);border:1px solid var(--yellow)}
.bg-red{background:#dc262622;color:var(--red);border:1px solid var(--red)}

/* cards */
.card{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:14px;margin-bottom:12px}
.ct{font-size:11px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:0.5px;margin-bottom:10px;display:flex;justify-content:space-between;align-items:center}

/* stats row */
.sr{display:flex;gap:12px}
.sr-item{flex:1;text-align:center;padding:8px 0}
.sr-v{font-size:22px;font-weight:700;line-height:1.2}
.sr-l{font-size:11px;color:var(--muted);margin-top:2px}
.sr-bar{height:4px;border-radius:2px;background:var(--border);margin-top:8px;overflow:hidden}
.sr-bar-f{height:100%;border-radius:2px;transition:width 0.5s}

/* dim list */
.dl-item{display:flex;justify-content:space-between;align-items:center;padding:6px 0;border-bottom:1px solid #ffffff08;font-size:12px}
.dl-item:last-child{border-bottom:none}
.dl-code{color:var(--muted);font-size:10px}
.dl-val{font-weight:600;font-size:13px}

/* ranking */
.r-item{display:flex;align-items:center;padding:8px 0;border-bottom:1px solid #ffffff08}
.r-item:last-child{border-bottom:none}
.r-num{width:20px;height:20px;border-radius:5px;display:flex;align-items:center;justify-content:center;font-size:10px;font-weight:700;background:var(--border);flex-shrink:0}
.r1{background:#fbbf2433;color:var(--yellow)}
.r2{background:#94a3b833;color:#94a3b8}
.r3{background:#b4530933;color:#d97706}
.r-name{flex:1;margin-left:10px;font-size:13px;font-weight:500}
.r-name .code{font-size:10px;color:var(--muted);margin-left:4px;font-weight:400}
.r-score{font-size:13px;font-weight:600;flex-shrink:0}

/* chips */
.chips{display:flex;gap:3px;margin-top:3px;flex-wrap:wrap}
.chip{padding:1px 6px;border-radius:3px;font-size:9px;background:var(--border);color:var(--muted)}
.cp{background:#065f4622;color:var(--green)}
.cn{background:#dc262622;color:var(--red)}

/* event */
.e-item{display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid #ffffff08;font-size:12px}
.e-item:last-child{border-bottom:none}
.e-tag{font-size:10px;padding:1px 6px;border-radius:3px;background:var(--border)}

/* dim weight */
.dw-item{display:flex;justify-content:space-between;padding:5px 0;font-size:12px;border-bottom:1px solid #ffffff08}
.dw-item:last-child{border-bottom:none}
.dw-item .sub{color:var(--muted);font-size:11px}

/* footer */
.footer{text-align:center;font-size:11px;color:var(--muted);padding:16px 0}
</style>
</head>
<body>
<div class="container" id="app"></div>
<script>
const DATA = ''' + data_json + ''';

function sc(v){
  if(v>0.2)return 'var(--green)';
  if(v<-0.1)return 'var(--red)';
  return 'var(--yellow)';
}
function sbar(v,lo=-1,hi=1){
  const p=Math.max(2,Math.min(98,((v-lo)/(hi-lo))*100));
  return `<div class="sr-bar"><div class="sr-bar-f" style="width:${p}%;background:${sc(v)}"></div></div>`;
}
function chip(v){
  if(v===0)return '';
  return `<span class="chip ${v>0?'cp':'cn'}">${v>0?'+':''}${v.toFixed(2)}</span>`;
}

function render(d){
  const vc = d.market_view==='看多'?'bg-green':d.market_view==='谨慎'?'bg-red':'bg-yellow';
  const pp = Math.round(d.position_limit*100);
  const ac = d.accuracy != null ? d.accuracy+'%' : '—';

  // 准确率趋势图
  let accChartHtml = '';
  if (d.accuracy_history && d.accuracy_history.length >= 2) {
    accChartHtml = `<div class="card" id="accCard"><div class="ct">📈 预测准确率趋势 <span style="font-weight:400;font-size:10px;color:var(--muted)">最近${d.accuracy_history.length}天</span></div><canvas id="accChart" style="width:100%;height:120px;display:block"></canvas></div>`;
  }

  // Top cards
  let html = `<div class="hdr"><h1>📊 投资系统</h1><span class="date">${d.date}</span></div>`;
  html += `<div class="card"><div class="ct">市场总览</div><div class="sr">`;
  html += `<div class="sr-item"><div class="sr-v" style="color:${sc(d.macro_score)}">${d.macro_score}</div><div class="sr-l">宏观评分</div>${sbar(d.macro_score)}</div>`;
  html += `<div class="sr-item"><div class="sr-v">${d.market_view}</div><div class="sr-l">市场判断 <span class="badge ${vc}" style="margin-left:4px">${pp}%</span></div></div>`;
  html += `<div class="sr-item"><div class="sr-v" style="color:${d.accuracy? 'var(--green)':'var(--muted)'}">${ac}</div><div class="sr-l">预测准确率</div></div>`;
  html += `</div></div>`;

  html += accChartHtml;

  // Recommended
  const ri = d.rec_inds.map((ind,i)=>`<div class="r-item"><span class="r-num ${i===0?'r1':i===1?'r2':i===2?'r3':''}">${i+1}</span><div class="r-name">${ind.name}${Object.entries(ind.details).map(([k,v])=>chip(v)).filter(Boolean).join('')?'<div class="chips">'+Object.entries(ind.details).map(([k,v])=>chip(v)).filter(Boolean).join('')+'</div>':''}</div><span class="r-score" style="color:${sc(ind.score)}">${ind.score.toFixed(4)}</span></div>`).join('');
  html += `<div class="card"><div class="ct">今日推荐 <span style="font-weight:400;font-size:10px">TOP${d.rec_inds.length}</span></div>${ri}</div>`;

  // Macro
  const mi = d.macro.map(m=>`<div class="dl-item"><div class="dl-label"><span class="dl-code">${m.id}</span> ${m.name}</div><div><span class="dl-val" style="color:${sc(m.value)}">${m.value>=0?'+':''}${m.value.toFixed(3)}</span></div></div>`).join('');
  html += `<div class="card"><div class="ct">宏观维度</div>${mi}</div>`;

  // Ranking
  const rk = d.ranking.map((ind,i)=>{
    const rc = i===0?'r1':i===1?'r2':i===2?'r3':'';
    const ch = Object.entries(ind.details).map(([k,v])=>chip(v)).filter(Boolean).join('');
    return `<div class="r-item"><span class="r-num ${rc}">${i+1}</span><div class="r-name">${ind.name} <span class="code">${ind.code}</span>${ch?'<div class="chips">'+ch+'</div>':''}</div><span class="r-score" style="color:${sc(ind.score)}">${ind.score.toFixed(4)}</span></div>`;
  }).join('');
  html += `<div class="card"><div class="ct">行业排名 <span style="font-weight:400;font-size:10px">TOP${d.ranking.length}</span></div>${rk}</div>`;

  // Events
  const ev = d.events.map(e=>`<div class="e-item"><span><span class="e-tag">${e.source}</span> <span style="color:var(--muted);font-size:11px">${e.count}项</span></span><span style="color:var(--muted);font-size:11px">剩余${e.remaining}天</span></div>`).join('');
  html += `<div class="card"><div class="ct">活跃事件</div>${ev||'<div style="color:var(--muted);font-size:12px">暂无</div>'}</div>`;

  // Weights
  const ws = d.ind_cfg.map(c=>`<div class="dw-item"><span><span class="dl-code">${c.id}</span> ${c.name}</span><span>w:${c.weight.toFixed(4)} <span class="sub">c:${c.confidence}</span></span></div>`).join('');
  html += `<div class="card"><div class="ct">行业维度权重</div>${ws}</div>`;

  // 🔬 微观层
  if (d.stock_active) {
    const sl = d.stock_data.slice(0, 8).map(ind => {
      const ch = ['S01','S02','S03','S04'].map(k => {
        const v = ind[k] || 0;
        const cls = v > 0.1 ? 'cp' : v < -0.1 ? 'cn' : '';
        return `<span class="chip ${cls}">${k}:${v>=0?'+':''}${v.toFixed(3)}</span>`;
      }).join('');
      return `<div class="r-item"><div class="r-name" style="font-size:12px">${ind.name}<span class="code">${ind.code}</span></div><div class="chips">${ch}</div></div>`;
    }).join('');
    html += `<div class="card"><div class="ct">🔬 微观层(S01~S04) <span style="font-weight:400;font-size:10px;color:var(--green)">● 已激活</span></div>${sl}</div>`;
  } else {
    html += `<div class="card"><div class="ct">🔬 微观层(S01~S04)</div><div style="color:var(--muted);font-size:12px;padding:8px 0">等待定时任务采集ETF数据后激活</div></div>`;
  }

  html += `<div class="footer">小艺投资分析系统 v1.0</div>`;
  document.getElementById('app').innerHTML = html;
}
render(DATA);

// 准确率趋势图
setTimeout(function(){
  var c = document.getElementById('accChart');
  if (!c) return;
  var pts = DATA.accuracy_history;
  var ctx = c.getContext('2d');
  var w = c.offsetWidth * 2;
  var h = 120 * 2;
  c.width = w;
  c.height = h;
  var pad = {t: 16, r: 12, b: 24, l: 36};
  var pw = w - pad.l - pad.r;
  var ph = h - pad.t - pad.b;
  var minV = Math.min.apply(null, pts.map(function(p){return p.accuracy;})) - 5;
  var maxV = Math.max.apply(null, pts.map(function(p){return p.accuracy;})) + 5;
  minV = Math.max(0, Math.floor(minV / 10) * 10);
  maxV = Math.min(100, Math.ceil(maxV / 10) * 10);

  // grid
  ctx.strokeStyle = '#2a2d3e';
  ctx.lineWidth = 1;
  for (var g = 0; g <= 4; g++) {
    var y = pad.t + ph * (1 - g / 4);
    ctx.beginPath();
    ctx.moveTo(pad.l, y);
    ctx.lineTo(pad.l + pw, y);
    ctx.stroke();
    ctx.fillStyle = '#7a7d8e';
    ctx.font = '12px system-ui';
    ctx.textAlign = 'right';
    ctx.fillText(Math.round(minV + (maxV - minV) * g / 4) + '%', pad.l - 4, y + 4);
  }

  // line
  ctx.beginPath();
  ctx.strokeStyle = '#34d399';
  ctx.lineWidth = 2.5;
  ctx.lineJoin = 'round';
  pts.forEach(function(p, i) {
    var x = pad.l + pw * i / (pts.length - 1);
    var y = pad.t + ph * (1 - (p.accuracy - minV) / (maxV - minV));
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();

  // fill under line
  ctx.lineTo(pad.l + pw, pad.t + ph);
  ctx.lineTo(pad.l, pad.t + ph);
  ctx.closePath();
  ctx.fillStyle = '#34d39922';
  ctx.fill();

  // dots
  pts.forEach(function(p, i) {
    var x = pad.l + pw * i / (pts.length - 1);
    var y = pad.t + ph * (1 - (p.accuracy - minV) / (maxV - minV));
    ctx.beginPath();
    ctx.arc(x, y, 3, 0, Math.PI * 2);
    ctx.fillStyle = '#34d399';
    ctx.fill();
    ctx.strokeStyle = '#0f1119';
    ctx.lineWidth = 1.5;
    ctx.stroke();
  });

  // x labels
  pts.forEach(function(p, i) {
    if (pts.length > 7 && i % 2 !== 0 && i !== pts.length - 1) return;
    var x = pad.l + pw * i / (pts.length - 1);
    ctx.fillStyle = '#7a7d8e';
    ctx.font = '10px system-ui';
    ctx.textAlign = 'center';
    var label = p.date;
    if (label.length > 5) label = label.slice(5);
    ctx.fillText(label, x, pad.t + ph + 16);
  });
}, 100);
</script>
</body>
</html>'''

    out_path = BASE_DIR / "data" / "dashboard.html"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(HTML_TEMPLATE)
    print(f"✅ 看板已生成: {out_path}")
    print(f"   大小: {len(HTML_TEMPLATE)} bytes")
    return out_path


if __name__ == "__main__":
    build_standalone_html()
