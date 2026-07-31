#!/usr/bin/env python3
"""
每日投资系统管线
1. 加载上次快照(如果有)
2. 应用时效衰减
3. 采集当天新闻
4. 事件分类 → 维度更新
5. 生成投资建议
6. 保存快照
"""
import json
import sys
import subprocess
from pathlib import Path
from datetime import date, datetime, timedelta

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "engine"))
sys.path.insert(0, str(BASE_DIR / "pipeline"))
sys.path.insert(0, str(BASE_DIR / "collector"))
from dimension_engine import DimensionEngine
from event_classifier import process_news, load_event_templates
from industry_performance import fetch_industry_performance, save_performance
from etf_fundamentals import compute_stock_values, save_stock_values
from capital_flow import compute_i03_values, update_i03_in_engine, save_capital_flow
from online_learning import OnlineLearner

DATA_DIR = BASE_DIR / "data"


def load_yesterdays_snapshot(engine):
    """尝试加载最近的快照"""
    snap_dir = DATA_DIR / "snapshots"
    if not snap_dir.exists():
        return False
    snapshots = sorted(snap_dir.glob("*.json"))
    if not snapshots:
        return False
    latest = snapshots[-1]
    print(f"  → 加载快照: {latest.name}")
    engine.load_snapshot(latest.stem)
    return True


def collect_news():
    """
    采集当天新闻。
    调 xiaoyi-web-search 获取真实金融资讯。
    """
    from news_collector import collect_real_news
    return collect_real_news()


