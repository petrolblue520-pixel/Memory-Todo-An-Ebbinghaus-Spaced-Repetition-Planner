"""定点提醒代理: 启动即发一条通知, 之后在每个整点触发时刻各发一条。

触发时刻 (整点): 0点(即"24点") / 7点 / 9点 / 13点 / 17点 / 20点。
每次都重新读取数据, 组装"问候语 + 剩余任务提示"并弹 Windows 右下角通知。

用法:
    pythonw reminder.py        # 常驻后台, 定点提醒 (开机自启用这个)
    python  reminder.py --once # 只发一条立即退出 (测试/排错用)
"""

import sys
import time
from datetime import datetime, timedelta

import notifier

# 需要定点提醒的整点 (0 即 24 点)
TRIGGER_HOURS = [0, 7, 9, 13, 17, 20]


def next_trigger(now):
    """返回 now 之后最近的一个触发时刻 (整点)。"""
    candidates = []
    for day_offset in (0, 1):
        d = (now + timedelta(days=day_offset)).date()
        for h in TRIGGER_HOURS:
            t = datetime(d.year, d.month, d.day, h, 0, 0)
            if t > now:
                candidates.append(t)
    return min(candidates)


def run_loop():
    # 启动先发一条 (让用户知道程序已在运行, 也顺带提醒当前待办)
    notifier.notify_tasks()
    while True:
        target = next_trigger(datetime.now())
        # 分段小睡, 对系统休眠/唤醒、改时间更鲁棒
        while True:
            remaining = (target - datetime.now()).total_seconds()
            if remaining <= 0:
                break
            time.sleep(min(remaining, 30))
        notifier.notify_tasks()


def main():
    if "--once" in sys.argv:
        g, t = notifier.notify_tasks()
        print("已发送通知:", g, "|", t)
        return
    run_loop()


if __name__ == "__main__":
    main()
