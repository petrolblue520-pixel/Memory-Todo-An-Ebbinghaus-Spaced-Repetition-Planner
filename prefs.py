"""桌面苍蝇的用户偏好设置 (纯逻辑, 不 import tkinter / ctypes)。

- 设置与任务存档分开: appdata/prefs.json (任务存档是 data.json)。
- DEFAULTS 是唯一事实来源; 读盘时逐字段合并 + 钳制, 缺字段/坏字段一律回退默认;
  未识别的字段原样保留 (旧版本降级 / 新版本回退都不会丢配置)。
- 改动通过 subscribe() 广播: 设置窗 / 托盘菜单 / 苍蝇群 订阅同一个 Prefs 对象,
  所以"两处开关不一致"这种事在结构上不可能发生。
"""

from __future__ import annotations

import json
import math
import os
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

# ---------------------------------------------------------------- 默认值
# !!! 唯一事实来源: 加一个设置项 = 在 DEFAULTS 加一行 (需要范围/枚举时再补 RANGES/CHOICES) !!!
DEFAULTS: Dict[str, Any] = {
    "version": 1,

    # 总开关 / 层级 / 规模
    "enabled": True,               # 是否显示桌面苍蝇
    "layer": "desktop",            # desktop = 只在桌面级(会被窗口盖住); topmost = 最上层
    "max_entities": 15,            # 苍蝇 + 卵 在桌面上的总数上限
    "fly_scale": 1.0,              # 苍蝇大小倍率
    "show_eggs": True,             # 是否显示卵 (隐藏时仍在倒计时)
    "hatch_seconds": 3600,         # 卵孵化时长(秒); 设置窗用 时/分 两个输入框合成

    # 运动参数
    "speed_crawl": 60.0,           # 爬行速度 px/s
    "speed_fly": 600.0,            # 飞行速度 px/s
    "flee_factor": 1.0,            # 被鼠标吓到时的加速倍率
    "rest_seconds": 2.0,           # 每次"停一下"的时长(秒); 输入式, 输入多少是多少
    "flee_radius": 110.0,          # 鼠标进到这个半径内就开始逃 px
    "wing_hz": 22.0,               # 振翅相位切换频率

    # 苍蝇拍
    "swatter": False,              # 苍蝇拍模式 (开启后才能拍死苍蝇)
    "swatter_hit_radius": 44.0,    # 拍打命中半径 px
    "swatter_swing": True,         # 挥拍动画
    "swatter_scale": 1.0,          # 拍子大小倍率
    "swatter_desktop_only": True,  # 只在桌面可见时才显示拍子

    # 性能 / 隐私
    "pause_when_covered": True,    # 被窗口盖住时暂停动画 (省电主力)
    "pause_when_idle": True,       # 锁屏 / 长时间无操作时暂停
    "battery_low_fps": True,       # 电池供电时降帧
    "hide_from_capture": True,     # 截屏 / 录屏里隐身

    # 出场
    "spawn_mode": "taskbar",       # taskbar=任务栏随机位置 | tray=托盘区 | mixed=随机爬出/飞出
}

# 数值范围 (最小, 最大); 最大为 None 表示不设上限
RANGES: Dict[str, Tuple[float, Optional[float]]] = {
    "max_entities": (1, 30),
    "fly_scale": (0.6, 1.8),
    "hatch_seconds": (0, 30 * 24 * 3600),
    "speed_crawl": (10.0, 200.0),
    "speed_fly": (100.0, 1500.0),
    "flee_factor": (0.5, 3.0),
    "rest_seconds": (0.0, None),   # 输入式: 不设上限
    "flee_radius": (40.0, 250.0),
    "wing_hz": (8.0, 40.0),
    "swatter_hit_radius": (20.0, 90.0),
    "swatter_scale": (0.6, 1.6),
}

# 枚举型字段的合法取值 (不在其中 -> 回退默认)
CHOICES: Dict[str, Tuple[str, ...]] = {
    "layer": ("desktop", "topmost"),
    "spawn_mode": ("taskbar", "tray", "mixed"),
}

# 孵化时间: 分钟必须 < 60, 小时给个宽松上限
HATCH_MIN_MAX = 59
HATCH_HOUR_MAX = 30 * 24

# 落盘节流 (秒): 拖滑块时不要每秒写几十次盘
_SAVE_THROTTLE = 0.4


def _num(value: Any, default: float, low: Optional[float] = None,
         high: Optional[float] = None) -> float:
    """把任意输入洗成有限数字; 失败/NaN/Inf 一律回退 default, 再按范围钳制。"""
    if value is None or isinstance(value, bool):
        return float(default)
    try:
        f = float(str(value).strip())
    except (TypeError, ValueError):
        return float(default)
    if math.isnan(f) or math.isinf(f):
        return float(default)
    if low is not None and f < low:
        f = low
    if high is not None and f > high:
        f = high
    return f


def coerce(key: str, value: Any) -> Any:
    """按 DEFAULTS / RANGES / CHOICES 把外部来的值洗成合法值。"""
    if key not in DEFAULTS:
        return value                     # 未知字段: 原样保留 (向前兼容)
    default = DEFAULTS[key]
    if key in CHOICES:
        text = str(value)
        return text if text in CHOICES[key] else default
    if isinstance(default, bool):
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on", "是", "开")
        return bool(value)
    if key in RANGES:
        low, high = RANGES[key]
        got = _num(value, default, low, high)
        return int(round(got)) if isinstance(default, int) else got
    if isinstance(default, int):
        return int(round(_num(value, default)))
    if isinstance(default, float):
        return _num(value, default)
    return value if isinstance(value, type(default)) else default


