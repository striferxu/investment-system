#!/usr/bin/env python3
"""
定时任务运行器：跑日常管线 → 推送结果到QQ
由 openclaw cron 触发
"""
import json
import sys
import subprocess
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
PIPELINE_SCRIPT = BASE_DIR / "pipeline" / "daily_run.py"


def run_pipeline():
    """执行 daily_run.py 并解析推送消息"""
    result = subprocess.run(
        [sys.executable, str(PIPELINE_SCRIPT), "--json"],
        capture_output=True, text=True, timeout=300,
        cwd=str(BASE_DIR),
    )

    # 解析JSON输出
    output = result.stdout
    push_data = None
    if "##PUSH_JSON##" in output:
        json_part = output.split("##PUSH_JSON##")[1].strip()
        push_data = json.loads(json_part)

    # 打印完整日志（会被cron捕获）
    print(output)
    if result.stderr:
        print(result.stderr, file=sys.stderr)

    return push_data, result.returncode


def send_qq_message(text, file_path=None):
    """通过 openclaw 发送QQ消息"""
    cmd = ["openclaw", "say", "--channel", "xiaoyi-channel", text]
    subprocess.run(cmd, timeout=30)

    # 如果有附件看板文件，也发送
    if file_path and Path(file_path).exists():
        cmd_file = ["openclaw", "say", "--channel", "xiaoyi-channel",
                     "--file", str(file_path)]
        subprocess.run(cmd_file, timeout=30)


if __name__ == "__main__":
    push_data, code = run_pipeline()

    if push_data:
        message = push_data.get("message", "")
        dashboard = push_data.get("dashboard_path", "")
        send_qq_message(message, dashboard)
        print(f"\n✅ 推送完成（{push_data.get('errors', 0)} 个错误）")
    else:
        # 管线失败了，发错误通知
        send_qq_message("⚠️ 投资系统日报运行失败，请检查服务器日志")
        print("\n❌ 管线运行失败，未获取到推送数据")
        sys.exit(1)
