#!/usr/bin/env python3
"""
ETF 基本面与动量数据采集器
采集行业 ETF 的 PE/PB/涨跌幅，计算微观层(S01~S04)维度值
输出：stock_values 字典，供 DimensionEngine 使用

各维度计算逻辑：
  S01 基本面硬度  = PE历史位置（低PE→高分，高PE→低分）
  S02 相对价值    = PE vs 行业ETF平均PE的偏离度（折价→正分，溢价→负分）
  S03 动量趋势    = 近20日涨跌幅的标准化得分
  S04 催化剂密度  = 该行业近期活跃事件统计得分

数据源：
- 实时估值: https://fundgz.1234567.com.cn/js/{code}.js
- 历史PE: https://fund.eastmoney.com/pingzhongdata/{code}.js (pe数组)
- 历史净值: https://fund.eastmoney.com/pingzhongdata/{code}.js (Data_netWorthTrend数组)
"""
import json
import urllib.request
import urllib.error
import re
from pathlib import Path
from datetime import datetime, date, timedelta

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
STOCK_DIR = DATA_DIR / "stock_values"

# API域名
FUNDGZ_URL = "https://fundgz.1234567.com.cn/js/{code}.js"
PINGZHONG_URL = "https://fund.eastmoney.com/pingzhongdata/{code}.js"


def load_industry_config():
    """加载行业配置，获取行业到ETF的映射"""
    try:
        with open(CONFIG_DIR / "industries.json") as f:
            cfg = json.load(f)
        return cfg.get("industries", [])
    except Exception as e:
        print(f"  ⚠️ 行业配置加载失败: {e}")
        return []


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://fund.eastmoney.com/",
}


def fetch_fundgz(code):
    """
    获取ETF实时估值
    返回: { "gsz": 1.8896, "gszzl": 1.81, "dwjz": 1.8560, "jzrq": "2026-07-13" }
    """
    url = FUNDGZ_URL.format(code=code)
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=10) as resp:
            text = resp.read().decode("utf-8")
        # 解析 JSONP: jsonpgz({...})
        match = re.search(r'jsonpgz\(({.*})\)', text)
        if match:
            data = json.loads(match.group(1))
            return {
                "gsz": float(data.get("gsz", 0)),
                "gszzl": float(data.get("gszzl", 0)),
                "dwjz": float(data.get("dwjz", 0)),
                "jzrq": data.get("jzrq", ""),
            }
    except Exception as e:
        print(f"  ⚠️ fundgz {code} 请求失败: {e}")
    return None


def fetch_pingzhongdata(code):
    """
    获取ETF历史数据（PE、净值）
    返回: { "pe": [...], "nav": [...], "rank_pct": ... }
    """
    url = PINGZHONG_URL.format(code=code)
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=15) as resp:
            text = resp.read().decode("utf-8")

        result = {}

        # 解析PE数组：pe = [{"x": timestamp, "y": value}, ...]
        pe_match = re.search(r'pe\s*=\s*(\[.*?\])\s*;', text, re.DOTALL)
        if pe_match:
            result["pe"] = json.loads(pe_match.group(1))

        # 解析净值趋势：Data_netWorthTrend = [{"x": timestamp, "y": value, ...}, ...]
        nav_match = re.search(r'Data_netWorthTrend\s*=\s*(\[.*?\])\s*;', text, re.DOTALL)
        if nav_match:
            result["nav"] = json.loads(nav_match.group(1))

        # 解析排名百分位
        rank_match = re.search(r'Data_rateInSimilarPersent\s*=\s*(\[.*?\])\s*;', text, re.DOTALL)
        if rank_match:
            rank_data = json.loads(rank_match.group(1))
            if rank_data:
                result["rank_pct"] = rank_data[-1][1]  # 最新排名百分位

        return result
    except Exception as e:
        print(f"  ⚠️ pingzhongdata {code} 请求失败: {e}")
    return None


def batch_fetch_etf_details(industries):
    """
    批量获取ETF详情（实时估值+PE）
    返回: { BK_code: { "current_price": 1.89, "pe": 83, "rank_pct": 98.34, ... } }
    """
    details = {}
    for ind in industries:
        code = ind["code"]
        etf = ind.get("etf", "")
        if not etf:
            continue

        # 获取实时估值
        fundgz_data = fetch_fundgz(etf)
        if not fundgz_data:
            continue

        # 获取历史数据（PE，排名）
        pingzhong_data = fetch_pingzhongdata(etf)

        # 解析PE：取最新值
        pe_latest = None
        if pingzhong_data and "pe" in pingzhong_data:
            pe_arr = pingzhong_data["pe"]
            if pe_arr:
                pe_latest = pe_arr[-1].get("y") if isinstance(pe_arr[-1], dict) else pe_arr[-1]

        details[code] = {
            "code": code,
            "etf_code": etf,
            "name": ind.get("name", ""),
            "current_price": fundgz_data.get("gsz", 0),
            "nav": fundgz_data.get("dwjz", 0),
            "change_pct": fundgz_data.get("gszzl", 0),
            "pe": pe_latest,
            "rank_pct": pingzhong_data.get("rank_pct") if pingzhong_data else None,
        }

    print(f"  → 获取到 {len(details)} 个ETF的实时数据")
    return details


