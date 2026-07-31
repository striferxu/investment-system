#!/usr/bin/env python3
"""
行业实际涨跌幅采集器
数据源：东方财富移动端行业行情 API（emdatah5）
输出：data/actual_performance/YYYY-MM-DD.json
      { "BK0477": 0.023, "BK0481": -0.015, ... }
"""
import json
import urllib.request
import urllib.error
from pathlib import Path
from datetime import date, timedelta

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
PERF_DIR = DATA_DIR / "actual_performance"

# 东方财富移动端行业行情API
PERF_URL = "https://emdatah5.eastmoney.com/dc/ZJLX/getZDYLBData"

# 旧BK代码到新t:3 API code的映射（m:90+t:3 概念板块行业）
# 注意：t:3的分类和t:2不同，BK代码体系也有差异
# 大多数行业在t:3中也有，但code不同
OLD_BK_TO_T3 = {
    # 在t:3中找到的
    "BK0477": "BK1036",   # 半导体
    "BK0481": "BK1037",   # 消费电子
    "BK0479": "BK0800",   # 人工智能 (confirmed in t:3)
    "BK0489": "BK0493",   # 新能源汽车 → 新能源
    "BK0717": "BK0588",   # 光伏 → 光伏概念
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


def _fetch_all_performance_items():
    """分页获取所有行业涨跌幅数据（m:90+t:3）"""
    all_items = []
    base_params = (
        "?fields=f12,f14,f3"
        "&pz=100&po=1&fid=f3"
        "&fs=m:90+t:3"
        "&ut=bd1d9ddb04089700cf9c27f6f7426281"
    )
    base_url = PERF_URL + base_params

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


def fetch_industry_performance():
    """获取当日所有行业板块涨跌幅，返回 { BK_code: change_ratio }"""
    items = _fetch_all_performance_items()
    if not items:
        print("  ⚠️ 行情接口返回空")
        return {}

    # 构建code->item映射
    by_code = {}
    for item in items:
        by_code[item.get("f12", "")] = item

    # 按旧BK代码查询映射
    industries = _load_industries()
    result = {}

    for ind in industries:
        old_code = ind["code"]
        t3_code = OLD_BK_TO_T3.get(old_code)

        if t3_code and t3_code in by_code:
            item = by_code[t3_code]
            change_pct = item.get("f3")

            if change_pct is not None:
                # 转换为 [-1, 1] 区间的分数
                result[old_code] = round(change_pct / 100.0, 4)

    print(f"  → 获取到 {len(result)} 个行业行情数据")
    return result


def _load_industries():
    """加载行业配置"""
    try:
        with open(CONFIG_DIR / "industries.json") as f:
            cfg = json.load(f)
        return cfg.get("industries", [])
    except Exception as e:
        print(f"  ⚠️ 行业配置加载失败: {e}")
        return []


def save_performance(date_str=None, perf_data=None):
    """保存行业实际表现到文件"""
    today = date.today()
    if date_str is None:
        date_str = (today - timedelta(days=1)).isoformat()
    if perf_data is None:
        perf_data = fetch_industry_performance()

    PERF_DIR.mkdir(parents=True, exist_ok=True)
    path = PERF_DIR / f"{date_str}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(perf_data, f, ensure_ascii=False, indent=2)
    print(f"  ✅ 已保存 {len(perf_data)} 个行业行情: {path}")
    return path


def load_performance(date_str):
    """加载某天的实际行情"""
    path = PERF_DIR / f"{date_str}.json"
    if not path.exists():
        return {}
    with open(path) as f:
        return json.load(f)


if __name__ == "__main__":
    data = fetch_industry_performance()
    if data:
        print("前 10 个行业涨跌幅:")
        for i, (code, ratio) in enumerate(sorted(data.items(), key=lambda x: -abs(x[1]))[:10]):
            arrow = "🔺" if ratio > 0 else "🔻"
            print(f"  {i+1:2d}. {code:>8s} {arrow} {ratio*100:+.2f}%")
        save_performance(perf_data=data)
    else:
        print("❌ 未获取到行业行情数据，请检查网络或API是否可用")
