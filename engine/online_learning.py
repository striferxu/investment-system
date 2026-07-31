#!/usr/bin/env python3
"""
在线学习模块
对比"预测 vs 实际"，自动修正维度权重和置信度。

核心思路（贝叶斯更新风格）：
1. 每天对比系统给出的行业评分和实际涨跌幅
2. 计算每个维度的"贡献准确率"
3. 准确率高的维度 → 提高其权重和置信度
4. 准确率低的维度 → 降低其权重和置信度
5. 新维度/低置信度维度 → 学习率更高（更快调整）
"""
import json
import sys
from pathlib import Path
from datetime import date, datetime, timedelta

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "engine"))
from dimension_engine import DimensionEngine

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


class OnlineLearner:
    def __init__(self, engine=None):
        if engine is None:
            self.engine = DimensionEngine()
        else:
            self.engine = engine

        self.learning_rate = 0.05  # 基础学习率
        self.decay_factor = 0.01   # 权重衰减（防止某个维度无限增大）
        self.epsilon = 0.001

    def load_actual_performance(self, date_str=None):
        """
        获取指定日期的实际市场表现。
        初期：从简化的模拟数据加载。
        后续：接入真实行情API。
        返回: dict { industry_code: actual_return }
        """
        if date_str is None:
            date_str = (date.today() - timedelta(days=1)).isoformat()

        # 先尝试加载实际表现数据
        perf_path = DATA_DIR / "actual_performance" / f"{date_str}.json"
        if perf_path.exists():
            with open(perf_path) as f:
                return json.load(f)

        # 如果没有，返回空（表明数据不可用）
        return {}

    def evaluate_prediction_accuracy(self, date_str=None):
        """评估某天的预测准确率"""
        if date_str is None:
            date_str = (date.today() - timedelta(days=1)).isoformat()

        # 加载当天的推荐(预测)
        rec_path = DATA_DIR / "recommendations" / f"{date_str}.json"
        if not rec_path.exists():
            print(f"  ⚠️ 未找到 {date_str} 的预测记录")
            return None

        with open(rec_path) as f:
            rec = json.load(f)

        # 加载实际表现
        actual = self.load_actual_performance(date_str)
        if not actual:
            print(f"  ⚠️ 未找到 {date_str} 的实际行情数据")
            return None

        predictions = {}
        for ind in rec.get("recommended_industries", []):
            code = ind["industry_code"]
            predictions[code] = ind["score"]

        # 计算每个行业的预测误差
        errors = {}
        for code, pred_score in predictions.items():
            actual_ret = actual.get(code, 0)
            # 将预测分数映射到预期涨跌幅（正分→看涨，负分→看跌）
            pred_direction = 1 if pred_score > 0 else (-1 if pred_score < 0 else 0)
            actual_direction = 1 if actual_ret > 0 else (-1 if actual_ret < 0 else 0)

            # 方向正确与否
            correct = pred_direction == actual_direction
            # 误差幅度
            magnitude_error = abs(pred_score - actual_ret)
            errors[code] = {
                "predicted_score": pred_score,
                "actual_return": actual_ret,
                "direction_correct": correct,
                "magnitude_error": magnitude_error,
            }

        # 统计整体准确率
        total = len(errors)
        correct_count = sum(1 for e in errors.values() if e["direction_correct"])
        accuracy = correct_count / total if total > 0 else 0

        return {
            "date": date_str,
            "accuracy": accuracy,
            "total_predictions": total,
            "correct_predictions": correct_count,
            "errors": errors,
        }

    def update_weights_from_error(self, date_str=None):
        """
        核心：根据预测误差修正维度权重。

        方法：对每个行业维度，看这个维度的值在预测当天的贡献。
        - 如果预测对了，这个维度的权重微涨
        - 如果预测错了，这个维度的权重微降
        - 调整幅度取决于该维度的置信度（低置信度调更多）
        """
        evaluation = self.evaluate_prediction_accuracy(date_str)
        if evaluation is None:
            return 0

        errors = evaluation.get("errors", {})
        if not errors:
            return 0

        adjustments = {}  # dim_id → 累计调整量

        for code, err in errors.items():
            if code not in self.engine.industry_values:
                continue

            vals = self.engine.industry_values[code]
            correct = err["direction_correct"]

            for dim_id, val in vals.items():
                if val == 0:
                    continue

                # 当前置信度
                conf = self.engine.dim_confidences.get(dim_id, 0.5)
                # 低置信度调更多，高置信度调更少
                conf_factor = 1.0 - conf + 0.2

                # 调整量
                if correct:
                    # 预测正确 → 权重微升
                    delta = self.learning_rate * conf_factor * abs(val)
                else:
                    # 预测错误 → 权重微降
                    delta = -self.learning_rate * conf_factor * abs(val) * 2.0

                if dim_id not in adjustments:
                    adjustments[dim_id] = 0
                adjustments[dim_id] += delta

                # 同时更新置信度
                if correct:
                    new_conf = conf + 0.01 * conf_factor
                else:
                    new_conf = conf - 0.02 * conf_factor
                self.engine.dim_confidences[dim_id] = max(0.2, min(1.0, new_conf))

        # 应用调整，并保权归一化（各层内部权重和为1）
        layer_dim_map = {}
        for layer_key in ["macro", "industry", "stock"]:
            layer_dim_map[layer_key] = [
                d["id"] for d in self.engine.cfg[layer_key]["dimensions"]
            ]

        for dim_id, delta in adjustments.items():
            current = self.engine.dim_weights.get(dim_id, 0)
            new_val = current + delta
            # 防止负权重
            new_val = max(self.epsilon, new_val)
            self.engine.dim_weights[dim_id] = new_val

        # 各层级内归一化
        for layer_key, dim_ids in layer_dim_map.items():
            total = sum(self.engine.dim_weights.get(d, 0) for d in dim_ids)
            if total > 0:
                for d in dim_ids:
                    self.engine.dim_weights[d] /= total

        update_count = len(adjustments)
        avg_accuracy = evaluation["accuracy"]

        # 记录学习过程
        learn_dir = DATA_DIR / "learning_log"
        learn_dir.mkdir(parents=True, exist_ok=True)
        log_entry = {
            "date": date_str,
            "accuracy": avg_accuracy,
            "adjustments": {k: round(v, 6) for k, v in adjustments.items()},
            "new_weights": {k: round(v, 4) for k, v in self.engine.dim_weights.items()},
            "new_confidences": {k: round(v, 4) for k, v in self.engine.dim_confidences.items()},
        }
        log_path = learn_dir / f"{date.today().isoformat()}.json"
        with open(log_path, "w") as f:
            json.dump(log_entry, f, ensure_ascii=False, indent=2)

        print(f"  → 准确率: {avg_accuracy:.1%} ({evaluation['correct_predictions']}/{evaluation['total_predictions']})")
        print(f"  → 更新了 {update_count} 个维度的权重")
        return update_count


def run_learning(date_str=None):
    """独立运行在线学习"""
    engine = DimensionEngine()
    learner = OnlineLearner(engine)

    # 尝试加载前一天的快照
    snap_date = date_str if date_str else (date.today() - timedelta(days=1)).isoformat()
    loaded = engine.load_snapshot(snap_date)
    if loaded:
        print(f"✅ 已加载 {snap_date} 快照")
    else:
        print(f"⚠️ 未找到 {snap_date} 快照，使用初始状态")

    n = learner.update_weights_from_error(snap_date)
    if n:
        print(f"✅ 权重更新完成，{n} 个维度已调整")
        # 保存学习后的快照
        engine.save_snapshot()
        print(f"✅ 更新后的快照已保存")
    else:
        print("⏭️ 无更新")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="要评估的日期 YYYY-MM-DD")
    args = parser.parse_args()
    run_learning(args.date)
