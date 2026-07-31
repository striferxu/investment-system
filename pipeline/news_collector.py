#!/usr/bin/env python3
"""
新闻采集模块 — 用 xiaoyi-web-search 获取真实金融资讯
"""
import json
import sys
import subprocess
from pathlib import Path
from datetime import date

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


SEARCH_SCRIPT = Path.home() / ".openclaw/workspace/skills/xiaoyi-web-search/scripts/search.js"


def search_news(query, max_results=5):
    """调用 xiaoyi-web-search 搜索新闻"""
    cmd = ["node", str(SEARCH_SCRIPT), query, "-n", str(max_results)]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            # 解析输出 - 每行一条结果，包含标题+链接+摘要
            lines = result.stdout.strip().split("\n")
            items = []
            current = {}
            for line in lines:
                line = line.strip()
                if not line:
                    if current.get("title"):
                        items.append(current)
                    current = {}
                    continue
                if line.startswith("📌"):
                    if current.get("title"):
                        items.append(current)
                    current = {"title": line[2:].strip()}
                elif line.startswith("📝"):
                    current["content"] = line[2:].strip()
            if current.get("title"):
                items.append(current)

            return [
                item.get("title", "") + "，" + item.get("content", "")
                for item in items[:max_results]
            ]
        else:
            print(f"  ⚠️ 搜索失败: {result.stderr[:200]}")
            return []
    except Exception as e:
        print(f"  ⚠️ 搜索异常: {e}")
        return []


def collect_real_news():
    """采集当天各类金融新闻"""
    queries = [
        "A股 今日 重大政策 新闻 2026年",
        "宏观经济 最新数据 政策 2026",
        "主力资金 北向资金 板块流向 今日",
        "行业政策 利好 利空 最新",
    ]

    all_news = []
    seen = set()

    for q in queries:
        print(f"  → 搜索: {q}")
        items = search_news(q)
        for item in items:
            # 简单去重
            key = item[:50]
            if key not in seen:
                seen.add(key)
                all_news.append(item)

    return all_news


if __name__ == "__main__":
    news = collect_real_news()
    print(f"\n获取到 {len(news)} 条新闻:")
    for i, n in enumerate(news, 1):
        print(f"  {i}. {n[:100]}...")
