#!/usr/bin/env python3
"""
投资系统 — 维度引擎
管理所有维度的值、权重、置信度，负责加权运算和分层评分。
"""
import json
import os
import copy
from pathlib import Path
from datetime import datetime, date

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"


class DimensionEngine:
    def __init__(self, config_path=None, industries_path=None):
        if config_path is None:
            config_path = CONFIG_DIR / "dimensions.json"
        if industries_path is None:
            industries_path = CONFIG_DIR / "industries.json"
        with open(config_path) as f:
            self.cfg = json.load(f)

        # 加载行业列表，初始化行业维度值
        self.industry_list = []
        try:
            with open(industries_path) as f:
                ind_cfg = json.load(f)
            self.industry_list = ind_cfg.get("industries", [])
        except (FileNotFoundError, json.JSONDecodeError):
            pass

        # 层级权重
        self.layer_weights = {
            "macro": self.cfg["macro"]["weight"],
            "industry": self.cfg["industry"]["weight"],
            "stock": self.cfg["stock"]["weight"],
        }

        # 维度模板索引：dim_id → {layer, name, range, confidence, desc}
        self.dim_templates = {}
        for layer_key in ["macro", "industry", "stock"]:
            for d in self.cfg[layer_key]["dimensions"]:
                self.dim_templates[d["id"]] = {
                    "layer": layer_key,
                    "name": d["name"],
                    "range": d["range"],
                    "confidence": d["confidence"],
                    "desc": d["desc"],
                }

        # 约束
        self.constraints = self.cfg.get("dynamic_constraints", {})

        # --- 运行时数据 ---
        # 宏观维度值
        self.macro_values = {
            d["id"]: d.get("initial", 0.0)
            for d in self.cfg["macro"]["dimensions"]
        }
        # 行业维度值: { industry_code: { dim_id: value } }
        self.industry_values = {}
        # 标的维度值: { stock_code: { dim_id: value } }
        self.stock_values = {}

        # 维度权重（可调）
        self.dim_weights = {}  # dim_id → weight
        for layer_key in ["macro", "industry", "stock"]:
            for d in self.cfg[layer_key]["dimensions"]:
                self.dim_weights[d["id"]] = 1.0 / len(self.cfg[layer_key]["dimensions"])

        # 置信度
        self.dim_confidences = {
            d["id"]: d["confidence"]
            for layer_key in ["macro", "industry", "stock"]
            for d in self.cfg[layer_key]["dimensions"]
        }

        # 行业维度初始化（从行业列表自动填充）
        if not self.industry_values:
            for ind in self.industry_list:
                code = ind["code"]
                if code not in self.industry_values:
                    self.industry_values[code] = {
                        d["id"]: 0.0 for d in self.cfg["industry"]["dimensions"]
                    }

        # 时效队列: 用于跟踪事件影响的衰减
        self.active_event_impacts = []  # [{dim_id, target, delta_per_day, remaining_days, source}]

    # ─── 维度值操作 ───

    def set_macro(self, dim_id, value):
        self._clamp_and_set(self.macro_values, dim_id, value)

    def set_industry(self, industry_code, dim_id, value):
        if industry_code not in self.industry_values:
            self.industry_values[industry_code] = {
                d["id"]: 0.0 for d in self.cfg["industry"]["dimensions"]
            }
        self._clamp_and_set(self.industry_values[industry_code], dim_id, value)

    def set_stock(self, stock_code, dim_id, value):
        if stock_code not in self.stock_values:
            self.stock_values[stock_code] = {
                d["id"]: 0.0 for d in self.cfg["stock"]["dimensions"]
            }
        self._clamp_and_set(self.stock_values[stock_code], dim_id, value)

    def _clamp_and_set(self, container, key, value):
        template = self.dim_templates.get(key)
        if template:
            lo, hi = template["range"]
            container[key] = max(lo, min(hi, value))
        else:
            container[key] = value

    # ─── 时效衰减（每天调用一次）───

    def tick_decay(self):
        """让所有活跃的事件影响衰减一天"""
        still_active = []
        for imp in self.active_event_impacts:
            imp["remaining_days"] -= 1
            if imp["remaining_days"] > 0:
                still_active.append(imp)
        self.active_event_impacts = still_active

    @property
    def active_event_impacts_by_industry(self):
        """将事件影响列表转换为 {code: {dim_id: impact, ...}} 格式
        供 ETF 采集 S04 催化剂密度计算使用
        """
        result = {}
        for imp in self.active_event_impacts:
            target = imp.get("target", "")
            if target and str(target).startswith("industry:"):
                code = str(target).split(":", 1)[1]
                if code not in result:
                    result[code] = {}
                impact = imp["delta_per_day"] * max(1, imp["remaining_days"])
                result[code][imp["dim_id"]] = result[code].get(imp["dim_id"], 0) + impact
        return result

    def add_event_impact(self, dim_id, target, delta_per_day, total_days, source=""):
        """添加一个时效影响（自动去重：同source+dim_id+target刷新剩余天数）"""
        for imp in self.active_event_impacts:
            if (imp["source"] == source
                    and imp["dim_id"] == dim_id
                    and imp["target"] == target):
                imp["remaining_days"] = max(imp["remaining_days"], total_days)
                return
        self.active_event_impacts.append({
            "dim_id": dim_id,
            "target": target,  # None=宏观, "industry:code"=行业, "stock:code"=标的
            "delta_per_day": delta_per_day,
            "remaining_days": total_days,
            "source": source,
        })

    # ─── 综合评分 ───

    def score_macro(self):
        """宏观层得分"""
        total = 0.0
        weight_sum = 0.0
        for dim_id, val in self.macro_values.items():
            w = self.dim_weights.get(dim_id, 0.25) * self.dim_confidences.get(dim_id, 0.5)
            total += val * w
            weight_sum += w
        return total / weight_sum if weight_sum > 0 else 0.0

    def score_industry(self, industry_code):
        """特定行业得分"""
        if industry_code not in self.industry_values:
            return 0.0
        vals = self.industry_values[industry_code]
        total = 0.0
        weight_sum = 0.0
        for dim_id, val in vals.items():
            w = self.dim_weights.get(dim_id, 0.2) * self.dim_confidences.get(dim_id, 0.5)
            total += val * w
            weight_sum += w

        # 估值安全线约束：估值低(I04负) + 景气向下(I01负) = 价值陷阱打折
        constraint = self.constraints.get("value_trap_protection", {})
        if constraint.get("active", False):
            i04_neg = vals.get("I04", 0) < 0
            i01_neg = vals.get("I01", 0) < 0
            trigger = constraint.get("trigger_when", {})
            if trigger.get("I04_negative", False) and i04_neg and trigger.get("I01_negative", False) and i01_neg:
                total *= constraint.get("penalty_factor", 0.6)

        return total / weight_sum if weight_sum > 0 else 0.0

    def score_stock(self, stock_code):
        """特定标的得分"""
        if stock_code not in self.stock_values:
            return 0.0
        vals = self.stock_values[stock_code]
        total = 0.0
        weight_sum = 0.0
        for dim_id, val in vals.items():
            w = self.dim_weights.get(dim_id, 0.25) * self.dim_confidences.get(dim_id, 0.5)
            # 动量衰减约束
            constraint = self.constraints.get("momentum_decay", {})
            if constraint.get("active", False) and dim_id == "S03":
                w = min(w, constraint.get("max_weight", 0.15))
            total += val * w
            weight_sum += w
        return total / weight_sum if weight_sum > 0 else 0.0

    def overall_score(self, industry_code, stock_code=None):
        """三级综合得分：宏观 + 行业 + 微观"""
        macro_s = self.score_macro()
        industry_s = self.score_industry(industry_code)

        # 不确定性风控：M04 > 0.5 时限制仓位
        uncertainty = self.macro_values.get("M04", 0)
        position_limit = 1.0
        rc = self.constraints.get("uncertainty_risk_control", {})
        if rc.get("active", False) and uncertainty > rc.get("trigger_when", {}).get("M04_gt", 0.5):
            position_limit = rc.get("max_position", 0.6)

        stock_s = self.score_stock(stock_code) if stock_code else 0.0
        raw = (
            macro_s * self.layer_weights["macro"]
            + industry_s * self.layer_weights["industry"]
            + stock_s * self.layer_weights["stock"]
        )
        # 应用不确定性仓位限制
        weighted = raw * position_limit
        return {
            "overall": round(weighted, 4),
            "macro_score": round(macro_s, 4),
            "industry_score": round(industry_s, 4),
            "stock_score": round(stock_s, 4),
            "position_limit": round(position_limit, 2),
            "uncertainty": round(uncertainty, 4),
        }

    # ─── 排名与建议 ───

    def score_all_layers(self, industry_code):
        """三层综合评分：宏观 + 行业 + 微观"""
        macro_s = self.score_macro()
        industry_s = self.score_industry(industry_code)

        # 微观层：如果有该行业的 stock_value 则计算，否则返回0
        stock_s = 0.0
        if industry_code in self.stock_values:
            vals = self.stock_values[industry_code]
            total = 0.0
            weight_sum = 0.0
            for dim_id, val in vals.items():
                w = self.dim_weights.get(dim_id, 0.25) * self.dim_confidences.get(dim_id, 0.5)
                # 动量衰减约束
                constraint = self.constraints.get("momentum_decay", {})
                if constraint.get("active", False) and dim_id == "S03":
                    w = min(w, constraint.get("max_weight", 0.15))
                total += val * w
                weight_sum += w
            stock_s = total / weight_sum if weight_sum > 0 else 0.0

        return macro_s, industry_s, stock_s

    def rank_industries(self, top_n=5, include_stock=True):
        """返回得分最高的N个行业"""
        scores = []
        for code in self.industry_values:
            if include_stock:
                _, ind_s, stock_s = self.score_all_layers(code)
                # 微观层已有数据才计入总分
                if code in self.stock_values:
                    combined = (ind_s * self.layer_weights["industry"]
                                + stock_s * self.layer_weights["stock"])
                    # 归一化回 [0,1] 尺度，保证和原 ind_s 可比
                    combined = combined / (self.layer_weights["industry"] + self.layer_weights["stock"])
                    scores.append((code, combined))
                else:
                    scores.append((code, ind_s))
            else:
                s = self.score_industry(code)
                scores.append((code, s))
        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:top_n]

    def recommend(self, top_industries=3):
        """生成投资建议（含微观层）"""
        # 宏观评估
        macro_s = self.score_macro()
        uncertainty = self.macro_values.get("M04", 0)

        # 整体市场判断
        if macro_s > 0.3:
            market_view = "看多"
        elif macro_s > -0.2:
            market_view = "中性"
        else:
            market_view = "谨慎"

        # 限制仓位
        position_limit = 1.0
        rc = self.constraints.get("uncertainty_risk_control", {})
        if rc.get("active", False) and uncertainty > rc.get("trigger_when", {}).get("M04_gt", 0.5):
            position_limit = rc.get("max_position", 0.6)

        # 推荐行业（含微观层评分）
        top = self.rank_industries(top_industries)
        recs = []
        for code, score in top:
            entry = {
                "industry_code": code,
                "score": score,
                "details": self.industry_values.get(code, {}),
            }
            # 如果有微观层数据，附带
            if code in self.stock_values:
                entry["stock_details"] = self.stock_values[code]
            recs.append(entry)

        return {
            "date": date.today().isoformat(),
            "market_view": market_view,
            "macro_score": macro_s,
            "position_limit": position_limit,
            "recommended_industries": recs,
            "has_stock_layer": bool(self.stock_values),
        }

    # ─── 持久化 ───

    def save_snapshot(self):
        today = date.today().isoformat()
        data = {
            "date": today,
            "macro_values": self.macro_values,
            "dim_weights": self.dim_weights,
            "dim_confidences": self.dim_confidences,
            "industry_values": self.industry_values,
            "stock_values": self.stock_values,
            "active_event_impacts": self.active_event_impacts,
        }
        snap_dir = DATA_DIR / "snapshots"
        snap_dir.mkdir(parents=True, exist_ok=True)
        path = snap_dir / f"{today}.json"
        with open(path, "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return path

    def load_snapshot(self, date_str=None):
        if date_str is None:
            date_str = date.today().isoformat()
        path = DATA_DIR / "snapshots" / f"{date_str}.json"
        if not path.exists():
            return False
        with open(path) as f:
            data = json.load(f)
        self.macro_values = data["macro_values"]
        self.dim_weights = data["dim_weights"]
        self.dim_confidences = data["dim_confidences"]
        self.industry_values = data["industry_values"]
        self.stock_values = data.get("stock_values", {})
        self.active_event_impacts = data.get("active_event_impacts", [])
        return True

    def load_stock_values(self, stock_vals):
        """加载外部采集的微观层数据"""
        if stock_vals:
            self.stock_values = stock_vals
            print(f"  ✅ 已加载 {len(stock_vals)} 个行业的微观维度")
        return self


if __name__ == "__main__":
    engine = DimensionEngine()
    # 测试：设置一些初始值
    engine.set_macro("M01", 0.3)
    engine.set_macro("M02", 0.2)
    engine.set_macro("M03", 0.1)
    engine.set_macro("M04", 0.15)
    engine.set_industry("BK0477", "I01", 0.6)
    engine.set_industry("BK0477", "I02", 0.8)
    engine.set_industry("BK0477", "I04", -0.2)
    engine.set_industry("BK0489", "I01", 0.4)
    engine.set_industry("BK0489", "I02", 0.7)
    rec = engine.recommend()
    print(json.dumps(rec, ensure_ascii=False, indent=2))
