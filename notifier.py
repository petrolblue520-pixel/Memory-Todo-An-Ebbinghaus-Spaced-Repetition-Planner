"""Windows 右下角通知 + 提示语组装 (无第三方依赖)。

- 通知走系统 Toast: 通过 PowerShell 调用 WinRT 的 ToastNotificationManager,
  用 -EncodedCommand (UTF-16LE base64) 传参, 彻底规避中文编码问题, 也不弹黑窗。
- 提示语放在 phrases/ 下的多个 txt 里, 每类随机抽一条, 组合成"问候语 + 任务提示"。
  * 问候语按时段分文件 (凌晨/早晨/上午/中午/下午/晚上)。
  * 任务提示分"有未完成"(含 * 占位, 替换成剩余条数) 与"全部完成"两类。
"""

import os
import base64
import random
import subprocess
from datetime import date, datetime

from models import Store

BASE = os.path.dirname(os.path.abspath(__file__))
PHRASE_DIR = os.path.join(BASE, "phrases")

CREATE_NO_WINDOW = 0x08000000

# 兜底文案 (万一 txt 缺失也能正常提醒)
_FALLBACK_GREET = ["你好呀"]
_FALLBACK_PENDING = ["今天还有*项任务没完成呢"]
_FALLBACK_DONE = ["太棒啦，所有任务都完成啦"]


def data_path():
    d = os.environ.get("EBBINGHAUS_DATA_DIR", os.path.join(BASE, "appdata"))
    return os.path.join(d, "data.json")


def _load_lines(fname, fallback):
    path = os.path.join(PHRASE_DIR, fname)
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = [ln.strip() for ln in f
                     if ln.strip() and not ln.strip().startswith("#")]
        if lines:
            return lines
    except OSError:
        pass
    return fallback


def _greet_file(hour):
    if 7 <= hour < 9:
        return "问候语_早晨7-9点.txt"
    if 9 <= hour < 13:
        return "问候语_上午9-13点.txt"
    if 13 <= hour < 17:
        return "问候语_中午13-17点.txt"
    if 17 <= hour < 20:
        return "问候语_下午17-20点.txt"
    if 20 <= hour < 24:
        return "问候语_晚上20-24点.txt"
    return "问候语_凌晨0-7点.txt"


def due_count(today=None):
    """今日待办总数: 到期的未完成复习 + 到期的未完成学习计划(绿卡)。"""
    today = today or date.today()
    store = Store(data_path())
    return store.due_task_count(today)


def task_phrase(n):
    """按剩余任务数随机返回一条任务提示 (n>0 用含 * 的模板并替换成数字, 否则用全部完成)。"""
    if n > 0:
        tmpl = random.choice(_load_lines("任务_有未完成.txt", _FALLBACK_PENDING))
        return tmpl.replace("*", str(n))
    return random.choice(_load_lines("任务_全部完成.txt", _FALLBACK_DONE))


def greeting(now=None):
    """按当前时段随机返回一句问候语。"""
    now = now or datetime.now()
    return random.choice(_load_lines(_greet_file(now.hour), _FALLBACK_GREET))


def build_message(now=None):
    """返回 (问候语, 任务提示)。"""
    now = now or datetime.now()
    return greeting(now), task_phrase(due_count(now.date()))


def _toast_script(title, body):
    t = title.replace("'", "''")
    b = body.replace("'", "''")
    # 用 PowerShell 的 App Id (已注册) 让通知正常出现在右下角 / 通知中心
    return (
        "$ErrorActionPreference='Stop';"
        "[Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,ContentType=WindowsRuntime]|Out-Null;"
        "[Windows.UI.Notifications.ToastNotification,Windows.UI.Notifications,ContentType=WindowsRuntime]|Out-Null;"
        "[Windows.Data.Xml.Dom.XmlDocument,Windows.Data.Xml.Dom,ContentType=WindowsRuntime]|Out-Null;"
        "$x=[Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
        "$e=$x.GetElementsByTagName('text');"
        f"$e.Item(0).AppendChild($x.CreateTextNode('{t}'))|Out-Null;"
        f"$e.Item(1).AppendChild($x.CreateTextNode('{b}'))|Out-Null;"
        "$toast=[Windows.UI.Notifications.ToastNotification]::new($x);"
        "$id='{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe';"
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($id).Show($toast);"
    )


def toast(title, body):
    """弹出一条 Windows 通知。失败时静默 (不影响主程序)。"""
    script = _toast_script(title, body)
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
            creationflags=CREATE_NO_WINDOW, timeout=20,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


def notify_tasks(now=None):
    """组装"问候语 + 任务提示"并弹出通知。"""
    greeting, task = build_message(now)
    toast(greeting, task)
    return greeting, task


if __name__ == "__main__":
    g, t = build_message()
    print("greeting:", g)
    print("task   :", t)
    toast(g, t)
    print("toast sent")
