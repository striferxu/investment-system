# 📊 投资系统 Investment System

> 基于多维度评分模型的行业ETF配置决策支持系统

## 概述

一套全自动的 **A股行业ETF配置辅助系统**，每天自动采集资金流、行业涨跌幅、ETF基本面、财经新闻等数据，通过 **三层维度加权 + 在线学习** 输出行业投资建议。

**核心思路：** 不预测涨跌，而是通过多个维度的交叉验证，识别当前最具配置价值的行业赛道。

## 系统架构

```
invest-system/
├── collector/            # 数据采集层
│   ├── capital_flow.py      # 北向/主力资金流（I03）
│   ├── industry_performance.py # 行业实际涨跌幅
│   └── etf_fundamentals.py  # ETF基本面（PE/PB、动量）
│
├── engine/               # 计算引擎层
│   ├── dimension_engine.py  # 维度引擎 — 加权评分
│   └── online_learning.py   # 在线学习 — 权重自适应
│
├── pipeline/             # 管线调度层
│   ├── news_collector.py    # 新闻采集（小艺联网搜索）
│   ├── event_classifier.py  # 事件分类 → 维度更新
│   ├── daily_run.py         # 每日主管线
│   └── cron_runner.py       # 定时任务运行器
│
├── web/                  # 可视化层
│   ├── server.py            # HTTP 看板服务
│   ├── dashboard_data.py    # 看板数据接口
│   └── generate_static.py   # 静态 HTML 看板生成
│
├── config/               # 静态配置
│   ├── dimensions.json         # 维度定义（宏观/行业/微观）
│   ├── industries.json         # 行业列表（含ETF映射）
│   ├── event_templates.json    # 事件模板（LLM+正则）
│   └── industry_api_mapping.json # API代码映射
│
├── data/                 # 运行数据（自动生成）
│   ├── snapshots/            # 每日系统快照
│   ├── recommendations/      # 历史推荐记录
│   ├── capital_flow/         # 资金流原始数据
│   ├── actual_performance/   # 行业实际涨跌幅
│   ├── stock_values/         # ETF基本面数据
│   ├── learning_log/         # 在线学习日志
│   ├── dashboard.html        # 静态看板
│   └── dashboard_latest.json # 最新看板数据
│
└── .gitignore
```

## 三层维度评分模型

系统将投资分析分为三层，每层包含若干维度，最终加权得出行业综合评分。

### 宏观层（M01~M04）— 权重 33.3%

| 维度 | 名称 | 描述 | 
|------|------|------|
| M01 | 经济景气度 | 经济数据驱动的宏观热度 |
| M02 | 货币宽松度 | 降息/降准等政策影响 |
| M03 | 市场风险偏好 | 恐慌 vs 贪婪 |
| M04 | 不确定性溢价 | 市场混沌程度 |

### 行业层（I01~I05）— 权重 33.3%

| 维度 | 名称 | 描述 | 数据源 |
|------|------|------|--------|
| I01 | 行业景气趋势 | 新闻事件驱动的景气判断 | 新闻采集 |
| I02 | 政策导向 | 政策利好/监管打压 | 新闻采集 |
| I03 | 资金聚集度 | 主力资金净流入/流出 | 东方财富资金流API |
| I04 | 估值合理性 | PE/PB历史分位 | ETF基本面数据 |
| I05 | 产业变革强度 | 技术突破/产业拐点 | 新闻采集 |

### 微观层（S01~S04）— 权重 33.3%

| 维度 | 名称 | 描述 |
|------|------|------|
| S01 | 基本面硬度 | PE历史位置（低PE→高分） |
| S02 | 相对价值 | 行业ETF PE vs 行业平均偏差 |
| S03 | 动量趋势 | 近20日涨跌标准化得分 |
| S04 | 催化剂密度 | 近期行业活跃事件统计 |

### 动态约束

系统内置三条风控规则：
- **动量衰减约束** — 动量维度权重上限15%，防止追涨杀跌
- **估值安全线约束** — 估值低但景气向下视为价值陷阱，评分打6折
- **不确定性风控** — 不确定性溢价>0.5时总仓位上限降至60%

## 在线学习

系统通过 **OnlineLearner** 每天对比"预测评分 vs 实际涨跌幅"，自动修正维度权重和置信度：

- 准确率高的维度 → 提高权重和置信度（下次影响更大）
- 准确率低的维度 → 降低权重和置信度（下次影响更小）
- 新维度 / 低置信度维度 → 学习率更高（更快适应）

学习日志存储在 `data/learning_log/` 中。

## 快速开始

### 前提

- Python 3.10+
- OpenClaw 环境（用于新闻采集和定时任务）

### 安装

```bash
cd investment-system
pip install -r requirements.txt   # 如果后续添加了依赖
```

### 单次运行

```bash
python3 pipeline/daily_run.py
```

### 启动 Web 看板

```bash
python3 web/server.py
# 访问 http://localhost:8899
```

### 生成静态看板

```bash
python3 web/generate_static.py
# 输出: data/dashboard.html（可离线查看）
```

### 定时任务

通过 OpenClaw cron 每日自动运行：

```bash
openclaw cron add \
  --name "invest-daily" \
  --cron "0 18 * * 1-5" \
  --timezone "Asia/Shanghai" \
  --channel xiaoyi-channel \
  -- python3 pipeline/cron_runner.py
```

## 数据源

| 数据 | 来源 | 说明 |
|------|------|------|
| 行业涨跌幅 | `emdatah5.eastmoney.com` | 东方财富移动端API |
| 资金流 | `emdatah5.eastmoney.com` + `datacenter.eastmoney.com` | 主力资金 + 北向资金 |
| ETF基本面 | `fundgz.1234567.com.cn` + `fund.eastmoney.com` | 实时/历史 PE、净值 |
| 财经新闻 | 小艺联网搜索 | OpenClaw 环境搜索采集 |
| 事件分类 | LLM 语义分类 + 正则兜底 | 优先LLM，降级到关键词匹配 |

## 技术栈

- **Python 3** — 核心语言
- **东方财富API** — 行情/资金流/ETF数据
- **OpenClaw / 小艺** — 新闻搜索与定时调度
- **纯静态 HTML** — 看板无需后端 API

## 输出示例

每日运行后生成推荐列表（`data/recommendations/YYYY-MM-DD.json`），按综合评分排序：

```json
{
  "date": "2026-06-23",
  "market_view": "中性",
  "industries": [
    {"name": "新能源汽车", "score": 0.33, "top_dimensions": ["政策导向+1.0", "产业变革+0.47"]},
    {"name": "人工智能",   "score": 0.32, "top_dimensions": ["政策导向+1.0", "产业变革+0.42"]},
    {"name": "食品饮料",   "score": 0.20, "top_dimensions": ["政策导向+0.64", "产业变革+0.27"]}
  ]
}
```

## 免责声明

⚠️ **本系统仅供学习和研究参考，不构成任何投资建议。** 投资有风险，入市需谨慎。过往业绩不代表未来表现。

## License

MIT
