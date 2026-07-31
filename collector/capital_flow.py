#!/usr/bin/env python3
"""
北向资金/主力资金流数据采集器
计算 I03(资金聚集度) 维度值

数据源：
- 行业资金流: emdatah5.eastmoney.com/dc/ZJLX/getZDYLBData (m:90+t:2)
- 北向资金: datacenter.eastmoney.com RPT_MUTUAL_DEAL_HISTORY

I03 计算逻辑：
  1. 各行业主力资金净流入占比（当日）
  2. 归一化到 [-1, +1] 区间
  3. 北向资金整体方向作为系数微调
"""
import json
import urllib.request
import urllib.error
from pathlib import Path
from datetime import date, datetime, timedelta

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
FLOW_DIR = DATA_DIR / "capital_flow"

# 东方财富移动端行业资金流API
# m:90+t:2 = 东方财富二级行业分类
INDUSTRY_FLOW_URL = (
    "https://emdatah5.eastmoney.com/dc/ZJLX/getZDYLBData"
)

# 北向资金历史数据 (datacenter-web)
NORTH_FLOW_URL = (
    "http://datacenter-web.eastmoney.com/api/data/v1/get"
    "?reportName=RPT_MUTUAL_DEAL_HISTORY&columns=ALL"
    "&pageNumber=1&pageSize=5&sortTypes=-1&sortColumns=TRADE_DATE"
)

# 行业名称到API code的映射
INDUSTRY_T2_MAP = {
    "半导体": "BK1036",
    "消费电子": "BK1037",
    "人工智能": "BK1207",   # 用计算机作为AI的近似
    "新能源汽车": "BK1211",  # 用汽车作为新能源汽车的近似
    "光伏": "BK1031",       # 光伏设备
    "医药生物": "BK1216",
    "食品饮料": "BK0438",
    "银行": "BK1283",
    "证券": "BK0473",       # 证券Ⅱ
    "房地产": "BK1202",
    "煤炭": "BK0437",
    "有色金属": "BK0478",
    "电力": "BK0428",
    "国防军工": "BK1204",
    "通信": "BK1215",
    "计算机": "BK1207",
    "医疗器械": "BK1041",
    "钢铁": "BK0479",
    "建筑装饰": "BK1209",
    "汽车": "BK1211",
}

# 旧BK代码到新API code的映射（兼容原有config配置）
OLD_BK_TO_T2 = {
    "BK0477": "BK1036",   # 半导体
    "BK0481": "BK1037",   # 消费电子
    "BK0479": "BK1207",   # 人工智能 → 计算机
    "BK0489": "BK1211",   # 新能源汽车 → 汽车
    "BK0717": "BK1031",   # 光伏 → 光伏设备
    "BK0545": "BK1216",   # 医药生物
    "BK0433": "BK0438",   # 食品饮料
    "BK0896": "BK1283",   # 银行
    "BK0475": "BK0473",   # 证券 → 证券Ⅱ
    "BK0451": "BK1202",   # 房地产
    "BK0430": "BK0437",   # 煤炭
    "BK0548": "BK0478",   # 有色金属
    "BK0737": "BK0428",   # 电力
    "BK0456": "BK1204",   # 国防军工
    "BK0440": "BK1215",   # 通信
    "BK0474": "BK1207",   # 计算机
    "BK0546": "BK1041",   # 医疗器械
    "BK0431": "BK0479",   # 钢铁
    "BK0748": "BK1209",   # 建筑装饰
    "BK0507": "BK1211",   # 汽车
}


def _fetch_all_items():
    """分页获取所有行业资金流数据"""
    all_items = []
    base_params = (
        "?fields=f12,f14,f62,f184"
        "&pz=100&po=1&fid=f62"
        "&fs=m:90+t:2"
        "&ut=bd1d9ddb04089700cf9c27f6f7426281"
    )
    base_url = INDUSTRY_FLOW_URL + base_params

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Linux; Android 10; K) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Mobile Safari/537.36"
        ),
        "Referer": "https://emdatah5.eastmoney.com/dc/zjlx/block",
    }

    for page in range(1, 6):
        url = base_url + f"&pn={page}"
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = resp.read().decode("utf-8")
                data = json.loads(raw)
            items = data.get("data", {}).get("diff", [])
            if not items:
                break
            all_items.extend(items)
            if len(items) < 100:
                break
        except Exception as e:
            print(f"  ⚠️ 分页请求(page={page})失败: {e}")
            break

    return all_items