# ---------------------------------------------------------------- 孵化时间
def split_hatch(seconds: Any) -> Tuple[int, int]:
    """秒 -> (小时, 分钟)。"""
    total = max(0, int(round(_num(seconds, DEFAULTS["hatch_seconds"], 0))))
    return total // 3600, (total % 3600) // 60


def join_hatch(hours: Any, minutes: Any) -> int:
    """(小时, 分钟) -> 秒。调用方应先校验分钟; 这里做兜底截断。"""
    h = int(round(_num(hours, 0, 0, HATCH_HOUR_MAX)))
    m = int(round(_num(minutes, 0, 0, HATCH_MIN_MAX)))
    return h * 3600 + m * 60


def minutes_ok(minutes: Any) -> bool:
    """设置窗用: 分钟是否为 0..59 的整数 (你要求的 min < 60 校验)。"""
    if minutes is None or isinstance(minutes, bool):
        return False
    try:
        m = float(str(minutes).strip())
    except (TypeError, ValueError):
        return False
    return math.isfinite(m) and m.is_integer() and 0 <= m <= HATCH_MIN_MAX


def hours_ok(hours: Any) -> bool:
    """设置窗用: 小时是否为 0..HATCH_HOUR_MAX 的整数。"""
    if hours is None or isinstance(hours, bool):
        return False
    try:
        h = float(str(hours).strip())
    except (TypeError, ValueError):
        return False
    return math.isfinite(h) and h.is_integer() and 0 <= h <= HATCH_HOUR_MAX


# ---------------------------------------------------------------- 设置对象
class Prefs:
    """一份偏好设置 + 原子落盘 + 变更广播。只在 Tk 主线程里用。"""

    def __init__(self, path: Optional[str] = None, load: bool = True):
        self.path = path
        self._data: Dict[str, Any] = dict(DEFAULTS)
        self._extra: Dict[str, Any] = {}     # 未识别字段, 原样保留
        self._subs: List[Callable[[str], None]] = []
        self._last_save = 0.0
        if path and load:
            self.load()

    # ---------------- 读 ----------------
    def get(self, key: str, default: Any = None) -> Any:
        if key in self._data:
            return self._data[key]
        if key in self._extra:
            return self._extra[key]
        return DEFAULTS.get(key, default)

    def __getitem__(self, key: str) -> Any:
        return self.get(key)

    def as_dict(self) -> Dict[str, Any]:
        """完整配置 (含未识别字段), 用于落盘 / 调试。"""
        merged = dict(self._data)
        merged.update(self._extra)
        return merged

    # ---------------- 写 ----------------
    def set(self, key: str, value: Any, notify: bool = True,
            save: bool = True) -> bool:
        """写入一项; 返回是否真的发生了变化 (用于避免无意义的重建)。"""
        if key in DEFAULTS:
            new = coerce(key, value)
        else:
            new = value
        old = self.get(key)
        if key not in DEFAULTS and old == new:
            return False
        if type(old) is type(new) and old == new:
            return False
        if key in DEFAULTS:
            self._data[key] = new
        else:
            self._extra[key] = new
        if notify:
            self._notify(key)
        if save:
            self.save()
        return True

    def update(self, mapping: Dict[str, Any], notify: bool = True,
               save: bool = True) -> List[str]:
        """批量写入; 返回真正变化的键 (批量时只广播一次)。"""
        changed = [k for k in mapping if self.set(k, mapping[k], notify=False, save=False)]
        if changed and notify:
            for k in changed:
                self._notify(k)
        if changed and save:
            self.save()
        return changed

    def reset(self, notify: bool = True) -> None:
        """恢复默认 (未识别字段保留)。"""
        self._data = dict(DEFAULTS)
        if notify:
            self._notify("*")
        self.save(force=True)

    # ---------------- 变更广播 ----------------
    def subscribe(self, fn: Callable[[str], None]) -> Callable[[], None]:
        """订阅变更, 返回退订函数。"""
        self._subs.append(fn)

        def unsubscribe() -> None:
            try:
                self._subs.remove(fn)
            except ValueError:
                pass
        return unsubscribe

    def _notify(self, key: str) -> None:
        for fn in list(self._subs):
            try:
                fn(key)
            except Exception:
                pass                          # 订阅者出错不能影响别人

    # ---------------- 落盘 ----------------
    def load(self) -> None:
        """读盘; 任何异常一律回退默认值 (坏文件不能让程序起不来)。"""
        self._data = dict(DEFAULTS)
        self._extra = {}
        if not self.path:
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, ValueError):
            return
        if not isinstance(raw, dict):
            return
        for key, value in raw.items():
            if key in DEFAULTS:
                self._data[key] = coerce(key, value)
            else:
                self._extra[key] = value

    def save(self, force: bool = False) -> None:
        """原子写 (临时文件 + replace), 默认按 _SAVE_THROTTLE 节流。"""
        if not self.path:
            return
        now = time.monotonic()
        if not force and (now - self._last_save) < _SAVE_THROTTLE:
            return
        self._last_save = now
        tmp = self.path + ".tmp"
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.as_dict(), f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except OSError:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass

    def flush(self) -> None:
        """退出 / 关窗时强制落盘 (把节流窗口内的最后一次改动写下去)。"""
        self.save(force=True)
