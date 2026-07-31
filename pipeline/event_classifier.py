#!/usr/bin/env python3
"""
事件映射引擎 - LLM增强版
将原始新闻事件 → LLM语义分类（带正则兜底）→ 维度更新 → 注入 DimensionEngine

架构：
1. 优先调用 LLM（通过 openclaw CLI）进行语义分类
2. 如果 LLM 不可用，自动降级到正则关键词匹配
3. LLM 输出按模板验证和规范化
"""
import json
import os
import re
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "engine"))
from dimension_engine import DimensionEngine

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# ============================================================
# 配置加载
# ============================================================

def load_event_templates():
    with open(CONFIG_DIR / "event_templates.json") as f:
        return json.load(f)


def _load_industries():
    with open(CONFIG_DIR / "industries.json") as f:
        return json.load(f)["industries"]


# ============================================================
# LLM 分类器
# ============================================================

_LLM_AVAILABLE = not os.environ.get("CRON_MODE")  # cron模式跳过LLM（子进程openclaw infer会挂死）


def _build_llm_prompt(news_text):
    """构建 LLM 分类提示"""
    templates = load_event_templates()
    industries = _load_industries()

    event_list = "\n".join(
        f"  - {et}"
        for et in sorted(templates.keys())
    )

    industry_list = "\n".join(
        f"  - {ind['code']}: {ind['name']}（{ind['category']}）"
        for ind in industries
    )

    return f"""你是一个金融事件分类专家。将新闻分类到以下事件类型之一，并评估强度和影响范围。

可用事件类型：
{event_list}

可用行业（代码：名称）：
{industry_list}

输出规则（严格遵守）：
1. event_type 必须是上述列表中的一种
2. intensity 0.0~1.0（弱 0.2~0.4 / 中 0.5~0.7 / 强 0.8~1.0）
3. scope: macro(宏观) | industry(特定行业) | global(全球性)
4. targets: scope=industry时填受影响的行业代码数组(一个或多个)，否则填空数组 []
5. 如果新闻同时影响多个方面，可以输出多个事件对象
6. 如果新闻完全不相关，输出空数组 []
7. 仅输出纯 JSON 数组，不要 markdown 代码块包裹，不要任何额外文字

===
新闻：{news_text}
===
JSON："""


def _call_llm(news_text):
    """通过 openclaw CLI 调用 LLM 进行分类"""
    global _LLM_AVAILABLE
    if not _LLM_AVAILABLE:
        return None

    prompt = _build_llm_prompt(news_text)

    try:
        result = subprocess.run(
            ["openclaw", "infer", "model", "run", "--prompt", prompt, "--json"],
            capture_output=True, text=True, timeout=60,
            env={**os.environ, "OPENCLAW_LOG_LEVEL": "error"},
        )
        if result.returncode != 0:
            _LLM_AVAILABLE = False
            return None

        # 解析 JSON 输出
        output = json.loads(result.stdout)
        raw_text = output.get("outputs", [{}])[0].get("text", "")
        if not raw_text:
            return None

        # 清理响应：去掉可能的 markdown 代码块包裹
        cleaned = raw_text.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            cleaned = "\n".join(
                line for line in lines
                if not line.strip().startswith("```")
            ).strip()

        # 尝试解析 JSON
        events = json.loads(cleaned)
        if not isinstance(events, list):
            return None

        # 验证并规范化
        templates = load_event_templates()
        valid = []
        for e in events:
            et = e.get("event_type", "")
            if et not in templates:
                continue
            valid.append({
                "event_type": et,
                "intensity": max(0.0, min(1.0, float(e.get("intensity", 0.5)))),
                "scope": e.get("scope", "macro")
                if e.get("scope") in ("macro", "industry", "global")
                else "macro",
                "targets": e.get("targets", []),
            })

        return valid if valid else None

    except (json.JSONDecodeError, subprocess.TimeoutExpired, Exception):
        _LLM_AVAILABLE = False
        return None


# ============================================================
# 正则兜底分类器
# ============================================================