def fetch_industry_capital_flow():
    """
    获取各行业主力资金净流入数据（通过 emdatah5 移动端API）
    返回: { BK_code: { "net_inflow_ratio": 0.023, "net_inflow_amount": 12345678 } }
    """
    items = _fetch_all_items()
    if not items:
        print("  ⚠️ 行业资金流接口返回空")
        return {}

    # 构建 code -> item 映射
    by_code = {}
    for item in items:
        by_code[item.get("f12", "")] = item

    # 读取行业配置，按旧BK代码查找
    industries = _load_industries()
    result = {}

    for ind in industries:
        old_code = ind["code"]
        t2_code = OLD_BK_TO_T2.get(old_code)

        if t2_code and t2_code in by_code:
            item = by_code[t2_code]
            f184 = item.get("f184")
            f62 = item.get("f62")

            if f184 is not None:
                # f184 是百分比，如 2.34 表示 +2.34%
                net_ratio = round(float(f184) / 100.0, 4)
                result[old_code] = {
                    "net_inflow_ratio": net_ratio,
                    "net_inflow_amount": float(f62) if f62 else 0,
                }

    print(f"  → 获取到 {len(result)} 个行业的资金流数据")
    return result


def fetch_north_flow():
    """
    获取北向资金整体净流入/流出
    返回: { "total_net_inflow": 123456789, "direction": 1/-1/0 }
    """
    req = urllib.request.Request(NORTH_FLOW_URL, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8")
            data = json.loads(raw)
    except Exception as e:
        print(f"  ⚠️ 北向资金API请求失败: {e}")
        return {"total_net_inflow": 0, "direction": 0}

    records = data.get("result", {}).get("data", [])
    if not records:
        print("  ⚠️ 北向资金接口返回空")
        return {"total_net_inflow": 0, "direction": 0}

    # 汇总沪股通+深股通最新一天的总成交额
    # 注：RPT_MUTUAL_DEAL_HISTORY 的 DEAL_AMT 是当日成交额
    total_deal = sum(float(r.get("DEAL_AMT", 0) or 0) for r in records
                     if r.get("MUTUAL_TYPE") in ("001", "003"))

    # 简化的方向判断：如果有正数deal表示有资金流动
    direction = 1 if total_deal > 0 else (-1 if total_deal < 0 else 0)
    return {"total_net_inflow": total_deal, "direction": direction}


def compute_i03_values(flow_data=None, north_data=None):
    """
    计算I03(资金聚集度) 维度值
    主入口：从原始资金流到归一化I03值
    """
    if flow_data is None:
        flow_data = fetch_industry_capital_flow()
    if north_data is None:
        north_data = fetch_north_flow()

    if not flow_data:
        print("  ⚠️ 无资金流数据, I03全部置0")
        return {}

    # 提取各行业的净流入占比
    ratios = {}
    for code, info in flow_data.items():
        ratios[code] = info.get("net_inflow_ratio", 0)

    if not ratios:
        return {}

    # 归一化到 [-1, +1]
    values = list(ratios.values())
    max_abs = max(abs(v) for v in values) if values else 1
    if max_abs == 0:
        max_abs = 1

    i03_values = {}
    north_dir = north_data.get("direction", 0)

    for code, ratio in ratios.items():
        # min-max归一化，保号
        normalized = ratio / max_abs if max_abs > 0 else 0
        # 北向方向微调 (±0.05)
        adjusted = normalized + (0.05 * north_dir)
        # 裁切到 [-1, +1]
        adjusted = max(-1.0, min(1.0, adjusted))
        i03_values[code] = round(adjusted, 4)

    return i03_values


def _load_industries():
    """加载行业配置"""
    try:
        with open(CONFIG_DIR / "industries.json") as f:
            cfg = json.load(f)
        return cfg.get("industries", [])
    except Exception as e:
        print(f"  ⚠️ 行业配置加载失败: {e}")
        return []


def update_i03_in_engine(engine):
    """将I03值更新到引擎中"""
    i03_values = compute_i03_values()
    if not i03_values:
        return 0

    count = 0
    for code, val in i03_values.items():
        # 只有在引擎已有该行业的值时才更新
        if code in engine.industry_values:
            engine.set_industry(code, "I03", val)
            count += 1

    return count


def save_capital_flow(i03_values, date_str=None):
    """保存资金流数据到文件"""
    if date_str is None:
        date_str = date.today().isoformat()
    FLOW_DIR.mkdir(parents=True, exist_ok=True)
    path = FLOW_DIR / f"{date_str}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(i03_values, f, ensure_ascii=False, indent=2)
    print(f"  ✅ I03资金流已保存: {path}")
    return path


if __name__ == "__main__":
    i03 = compute_i03_values()
    if i03:
        print(f"✅ 获取到 {len(i03)} 个行业的I03数据")
        sorted_items = sorted(i03.items(), key=lambda x: -abs(x[1]))[:10]
        for code, val in sorted_items:
            arrow = "🔺" if val > 0 else "🔻"
            print(f"  {code}: {arrow} {val:+.3f}")
        save_capital_flow(i03)
    else:
        print("❌ 未获取到资金流数据（非交易时间或网络不通）")