def batch_fetch_etf_kline(industries, days=20):
    """
    批量获取ETF近N日涨跌幅（动量数据）
    从Data_netWorthTrend提取历史净值，计算近N日涨跌幅
    返回: { BK_code: { "momentum_20d": 0.023, "nav_history": [...] } }
    """
    momentum = {}
    for ind in industries:
        code = ind["code"]
        etf = ind.get("etf", "")
        if not etf:
            continue

        pingzhong_data = fetch_pingzhongdata(etf)
        if not pingzhong_data or "nav" not in pingzhong_data:
            continue

        nav_arr = pingzhong_data["nav"]
        if len(nav_arr) < 2:
            continue

        # 取最新净值和days天前的净值计算动量
        latest_nav = nav_arr[-1]["y"]
        # 找到days天前的数据点
        target_idx = max(0, len(nav_arr) - days - 1)
        prev_nav = nav_arr[target_idx]["y"]

        if prev_nav > 0:
            momentum_ratio = round((latest_nav - prev_nav) / prev_nav, 4)
        else:
            momentum_ratio = 0

        momentum[code] = {
            "momentum_20d": momentum_ratio,
            "current_nav": latest_nav,
            "prev_nav": prev_nav,
        }

    print(f"  → 获取到 {len(momentum)} 个ETF的动量数据")
    return momentum


def calc_stock_dimensions(industries, etf_details, etf_momentum, snapshot_event_impacts):
    """
    计算微观层维度值 S01~S04
    """
    stock_vals = {}

    # 收集所有PE值用于相对价值计算
    all_pes = [d["pe"] for d in etf_details.values() if d.get("pe") is not None and d["pe"] > 0]
    avg_pe = sum(all_pes) / len(all_pes) if all_pes else 1

    for ind in industries:
        code = ind["code"]
        detail = etf_details.get(code, {})
        momentum = etf_momentum.get(code, {})

        pe = detail.get("pe")
        rank_pct = detail.get("rank_pct")
        change_pct = detail.get("change_pct", 0)
        momentum_20d = momentum.get("momentum_20d", 0)

        # S01: 基本面硬度 = PE历史位置归一化
        # PE百分位越低(估值低) → 高分，越高(估值贵) → 低分
        if rank_pct is not None and rank_pct > 0:
            # rank_pct 是百分位(0-100)，越低说明PE在历史上处于低位
            # 归一化到 [-1, +1]：rank_pct<50 → 正分，>50 → 负分
            s01 = round((50 - rank_pct) / 50.0, 4)
        elif pe and pe > 0:
            # 没有百分位时，用PE绝对值粗略判断
            # PE<20 → 价值型, PE>80 → 成长型
            s01 = round(max(-1.0, min(1.0, (40 - pe) / 40.0)), 4)
        else:
            s01 = 0.0

        # S02: 相对价值 = PE vs 行业平均PE
        if pe and pe > 0 and avg_pe > 0:
            # 折价(PE < avg) → 正分
            s02 = round((avg_pe - pe) / avg_pe, 4)
            s02 = max(-1.0, min(1.0, s02))
        else:
            s02 = 0.0

        # S03: 动量趋势 = 近20日涨跌幅标准化
        if momentum_20d != 0:
            s03 = round(momentum_20d * 5.0, 4)  # 放大系数让变化更明显
            s03 = max(-1.0, min(1.0, s03))
        elif change_pct != 0:
            # 没有历史数据时，用当日涨跌幅
            s03 = round(max(-1.0, min(1.0, change_pct / 10.0)), 4)
        else:
            s03 = 0.0

        # S04: 催化剂密度 = 行业近期事件活跃度
        impacts = snapshot_event_impacts.get(code, {})
        # 统计事件影响总和
        s04 = sum(impacts.values()) if impacts else 0
        s04 = round(max(-1.0, min(1.0, s04)), 4)

        stock_vals[code] = {
            "S01": s01,
            "S02": s02,
            "S03": s03,
            "S04": s04,
            "pe": pe or 0,
            "price": detail.get("current_price", 0),
            "change_pct": change_pct,
        }

    return stock_vals


def compute_stock_values(industries=None, snapshot_event_impacts=None):
    """
    主入口：采集ETF数据并计算微观维度
    """
    if industries is None:
        industries = load_industry_config()
    if snapshot_event_impacts is None:
        snapshot_event_impacts = {}

    # 第一步：获取实时估值 + PE
    print("  📡 采集ETF实时数据...")
    etf_details = batch_fetch_etf_details(industries)

    # 第二步：获取动量数据（近20日涨跌幅）
    print("  📡 采集ETF动量数据...")
    etf_momentum = batch_fetch_etf_kline(industries)

    if not etf_details and not etf_momentum:
        print("  ⚠️ 未获取到任何ETF数据")
        return {}

    # 第三步：计算微观维度
    print("  🧮 计算微观维度...")
    stock_vals = calc_stock_dimensions(
        industries, etf_details, etf_momentum, snapshot_event_impacts
    )
    print(f"  → 计算了 {len(stock_vals)} 个行业的微观维度")

    return stock_vals


def save_stock_values(stock_vals, date_str=None):
    """保存微观维度值到文件"""
    if date_str is None:
        date_str = date.today().isoformat()

    STOCK_DIR.mkdir(parents=True, exist_ok=True)
    path = STOCK_DIR / f"{date_str}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(stock_vals, f, ensure_ascii=False, indent=2)
    print(f"  ✅ 微观维度已保存: {path}")
    return path


if __name__ == "__main__":
    industries = load_industry_config()
    vals = compute_stock_values(industries)
    if vals:
        print("\n微观维度示例（前5个行业）:")
        for i, (code, dims) in enumerate(sorted(vals.items())[:5]):
            print(f"  {code}: S01={dims['S01']:+.3f} S02={dims['S02']:+.3f} S03={dims['S03']:+.3f} S04={dims['S04']:+.3f}")
        save_stock_values(vals)
    else:
        print("❌ 未获取到微观维度数据")