def _regex_classify(news_text):
    """纯关键词匹配的兜底分类"""
    results = []
    text_lower = news_text.lower()

    _dedup_check = set()  # 防重复

    # ---------- 货币政策 ----------
    intensity = 0.5
    if re.search(r"超预期|重磅|大幅|意外", text_lower):
        intensity = 0.8
    if re.search(r"小幅|微调|温和", text_lower):
        intensity = 0.3

    if re.search(r"降[息准]|lpr.*下调|下调.*lpr|利率.*降", text_lower):
        et = "货币政策_降息"
        if et not in _dedup_check:
            _dedup_check.add(et)
            results.append({"event_type": et, "intensity": intensity, "scope": "macro", "targets": []})
    elif re.search(r"加[息准]", text_lower):
        et = "货币政策_加息"
        if et not in _dedup_check:
            _dedup_check.add(et)
            results.append({"event_type": et, "intensity": intensity, "scope": "macro", "targets": []})

    # ---------- 产业政策 ----------
    industry_keywords = {
        "半导体|芯片|集成电路": ("BK0477", "科技"),
        "新能源|光伏|风电|锂电池": ("BK0489", "制造"),
        "人工智能|ai|大模型|智能体": ("BK0479", "科技"),
        "医药|医疗|创新药|医保": ("BK0545", "消费"),
        "新能源汽车|电动车|充电桩": ("BK0489", "制造"),
        "房地产|房住不炒|限购|楼市": ("BK0451", "周期"),
        "军工|国防|装备": ("BK0456", "制造"),
        "消费|促消费|扩内需": ("BK0433", "消费"),
        "银行": ("BK0896", "金融"),
        "证券|券商": ("BK0475", "金融"),
        "煤炭": ("BK0430", "周期"),
        "有色金属": ("BK0548", "周期"),
        "电力": ("BK0737", "公用事业"),
        "通信|5g|6g": ("BK0440", "科技"),
        "计算机|软件": ("BK0474", "科技"),
        "器械|医疗设备": ("BK0546", "消费"),
        "钢铁": ("BK0431", "周期"),
        "建筑": ("BK0748", "周期"),
        "汽车": ("BK0507", "制造"),
        "消费电子|手机|智能硬件": ("BK0481", "科技"),
    }

    for kw, (industry_code, _) in industry_keywords.items():
        if re.search(kw, text_lower):
            if re.search(r"打压|限制|监管|处罚|整治|利空|风险|制裁|调查", text_lower):
                event_type = "产业政策_利空"
            else:
                event_type = "产业政策_利好"

            intensity = 0.5
            if re.search(r"重磅|超预期|历史性|前所未有|大力|重点", text_lower):
                intensity = 0.8
            if re.search(r"小幅|微调|温和|逐步", text_lower):
                intensity = 0.3

            results.append({
                "event_type": event_type,
                "intensity": intensity,
                "scope": "industry",
                "targets": [industry_code],
            })

    # ---------- 地缘冲突 ----------
    if re.search(r"制裁|冲突|战争|军事行动|地缘|贸易摩擦|关税", text_lower):
        if re.search(r"升级|加剧|扩大|新制裁|爆发", text_lower):
            event_type = "地缘冲突_升级"
        elif re.search(r"缓和|谈判|和解|停火|协议| ceasefire", text_lower):
            event_type = "地缘冲突_缓和"
        else:
            event_type = "地缘冲突_升级"
        results.append({
            "event_type": event_type,
            "intensity": 0.5,
            "scope": "global",
            "targets": [],
        })

    # ---------- 经济数据 ----------
    econ_pattern = r"cpi|pmi|gdp|进出口|社融|m2|工业增加值|社零|固定资产投资"
    if re.search(econ_pattern, text_lower):
        event_type = None
        if re.search(r"超预期|好于|高于|增长|回暖|回升|反弹", text_lower):
            event_type = "经济数据_超预期"
        elif re.search(r"不及预期|低于|放缓|回落|下滑|收缩|下降", text_lower):
            event_type = "经济数据_不及预期"

        if event_type:
            results.append({
                "event_type": event_type,
                "intensity": 0.5,
                "scope": "global",
                "targets": [],
            })

    # ---------- 公司事件 ----------
    company_bullish = r"业绩预增|利润大增|营收增长|盈利提升|涨停|创新高|回购|增持|分红"
    company_bearish = r"业绩预亏|利润下滑|亏损|跌停|退市|减持|质押|违规|立案|调查|st"
    if re.search(company_bullish, text_lower):
        intensity = 0.5
        if re.search(r"大幅|超预期|历史新高", text_lower):
            intensity = 0.8
        results.append({
            "event_type": "公司事件_利好",
            "intensity": intensity,
            "scope": "industry",
            "targets": [],
        })
    elif re.search(company_bearish, text_lower):
        intensity = 0.5
        if re.search(r"重大|严重|立案|退市", text_lower):
            intensity = 0.8
        results.append({
            "event_type": "公司事件_利空",
            "intensity": intensity,
            "scope": "industry",
            "targets": [],
        })

    # ---------- 外资异动 ----------
    if re.search(r"北向资金|外资流入|外资流出|北上资金", text_lower):
        intensity = 0.5
        if re.search(r"大幅|巨额|创新高|历史记录", text_lower):
            intensity = 0.8
        results.append({
            "event_type": "外资异动",
            "intensity": intensity,
            "scope": "macro",
            "targets": [],
        })

    return results