def run_daily():
    print(f"\n{'='*50}")
    print(f"📊 投资系统每日运行 - {date.today().isoformat()}")
    print(f"{'='*50}\n")
    
    steps_ok = []  # 记录成功完成的步骤
    errors = []

    def _safe(func, step_name, *args, **kwargs):
        try:
            result = func(*args, **kwargs)
            steps_ok.append(step_name)
            return result
        except Exception as e:
            print(f"  ❌ {step_name} 失败: {e}")
            errors.append((step_name, str(e)))
            return None

    # 1. 初始化引擎
    engine = _safe(lambda: (print("✅ 引擎初始化完成"), DimensionEngine())[1], "初始化引擎")
    if engine is None:
        engine = DimensionEngine()
    print("✅ 引擎初始化完成")

    # 2. 加载上次快照
    loaded = _safe(load_yesterdays_snapshot, "加载快照", engine) or False
    if loaded:
        print("✅ 快照加载完成")
    else:
        print("⚠️ 无历史快照，从初始状态开始")

    # 3. 时效衰减
    _safe(lambda: engine.tick_decay(), "时效衰减")
    print("✅ 时效衰减已应用")

    # 4. 采集新闻
    print("📡 采集新闻...")
    news_items = _safe(collect_news, "新闻采集") or []
    print(f"  → 获取到 {len(news_items)} 条新闻")

    # 5. LLM事件分类与维度更新
    print("🔍 事件分类（LLM）与维度更新...")
    engine, updates = (_safe(lambda: process_news(news_items, engine), "事件分类") or (engine, 0))
    print(f"  → 完成 {updates} 次维度更新")

    # 6. 采集前日行业行情
    print("📊 采集前日行业行情（在线学习用）...")
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    perf_data = _safe(fetch_industry_performance, "行情采集") or {}
    if perf_data:
        _safe(lambda: save_performance(date_str=yesterday, perf_data=perf_data), "行情保存")
    else:
        print("  ⚠️ 行情数据为空，跳过")

    # 7. 采集ETF数据（微观层S01~S04）
    print("📈 采集ETF基本面与动量数据...")
    stock_vals = _safe(lambda: compute_stock_values(
        industries=engine.industry_list,
        snapshot_event_impacts=engine.active_event_impacts_by_industry
    ), "ETF采集") or {}
    if stock_vals:
        _safe(lambda: engine.load_stock_values(stock_vals), "加载微观层")
        _safe(lambda: save_stock_values(stock_vals), "保存微观层")
    else:
        print("  ⚠️ 微观层数据为空，跳过")

    # 8. 采集资金流数据（I03）
    print("💸 采集资金流数据（I03资金聚集度）...")
    i03_count = _safe(lambda: update_i03_in_engine(engine), "资金流采集") or 0
    if i03_count:
        i03_vals = {}
        for code in engine.industry_values:
            val = engine.industry_values[code].get("I03", 0)
            if val != 0:
                i03_vals[code] = val
        if i03_vals:
            _safe(lambda: save_capital_flow(i03_vals), "资金流保存")
        print(f"  → I03已更新 {i03_count} 个行业")
    else:
        print("  ⚠️ 资金流数据为空，跳过")

    # 9. 在线学习
    print("🧠 在线学习（评估前日预测）...")
    try:
        ol = OnlineLearner(engine)
        n = ol.update_weights_from_error(yesterday)
        if n:
            print(f"  → 更新了 {n} 个维度权重")
        steps_ok.append("在线学习")
    except Exception as e:
        print(f"  ⚠️ 在线学习跳过: {e}")
        errors.append(("在线学习", str(e)))

    # 10. 生成投资建议
    print("💡 生成投资建议（三层评分）...")
    rec = _safe(engine.recommend, "生成建议") or engine.recommend()
    print(json.dumps(rec, ensure_ascii=False, indent=2))

    # 11. 保存快照
    _safe(engine.save_snapshot, "保存快照")

    # 12. 保存建议到独立文件
    rec_dir = DATA_DIR / "recommendations"
    rec_dir.mkdir(parents=True, exist_ok=True)
    rec_path = rec_dir / f"{date.today().isoformat()}.json"
    _safe(lambda: (rec_dir.mkdir(parents=True, exist_ok=True),
                   open(rec_path, "w").write(json.dumps(rec, ensure_ascii=False, indent=2))),
          "保存建议")
    print(f"📄 建议已保存: {rec_path}")

    # 13. 生成静态看板
    print("📱 生成静态看板...")
    _safe(lambda: build_standalone_html_inner(), "看板生成")

    # ─── 构建推送消息 ───
    view = rec.get("market_view", "—")
    pos = rec.get("position_limit", 1.0)
    macro_s = rec.get("macro_score", 0)
    top_inds = rec.get("recommended_industries", [])
    has_llm = any(s == "事件分类" for s in steps_ok)

    push_lines = [
        f"📊 投资系统日报 {date.today().isoformat()}",
        f"",
    ]

    # 运行状态
    if errors:
        errs = "; ".join(f"{s}: {e[:30]}" for s, e in errors[:3])
        push_lines.append(f"⚠️ 部分步骤异常: {errs}")
        push_lines.append(f"")

    push_lines.append(f"▎市场判断: {view} | 仓位: {int(pos*100)}%")
    push_lines.append(f"▎宏观评分: {macro_s:+.3f}")
    push_lines.append(f"")
    push_lines.append(f"▎推荐 TOP3")
    for i, ind in enumerate(top_inds, 1):
        name = load_industry_name(ind.get("industry_code", ""))
        score = ind.get("score", 0)
        line = f"  {i}. {name or ind.get('industry_code','')} {score:+.3f}"
        # 附微观层
        if "stock_details" in ind:
            s = ind["stock_details"]
            chips = []
            if s.get("S01", 0) != 0:
                chips.append(f"S01:{s['S01']:+.2f}")
            if s.get("S03", 0) != 0:
                chips.append(f"S03:{s['S03']:+.2f}")
            if chips:
                line += f" [{', '.join(chips)}]"
        push_lines.append(line)

    # 宏观维度
    push_lines.append(f"")
    eco = engine.macro_values.get("M01", 0)
    money = engine.macro_values.get("M02", 0)
    risk = engine.macro_values.get("M03", 0)
    uncer = engine.macro_values.get("M04", 0)
    push_lines.append(f"▎宏观维度: 景气{eco:+.2f} 宽松{money:+.2f} 偏好{risk:+.2f} 不确定{uncer:+.2f}")

    push_lines.append(f"")
    push_lines.append(f"💡 详细看板: 查看推送附件")

    push_message = "\n".join(push_lines)
    print(f"\n{'='*50}")
    print("📱 推送消息预览")
    print(f"{'='*50}")
    print(push_message)

    # 输出JSON供cron消费
    if "--json" in sys.argv:
        print(f"\n##PUSH_JSON##")
        print(json.dumps({
            "message": push_message,
            "dashboard_path": str(DATA_DIR / "dashboard.html"),
            "errors": len(errors),
        }, ensure_ascii=False))

    return rec


def load_industry_name(code):
    try:
        with open(BASE_DIR / "config" / "industries.json") as f:
            cfg = json.load(f)
        for ind in cfg.get("industries", []):
            if ind["code"] == code:
                return ind["name"]
    except Exception:
        pass
    return ""


def build_standalone_html_inner():
    sys.path.insert(0, str(BASE_DIR / "web"))
    from generate_static import build_standalone_html
    build_standalone_html()


if __name__ == "__main__":
    run_daily()