# ============================================================
# 主分类器接口（LLM + 正则兜底）
# ============================================================

def classify_event(news_text):
    """
    LLM 语义分类 + 正则兜底。
    保持与旧版完全兼容的接口。

    参数：
        news_text: str - 新闻文本

    返回：
        list of dict - 每个 dict 格式：
        {
            "event_type": str,      # 事件类型（11种之一）
            "intensity": float,     # 强度 0.0~1.0
            "scope": str,           # macro | industry | global
            "targets": list[str]    # 行业代码列表
        }
    """
    # 先尝试 LLM 分类
    llm_result = _call_llm(news_text)
    if llm_result:
        return llm_result

    # 兜底：正则匹配
    return _regex_classify(news_text)


# ============================================================
# 事件应用（保持原逻辑不变）
# ============================================================

def apply_event(engine, event, templates):
    """将事件映射到维度更新，注入引擎"""
    event_type = event.get("event_type", "")
    intensity = event.get("intensity", 0.5)
    targets = event.get("targets", [])

    template = templates.get(event_type)
    if not template:
        return 0

    affected = template.get("affected_dimensions", {})
    decay_days = template.get("decay_days", 20)
    decay_rate = template.get("decay_rate", 0.95)
    conf = template.get("confidence", 0.7)

    updates_count = 0

    for dim_id, dim_info in affected.items():
        delta = dim_info["base_delta"] * intensity

        if dim_id in ["M01", "M02", "M03", "M04"]:
            # 宏观维度
            current = engine.macro_values.get(dim_id, 0.0)
            new_val = current + delta
            engine.set_macro(dim_id, new_val)
            engine.add_event_impact(
                dim_id=dim_id, target=None,
                delta_per_day=delta * (1 - decay_rate) / decay_days,
                total_days=decay_days,
                source=event_type
            )
            updates_count += 1

        elif dim_id in ["I01", "I02", "I03", "I04", "I05"]:
            # 行业维度
            # scope=macro/global 时 targets 为空，影响所有行业
            # scope=industry 时 targets 为特定行业代码列表
            affect_all = not targets
            for ind_code in engine.industry_values:
                if not affect_all and ind_code not in targets:
                    continue
                current = engine.industry_values[ind_code].get(dim_id, 0.0)
                engine.set_industry(ind_code, dim_id, current + delta)
                engine.add_event_impact(
                    dim_id=dim_id, target=f"industry:{ind_code}",
                    delta_per_day=delta * (1 - decay_rate) / decay_days,
                    total_days=decay_days,
                    source=event_type
                )
                updates_count += 1

        elif dim_id in ["S01", "S02", "S03", "S04"]:
            # 微观维度，暂不处理（个股层级太多）
            pass

    return updates_count


def process_news(news_texts, engine=None):
    """批量处理新闻，更新引擎"""
    if engine is None:
        engine = DimensionEngine()

    templates = load_event_templates()
    total_updates = 0

    for text in news_texts:
        events = classify_event(text)
        for event in events:
            n = apply_event(engine, event, templates)
            total_updates += n

    return engine, total_updates


# ============================================================
# 测试
# ============================================================

if __name__ == "__main__":
    test_news = [
        "央行宣布降息25bp，LPR下调超预期",
        "国务院印发《半导体产业高质量发展规划》，重点扶持先进制程",
        "美国宣布新一轮对华半导体制裁，限制高端芯片出口",
        "6月制造业PMI录得51.2，连续三个月高于荣枯线",
        "地缘冲突升级：俄乌双方在顿巴斯地区爆发激烈交火",
        "贵州茅台三季度净利润同比增长15%，超出市场预期",
        "北向资金今日净买入超80亿元，连续5日加仓A股",
    ]

    print("=" * 60)
    print("事件分类器测试")
    print("=" * 60)

    for news in test_news:
        print(f"\n📰 {news}")
        events = classify_event(news)
        if events:
            for e in events:
                print(f"   [{e['event_type']}] 强度={e['intensity']} "
                      f"范围={e['scope']}" +
                      (f" 目标={e['targets']}" if e['targets'] else ""))
        else:
            print("   ❌ 未识别")

    # 完整流程
    print("\n" + "=" * 60)
    print("完整流程测试 (process_news -> recommend)")
    print("=" * 60)
    engine, updates = process_news(test_news)
    print(f"总维度更新数: {updates}")
    rec = engine.recommend()
    print(json.dumps(rec, ensure_ascii=False, indent=2))
