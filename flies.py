"""桌面苍蝇: 按"今日待完成任务数"在桌面上游荡的小苍蝇。

    ╔══════════════════════════════════════════════════════════════════════╗
    ║ 不变量:  苍蝇数 + 卵数  ==  min(今日待完成数 n, 上限 max_entities)      ║
    ╚══════════════════════════════════════════════════════════════════════╝

- 每个实体 (苍蝇 / 卵) 绑定一张"今天要打钩的卡片", 键来自 models.Store.due_keys();
- 任务完成 -> 键消失 -> 绑定它的苍蝇飞走 / 卵消失 (不需要任何额外同步);
- 拍死苍蝇 -> 原地留一个卵, 绑定不变; 卵倒计时结束后原地孵出苍蝇;
- 上限调小 -> 超出的实体立即消失 (苍蝇和卵一样处理, 不变成卵)。

行为:
    EMERGE(从任务栏随机位置爬出/飞出) -> CRAWL <-> REST <-> FLY
    鼠标靠近 -> FLEE (有反应延迟, 所以手快能拍到)
    SWATTED(淡出, 留卵) -> EGG(倒计时) -> HATCH(原地钻出来) -> CRAWL

本模块不 import tkinter 的控件, 只用 root.after / root.winfo_pointerxy,
所以窗口生命周期完全由 Tk 主循环驱动 (和 tray.py 一个套路, 单线程, 无锁)。
"""

from __future__ import annotations

import ctypes
import json
import math
import os
import random
import time
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import flyart
import flywin
import swatter as swatter_mod

try:
    _user32 = ctypes.windll.user32
    _kernel32 = ctypes.windll.kernel32
except Exception:                                    # pragma: no cover
    _user32 = _kernel32 = None

ART_VERSION = flyart.ART_VERSION
CACHE_NAME = "flyart_v%d.bin" % ART_VERSION
STATE_NAME = "flies.json"

# ---- 时间常数 (秒) ----
EMERGE_TIME = 1.35          # 从任务栏钻出来的时长
DIE_TIME = 0.34             # 拍死淡出
EGG_IN_TIME = 0.22          # 卵淡入
HATCH_TIME = 0.30           # 破壳
TASK_GONE_FADE = 0.30       # 任务完成 -> 实体消失的淡出
REST_MIN_GAP = 1.8          # 两次"静止"之间至少爬多久
REST_MAX_GAP = 5.5
FLY_MIN_GAP = 2.2           # 两次"飞一下"之间至少爬多久
FLY_MAX_GAP = 7.0
FLY_TIME = (0.45, 1.1)      # 单次飞行时长
FLEE_DELAY = (0.10, 0.22)   # 反应延迟: 手快就能拍到
FLEE_KEEP = 0.45            # 鼠标走开后还要继续逃一会儿
GAIT_PERIOD = 0.13          # 换一次腿的周期
VISIBILITY_PERIOD = 0.5     # 多久检查一次"我是不是被盖住了"
RESYNC_PERIOD = 20.0        # 多久重新对账一次任务 (兜底跨天)


# ---------------------------------------------------------------- 系统小工具
class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


class _POWERSTATUS(ctypes.Structure):
    _fields_ = [("ACLineStatus", ctypes.c_byte), ("BatteryFlag", ctypes.c_byte),
                ("BatteryLifePercent", ctypes.c_byte), ("SystemStatusFlag", ctypes.c_byte),
                ("BatteryLifeTime", ctypes.c_ulong), ("BatteryFullLifeTime", ctypes.c_ulong)]


def _idle_seconds() -> float:
    """距上一次键鼠输入过了多少秒 (拿不到就返回 0)。"""
    if _user32 is None:
        return 0.0
    try:
        info = _LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(_LASTINPUTINFO)
        if not _user32.GetLastInputInfo(ctypes.byref(info)):
            return 0.0
        return max(0.0, (_kernel32.GetTickCount() - info.dwTime) / 1000.0)
    except Exception:
        return 0.0


def _on_battery() -> bool:
    """是否用电池供电 (ACLineStatus == 0); 拿不到就当接了电源。"""
    if _kernel32 is None:
        return False
    try:
        st = _POWERSTATUS()
        if not _kernel32.GetSystemPowerStatus(ctypes.byref(st)):
            return False
        return st.ACLineStatus == 0
    except Exception:
        return False


def _taskbar_rect():
    """任务栏矩形 (left, top, right, bottom); 找不到返回 None。"""
    if _user32 is None:
        return None
    try:
        _user32.FindWindowW.restype = ctypes.c_void_p
        hwnd = _user32.FindWindowW("Shell_TrayWnd", None)
        if not hwnd:
            return None
        r = ctypes.wintypes.RECT()
        if not _user32.GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(r)):
            return None
        return (r.left, r.top, r.right, r.bottom)
    except Exception:
        return None


def _class_name_of(hwnd) -> str:
    try:
        buf = ctypes.create_unicode_buffer(256)
        _user32.GetClassNameW(ctypes.c_void_p(hwnd), buf, 256)
        return buf.value
    except Exception:
        return ""


def _foreground_rect():
    """前台窗口的矩形与类名; 拿不到返回 (None, "")。"""
    if _user32 is None:
        return None, ""
    try:
        hwnd = _user32.GetForegroundWindow()
        if not hwnd:
            return None, ""
        r = ctypes.wintypes.RECT()
        if not _user32.GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(r)):
            return None, ""
        return (r.left, r.top, r.right, r.bottom), _class_name_of(hwnd)
    except Exception:
        return None, ""


# ---------------------------------------------------------------- 帧缓存
class FlyArt:
    """苍蝇 / 卵 / 苍蝇拍的位图帧: 先查内存, 再查磁盘缓存, 最后才现算。

    现算一帧约 10ms, 全部 72 帧约 0.8s; 所以:
      - 有磁盘缓存 -> 启动直接读回来 (几毫秒);
      - 没有 -> 边跑边在 tick 里用时间预算预热 (每个 tick 最多 4ms), 不卡界面。
    """

    def __init__(self, data_dir: str):
        self.cache_path = os.path.join(data_dir, CACHE_NAME)
        self._frames: Dict[Tuple[str, int, int], bytes] = {}
        self._key = None
        self._warm_todo: List[Tuple[str, int, int]] = []
        self._since_save = 0
        self._dirty = False
        self.size = 0
        self.scale = 1.0
        self.hit_radius = 0.0
        self.egg_size = 0
        self.swatter_size = 0
        self.sw_scale = 1.0

    # ---- 尺寸 ----
    def configure(self, scale: float, hit_radius: float,
                  sw_scale: Optional[float] = None) -> bool:
        """按设置重建尺寸; 返回是否发生了变化 (变了就要重建窗口)。"""
        sw_scale = float(scale if sw_scale is None else sw_scale)
        new_size = flyart.spawn_size(scale, hit_radius)
        egg_size = int(math.ceil(26.0 * scale)) + 1
        sw_size = flyart.swatter_size(sw_scale)
        if (new_size == self.size and abs(scale - self.scale) < 1e-6
                and abs(hit_radius - self.hit_radius) < 1e-6
                and abs(sw_scale - self.sw_scale) < 1e-6):
            return False
        self.size = new_size
        self.scale = scale
        self.hit_radius = hit_radius
        self.egg_size = egg_size
        self.swatter_size = sw_size
        self.sw_scale = sw_scale
        self._frames.clear()
        self._key = (flyart.ART_VERSION, new_size, round(scale, 3),
                     round(hit_radius, 1), round(sw_scale, 3))
        self._warm_todo = self._frame_plan()
        self._dirty = True
        if not self._load_cache():
            pass
        return True

    @staticmethod
    def _frame_plan() -> List[Tuple[str, int, int]]:
        """要预热的帧 (顺序 = 最可能先用到的先来)。"""
        plan: List[Tuple[str, int, int]] = []
        for pose in ("crawl_a", "crawl_b"):
            for a in range(flyart.ANGLES):
                plan.append((pose, a, 0))
        for a in range(flyart.ANGLES):
            for ph in range(flyart.WING_PHASES):
                plan.append(("fly", a, ph))
        return plan

    # ---- 取帧 ----
    def fly(self, pose: str, angle_idx: int, wing_phase: int) -> bytes:
        key = (pose, int(angle_idx) % flyart.ANGLES,
               int(wing_phase) % flyart.WING_PHASES if pose == "fly" else 0)
        data = self._frames.get(key)
        if data is None:
            data = flyart.render_fly(key[1], key[0], key[2], self.size,
                                     self.scale, self.hit_radius)
            self._frames[key] = data
            self._dirty = True
        return data

    def egg(self) -> bytes:
        key = ("egg", 0, 0)
        data = self._frames.get(key)
        if data is None:
            data = flyart.render_egg(self.egg_size, self.scale)
            self._frames[key] = data
            self._dirty = True
        return data

    def swatter(self, swing: int = 0) -> bytes:
        key = ("swatter", int(swing), 0)
        data = self._frames.get(key)
        if data is None:
            data = flyart.render_swatter(self.swatter_size, self.sw_scale,
                                         swing / 2.0)
            self._frames[key] = data
            self._dirty = True
        return data

    # ---- 预热 (在 tick 里用时间预算慢慢做) ----
    def warm(self, budget: float = 0.004) -> bool:
        """在 budget 秒内尽量多烘几帧; 全部烘完返回 True。"""
        if not self._warm_todo:
            return True
        end = time.perf_counter() + budget
        while self._warm_todo and time.perf_counter() < end:
            pose, a, ph = self._warm_todo.pop(0)
            if (pose, a, ph) not in self._frames:
                self.fly(pose, a, ph)
                self._since_save += 1
                if self._since_save >= 24:        # 分批落盘, 免得第一次运行白烘
                    self._since_save = 0
                    self.save_cache()
        if not self._warm_todo:
            self.save_cache()
            return True
        return False

    @property
    def warm_ratio(self) -> float:
        total = len(self._frame_plan())
        return 1.0 - len(self._warm_todo) / max(1, total)

    # ---- 磁盘缓存 ----
    def save_cache(self) -> None:
        """把**已经烘好的**帧写盘 (支持部分缓存: 预热到一半也能存下来)。"""
        if not self._dirty or not self._key or not self.cache_path:
            return
        have = [k for k in self._frame_plan() if k in self._frames]
        if not have:
            return
        try:
            head = {"version": ART_VERSION, "key": list(self._key),
                    "egg_size": self.egg_size, "swatter_size": self.swatter_size,
                    "frames": [list(k) for k in have],
                    "egg": ("egg", 0, 0) in self._frames,
                    "swatter": ("swatter", 0, 0) in self._frames}
            with open(self.cache_path, "wb") as f:
                f.write((json.dumps(head) + "\n").encode("utf-8"))
                for k in have:
                    f.write(self._frames[k])
                if head["egg"]:
                    f.write(self._frames[("egg", 0, 0)])
                if head["swatter"]:
                    f.write(self._frames[("swatter", 0, 0)])
            self._dirty = False
        except OSError:
            pass

    def _load_cache(self) -> bool:
        if not self._key or not self.cache_path or not os.path.exists(self.cache_path):
            return False
        try:
            with open(self.cache_path, "rb") as f:
                head_line = f.readline()
                head = json.loads(head_line.decode("utf-8"))
                if tuple(head.get("key", [])) != tuple(self._key):
                    return False
                n = self.size * self.size * 4
                loaded = []
                for k in head.get("frames", []):
                    data = f.read(n)
                    if len(data) != n:
                        self._frames.clear()
                        return False
                    key = (str(k[0]), int(k[1]), int(k[2]))
                    self._frames[key] = data
                    loaded.append(key)
                if head.get("egg"):
                    egg_n = head.get("egg_size", self.egg_size) ** 2 * 4
                    egg = f.read(egg_n)
                    if len(egg) == egg_n:
                        self._frames[("egg", 0, 0)] = egg
                if head.get("swatter"):
                    sw_n = head.get("swatter_size", 0) ** 2 * 4
                    sw = f.read(sw_n)
                    if len(sw) == sw_n:
                        self._frames[("swatter", 0, 0)] = sw
            done = set(loaded)
            self._warm_todo = [k for k in self._frame_plan() if k not in done]
            self._dirty = False
            return True
        except (OSError, ValueError):
            self._frames.clear()
            return False


# ---------------------------------------------------------------- 实体
class Entity:
    """一只苍蝇或一个卵。坐标是屏幕物理像素, (x, y) 是中心。"""

    __slots__ = ("kind", "key", "name", "x", "y", "angle", "speed", "state",
                 "state_t", "alpha", "window", "target", "rest_gap", "fly_gap",
                 "gait_t", "pose", "wing_phase", "hatch_at", "rnd", "flee_delay",
                 "flee_hold", "turn", "dead", "spawn_at", "emerge_target",
                 "rest_left", "fly_left", "fade_time", "_last_frame")

    def __init__(self, kind: str, key: str, name: str, x: float, y: float,
                 rnd: random.Random):
        self.kind = kind
        self.key = key
        self.name = name
        self.x = float(x)
        self.y = float(y)
        self.angle = rnd.uniform(0.0, 360.0)
        self.speed = 0.0
        self.state = "emerge"
        self.state_t = 0.0
        self.alpha = 255.0
        self.window = None
        self.target = None
        self.rest_gap = rnd.uniform(REST_MIN_GAP, REST_MAX_GAP)
        self.fly_gap = rnd.uniform(FLY_MIN_GAP, FLY_MAX_GAP)
        self.gait_t = 0.0
        self.pose = "crawl_a"
        self.wing_phase = 0
        self.hatch_at = None
        self.rnd = rnd
        self.flee_delay = None
        self.flee_hold = 0.0
        self.turn = 0.0
        self.dead = False
        self.spawn_at = (float(x), float(y))
        self.emerge_target = None
        self.rest_left = 1.0
        self.fly_left = 0.6
        self.fade_time = DIE_TIME
        self._last_frame = None

    # ---- 表示 ----
    def frame_key(self):
        return (self.pose, int(self.angle // (360.0 / flyart.ANGLES)) % flyart.ANGLES,
                self.wing_phase)

    def contains(self, px: float, py: float, radius: float) -> bool:
        return (self.x - px) ** 2 + (self.y - py) ** 2 <= radius * radius


# ---------------------------------------------------------------- 苍蝇群
class FlySwarm:
    """桌面苍蝇的总调度: 实体集合 + 每 tick 的运动/动画 + 与任务对账。"""

    def __init__(self, root, prefs, px, data_dir: str,
                 due_provider: Callable[[], Sequence[Tuple[str, str]]],
                 on_open_app: Optional[Callable[[], None]] = None,
                 log: Optional[Callable[[str], None]] = None):
        self.root = root
        self.prefs = prefs
        self.px = px
        self.data_dir = data_dir
        self.due_provider = due_provider
        self.on_open_app = on_open_app
        self.log = log or (lambda _m: None)

        self.art = FlyArt(data_dir)
        self.entities: List[Entity] = []
        self.enabled = bool(prefs.get("enabled"))
        self.swatter = None                 # 由 Swatter 注入 (见 swatter.py)
        self.available = True
        self._job = None
        self._last = time.perf_counter()
        self._last_vis = 0.0
        self._last_resync = 0.0
        self._all_covered = False
        self._paused = False
        self._rnd = random.Random(20260101)
        self._swatter_on = bool(prefs.get("swatter"))
        self._battery = False
        self._battery_checked = 0.0
        self._win_fail = 0
        self._frames = 0
        self._fps = 0.0
        self._fps_at = time.perf_counter()
        self._geom = None
        self._state_path = os.path.join(data_dir, STATE_NAME)
        self._pending_save = False

        self.art.configure(prefs.get("fly_scale"), prefs.get("swatter_hit_radius"),
                           prefs.get("swatter_scale"))
        self._geom = self._screen_geom()
        self._load_state()
        prefs.subscribe(self._on_pref_change)

    # ------------------------------------------------ 几何
    def _screen_geom(self):
        """虚拟桌面范围 (多屏时为所有屏幕的并集)。"""
        try:
            x = self.root.winfo_vrootx()
            y = self.root.winfo_vrooty()
            w = self.root.winfo_vrootwidth()
            h = self.root.winfo_vrootheight()
        except Exception:
            x, y = 0, 0
            w, h = 1920, 1080
        return (x, y, x + w, y + h)

    def refresh_geom(self) -> None:
        self._geom = self._screen_geom()

    # ------------------------------------------------ 生命周期
    def start(self) -> None:
        self._ensure_swatter()
        self.sync()
        self._schedule(33)

    def _ensure_swatter(self) -> None:
        if self.swatter is None and self.available:
            try:
                self.swatter = swatter_mod.Swatter(self.art, self.prefs)
                self.swatter.set_enabled(self._swatter_on)
            except Exception as exc:                     # pragma: no cover
                self.swatter = None
                self.log("苍蝇拍初始化失败: %r" % (exc,))

    def shutdown(self) -> None:
        self._cancel()
        self._save_state()
        self.art.save_cache()
        if self.swatter is not None:
            self.swatter.destroy()
        for e in list(self.entities):
            self._destroy_window(e)
        self.entities = []

    def _cancel(self) -> None:
        if self._job is not None:
            try:
                self.root.after_cancel(self._job)
            except Exception:
                pass
            self._job = None

    def _schedule(self, ms: int) -> None:
        self._cancel()
        try:
            self._job = self.root.after(ms, self._tick)
        except Exception:
            self._job = None

    # ------------------------------------------------ 对账
    def sync(self) -> None:
        """把实体集合对齐到 min(今日待完成数, 上限)。"""
        if not self.enabled or not self.available:
            for e in list(self.entities):
                self._remove(e, animate=False)
            return
        try:
            due = list(self.due_provider())
        except Exception:
            due = []
        limit = int(self.prefs.get("max_entities"))
        wanted = due[:max(0, limit)]
        wanted_map = {k: n for k, n in wanted}

        # 1) 消失: 任务完成 / 被挤出上限 (苍蝇和卵一样处理)
        have = {}
        for e in list(self.entities):
            have.setdefault(e.key, e)
        for key, ent in list(have.items()):
            if key not in wanted_map:
                self._remove(ent, animate=True, fade=TASK_GONE_FADE)
        # 2) 补齐: 缺少的键 -> 新苍蝇 (从任务栏钻出来)
        alive_keys = {e.key for e in self.entities if not e.dead and e.state != "gone"}
        for key, name in wanted:
            if key in alive_keys:
                continue
            if any(e.key == key and e.state == "gone" for e in self.entities):
                continue
            self._spawn(key, name)
        # 3) 名字同步 (改名后详情里能看到)
        for e in self.entities:
            if e.key in wanted_map:
                e.name = wanted_map[e.key]

    def _spawn(self, key: str, name: str) -> Entity:
        near_tray = self.prefs.get("spawn_mode") == "tray"
        if self.prefs.get("spawn_mode") == "mixed":
            near_tray = self._rnd.random() < 0.5
        x, y = self._taskbar_anchor(near_tray)
        ent = Entity("fly", key, name, x, y, self._rnd)
        ent.spawn_at = (x, y)
        ent.emerge_target = None
        ent.speed = 0.0
        ent.state = "emerge"
        ent.angle = -90.0 + self._rnd.uniform(-25.0, 25.0)   # 朝上钻出来
        if not self._make_window(ent):
            ent.state = "crawl"                              # 建窗失败也别死循环
        self.entities.append(ent)
        return ent

    def _taskbar_anchor(self, near_tray: bool) -> Tuple[float, float]:
        """出没点: 任务栏内部的随机位置 (被任务栏盖住 = 天然"从里面钻出来")。"""
        gx0, gy0, gx1, gy1 = self._geom
        bar = _taskbar_rect()
        if not bar:
            return (self._rnd.uniform(gx0 + 40, gx1 - 40), gy1 - 20)
        left, top, right, bottom = bar
        if right - left < 80:                                # 竖着的任务栏
            if near_tray:
                return (right - 18, top + 30)
            return (right - 18, self._rnd.uniform(top + 20, bottom - 20))
        if near_tray:
            return (right - self._rnd.uniform(60, 160), (top + bottom) / 2.0)
        return (self._rnd.uniform(left + 40, right - 40), (top + bottom) / 2.0)

    def _emerge_target(self, ent: Entity) -> Tuple[float, float]:
        """钻出任务栏后的落脚点。"""
        gx0, gy0, gx1, gy1 = self._geom
        bar = _taskbar_rect()
        top = bar[1] if bar else gy1 - 48
        tx = max(gx0 + 30.0, min(gx1 - 30.0, ent.x + self._rnd.uniform(-120, 120)))
        ty = max(gy0 + 40.0, min(gy1 - 40.0, top - self._rnd.uniform(30, 120)))
        return (tx, ty)

    # ------------------------------------------------ 窗口
    def _make_window(self, ent: Entity) -> bool:
        if not self.available:
            return False
        try:
            topmost = self.prefs.get("layer") == "topmost"
            if ent.kind == "egg":
                size = self.art.egg_size
                win = flywin.LayeredWindow(size, size, clickable=False,
                                           topmost=topmost,
                                           exclude_capture=self.prefs.get("hide_from_capture"))
                win.set_frame(self.art.egg())
                win.set_alpha(0)
                if not self.prefs.get("show_eggs"):
                    win.hide()
            else:
                size = self.art.size
                win = flywin.LayeredWindow(
                    size, size, clickable=self._swatter_on, topmost=topmost,
                    exclude_capture=self.prefs.get("hide_from_capture"),
                    on_click=(lambda e=ent: self.on_fly_clicked(e)))
                win.set_frame(self.art.fly(ent.pose, 0, 0))
            ent.window = win
            self._win_fail = 0
            self._place(ent)
            return True
        except Exception as exc:                             # pragma: no cover
            self._win_fail += 1
            self.log("苍蝇窗口创建失败 (%d/3): %r" % (self._win_fail, exc))
            if self._win_fail >= 3:                          # 连续失败才放弃整个功能
                self.available = False
                self.log("连续 3 次创建失败, 已自动关闭桌面苍蝇")
            return False

    def _destroy_window(self, ent: Entity) -> None:
        if ent.window is not None:
            try:
                ent.window.destroy()
            except Exception:
                pass
            ent.window = None

    def _place(self, ent: Entity) -> None:
        if ent.window is None:
            return
        ent.window.move(int(round(ent.x - ent.window.w / 2.0)),
                        int(round(ent.y - ent.window.h / 2.0)))

    def _remove(self, ent: Entity, animate: bool = True, fade: float = 0.3) -> None:
        """实体退场 (任务完成 / 被挤出上限 / 清场)。"""
        if ent.dead:
            return
        if not animate or ent.window is None:
            ent.dead = True
            self._destroy_window(ent)
            if ent in self.entities:
                self.entities.remove(ent)
            return
        ent.state = "gone"
        ent.state_t = 0.0
        ent.dead = True
        ent.fade_time = max(0.05, fade)

    # ------------------------------------------------ 设置变化
    def _on_pref_change(self, key: str) -> None:
        if key in ("speed_crawl", "speed_fly", "flee_factor", "rest_seconds",
                   "flee_radius", "wing_hz", "swatter_hit_radius", "fly_scale",
                   "max_entities", "swatter", "show_eggs", "enabled",
                   "swatter_swing", "swatter_scale", "swatter_desktop_only",
                   "layer", "hide_from_capture", "spawn_mode", "*"):
            self.apply_prefs(key)

    def apply_prefs(self, key: Optional[str] = None) -> None:
        """设置改动后立即生效。

        拖速度滑块时这个方法会被高频调用, 所以按 key 分级: 只有"结构性"的改动
        (开关/上限/出场方式) 才去重新对账任务。
        """
        self.enabled = bool(self.prefs.get("enabled"))
        changed = self.art.configure(self.prefs.get("fly_scale"),
                                     self.prefs.get("swatter_hit_radius"),
                                     self.prefs.get("swatter_scale"))
        if changed:
            for e in list(self.entities):
                self._destroy_window(e)
                self._make_window(e)
        topmost = self.prefs.get("layer") == "topmost"
        for e in self.entities:
            if e.window is not None:
                e.window.set_topmost(topmost)
        if key in (None, "*", "hide_from_capture"):
            self._apply_capture()
        if key in (None, "*", "show_eggs"):
            self._apply_egg_visibility()
        self.set_swatter(bool(self.prefs.get("swatter")))
        if key in (None, "*", "enabled", "max_entities", "show_eggs", "spawn_mode"):
            self.sync()

    def _apply_capture(self) -> None:
        on = bool(self.prefs.get("hide_from_capture"))
        for e in self.entities:
            if e.window is not None:
                e.window.set_capture_excluded(on)

    def _apply_egg_visibility(self) -> None:
        """「显示卵」关掉时, 卵照样在倒计时, 只是不画出来。"""
        show = bool(self.prefs.get("show_eggs"))
        for e in self.entities:
            if e.kind != "egg" or e.window is None:
                continue
            try:
                e.window.show() if show else e.window.hide()
            except Exception:
                pass

    # ------------------------------------------------ 苍蝇拍
    def set_swatter(self, on: bool) -> None:
        self._swatter_on = bool(on)
        for e in self.entities:
            if e.window is not None:
                e.window.set_clickable(self._swatter_on and e.kind == "fly")
        if self.swatter is not None:
            self.swatter.set_enabled(self._swatter_on)

    def on_fly_clicked(self, ent: Entity) -> None:
        """拍到一只苍蝇: 命中半径内最近的一只死, 而不是机械地只死被点的这只。"""
        if not self._swatter_on or ent.dead or ent.kind != "fly":
            return
        radius = float(self.prefs.get("swatter_hit_radius"))
        best = None
        best_d = radius * radius
        for e in self.entities:
            if e.kind != "fly" or e.dead or e.state in ("dying", "gone"):
                continue
            d = (e.x - ent.x) ** 2 + (e.y - ent.y) ** 2
            if d <= best_d:
                best, best_d = e, d
        self.swat(best or ent)

    def swat(self, ent: Entity) -> None:
        """拍死一只苍蝇 -> 淡出 -> 原地留卵 (任务完成的话就不留)。"""
        if ent is None or ent.dead or ent.kind != "fly":
            return
        if ent.state in ("dying", "gone"):
            return
        ent.state = "dying"
        ent.state_t = 0.0
        ent.speed = 0.0
        if self.swatter is not None:
            self.swatter.swing()

    def kill_all_to_eggs(self) -> int:
        """「杀死全部苍蝇」: 全部淡出 -> 原地留卵 (数量不变)。"""
        n = 0
        for e in self.entities:
            if e.kind == "fly" and not e.dead and e.state not in ("dying", "gone"):
                self.swat(e)
                n += 1
        return n

    def revive_all(self) -> int:
        """「全部复活」: 所有卵立即破壳, 在卵的位置钻出来。"""
        eggs = [e for e in self.entities if e.kind == "egg" and not e.dead]
        for e in eggs:
            e.hatch_at = 0.0
            e.state = "hatch"
            e.state_t = 0.0
        return len(eggs)

    def respawn_all(self) -> int:
        """「重新出场」: 清掉全部苍蝇和卵, 让它们重新从任务栏爬出来。"""
        n = len(self.entities)
        for e in list(self.entities):
            self._remove(e, animate=False)
        self.sync()
        return n

    def clear_eggs(self) -> int:
        """「清空所有卵」: 只移除卵, 对应的任务会重新长出苍蝇。"""
        n = 0
        for e in list(self.entities):
            if e.kind == "egg" and not e.dead:
                self._remove(e, animate=False)
                n += 1
        self.sync()
        return n

    # ------------------------------------------------ 持久化 (卵)
    def _load_state(self) -> None:
        try:
            with open(self._state_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, ValueError):
            return
        for item in (raw.get("eggs") or []):
            try:
                key = str(item["key"])
                x, y = float(item["x"]), float(item["y"])
                hatch_at = float(item["hatch_at"])
            except (KeyError, TypeError, ValueError):
                continue
            ent = Entity("egg", key, str(item.get("name", "")), x, y, self._rnd)
            ent.hatch_at = hatch_at
            ent.state = "egg"
            ent.alpha = 255.0
            if not self._make_window(ent):
                continue
            if ent.window is not None:
                ent.window.set_alpha(255)
            self.entities.append(ent)
        self._pending_save = False

    def _save_state(self) -> None:
        eggs = []
        for e in self.entities:
            if e.kind == "egg" and not e.dead and e.hatch_at:
                eggs.append({"key": e.key, "name": e.name, "x": round(e.x, 1),
                             "y": round(e.y, 1), "hatch_at": e.hatch_at})
        try:
            tmp = self._state_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"version": 1, "eggs": eggs}, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self._state_path)
        except OSError:
            pass

    # ------------------------------------------------ 主循环
    def _tick(self) -> None:
        self._job = None
        now = time.perf_counter()
        dt = now - self._last
        self._last = now
        if dt > 0.25:                       # 睡醒 / 卡顿后不要把苍蝇瞬移
            dt = 0.25
        self._frames += 1

        try:
            self._do_state(now, dt)
        except Exception as exc:            # pragma: no cover
            self.log("苍蝇 tick 出错: %r" % (exc,))

        self._schedule(self._interval_ms())

    def _do_state(self, now: float, dt: float) -> None:
        # 省电: 被盖住 / 长时间无操作 -> 不跑动画 (仍然低频自检)
        self._paused = False
        if self.prefs.get("pause_when_idle") and _idle_seconds() > 90.0:
            self._paused = True
        if not self._paused and self.prefs.get("pause_when_covered") and self.entities:
            if self._all_covered_check(now):
                self._paused = True
        if not self._paused and now - self._battery_checked > 20.0:
            self._battery_checked = now
            self._battery = _on_battery()

        if self.enabled:
            # 预热帧 (最多 4ms), 保证界面不卡
            self.art.warm(0.004)
            flywin.pump_all()
        if self.enabled and not self._paused:
            try:
                mx, my = self.root.winfo_pointerxy()
            except Exception:
                mx, my = -10000, -10000
            for e in list(self.entities):
                self._step_entity(e, dt, mx, my, now)

        if self.swatter is not None:
            try:
                mx, my = self.root.winfo_pointerxy()
            except Exception:
                mx, my = None, None
            self.swatter.tick(mx, my, now, dt if not self._paused else 0.0)

        # 低频兜底
        if now - self._last_resync > RESYNC_PERIOD:
            self._last_resync = now
            self.sync()
        if self._pending_save:
            self._pending_save = False
            self._save_state()

        # FPS 统计
        if now - self._fps_at >= 1.0:
            self._fps = self._frames / (now - self._fps_at)
            self._frames = 0
            self._fps_at = now

    def _all_covered_check(self, now: float) -> bool:
        """所有苍蝇都被前台窗口盖住? (每 0.5s 才算一次)"""
        if now - self._last_vis < VISIBILITY_PERIOD:
            return self._all_covered
        self._last_vis = now
        rect, cls = _foreground_rect()
        if rect is None or cls in ("Progman", "WorkerW", "Shell_TrayWnd"):
            self._all_covered = False
            return False
        left, top, right, bottom = rect
        for e in self.entities:
            if e.window is None:
                continue
            if not (left <= e.x < right and top <= e.y < bottom):
                self._all_covered = False
                return False
        self._all_covered = bool(self.entities)
        return self._all_covered

    # ------------------------------------------------ 单只苍蝇
    def _step_entity(self, ent: Entity, dt: float, mx: float, my: float,
                     now: float) -> None:
        ent.state_t += dt
        st = ent.state

        if st == "gone":
            ent.alpha -= 255.0 * dt / max(0.05, ent.fade_time)
            if ent.window is not None:
                ent.window.set_alpha(max(0.0, ent.alpha))
            if ent.alpha <= 0:
                self._final_remove(ent)
            return

        if st == "egg":
            ent.gait_t += dt
            if ent.alpha < 255.0:                     # 卵淡入
                ent.alpha = min(255.0, ent.alpha + 255.0 * dt / EGG_IN_TIME)
                if ent.window is not None:
                    ent.window.set_alpha(ent.alpha)
            if ent.hatch_at and time.time() >= ent.hatch_at:
                ent.state = "hatch"
                ent.state_t = 0.0
            return

        if st == "hatch":
            if ent.state_t >= HATCH_TIME:
                self._become_fly(ent)
            else:
                if ent.window is not None:
                    ent.window.set_alpha(255.0)
            return

        if st == "emerge":
            t = min(1.0, ent.state_t / EMERGE_TIME)
            tx, ty = ent.emerge_target or self._emerge_target(ent)
            ent.emerge_target = (tx, ty)
            ease = t * t * (3 - 2 * t)
            sx, sy = ent.spawn_at
            ent.x = sx + (tx - sx) * ease
            ent.y = sy + (ty - sy) * ease
            ent.pose = "crawl_b" if int(ent.state_t / GAIT_PERIOD) % 2 else "crawl_a"
            ent.wing_phase = 0
            ent.angle = math.degrees(math.atan2(ent.x - sx, -(ent.y - sy))) % 360.0
            self._place(ent)
            self._push_frame(ent)
            if t >= 1.0:
                ent.state = "crawl"
                ent.state_t = 0.0
                ent.target = None
                ent.speed = float(self.prefs.get("speed_crawl"))
            return

        if st == "dying":
            t = ent.state_t / DIE_TIME
            if ent.window is not None:
                ent.window.set_alpha(max(0.0, 255.0 * (1.0 - t)))
            if t >= 1.0:
                self._become_egg(ent)
            return

        # ---- 活着的苍蝇 ----
        crawl = float(self.prefs.get("speed_crawl"))
        fly = float(self.prefs.get("speed_fly"))
        flee_radius = float(self.prefs.get("flee_radius"))
        wing_hz = float(self.prefs.get("wing_hz"))

        # 鼠标避让 (带反应延迟: 手快才拍得到)
        dist = math.hypot(mx - ent.x, my - ent.y) if mx is not None else 1e9
        if dist <= flee_radius:
            if ent.flee_delay is None:
                ent.flee_delay = self._rnd.uniform(*FLEE_DELAY)
            else:
                ent.flee_delay -= dt
                if ent.flee_delay <= 0:
                    ent.flee_hold = FLEE_KEEP
                    if st in ("crawl", "rest"):
                        ent.state = "flee"
                        ent.state_t = 0.0
                    ent.target = None
        else:
            ent.flee_delay = None
            if ent.flee_hold > 0:
                ent.flee_hold -= dt

        st = ent.state if ent.state in ("crawl", "rest", "fly", "flee") else "crawl"

        if st == "rest":
            ent.speed = 0.0
            ent.pose = "crawl_a" if (int(ent.gait_t / GAIT_PERIOD) % 2 == 0) else "crawl_b"
            ent.gait_t += dt
            if ent.state_t >= (ent.rest_left or 1.0):
                ent.state = "crawl"
                ent.state_t = 0.0
                ent.rest_gap = self._rnd.uniform(REST_MIN_GAP, REST_MAX_GAP)
            self._place(ent)
            self._push_frame(ent)
            return

        if st == "flee":
            ent.speed = max(crawl, fly * 0.55) * float(self.prefs.get("flee_factor"))
            ent.pose = "fly"
            ent.turn = 540.0
            away = math.degrees(math.atan2(ent.x - mx, -(ent.y - my)))
            self._steer(ent, away, dt, quick=True)
            ent.wing_phase = int(ent.gait_t * wing_hz * flyart.WING_PHASES) \
                % flyart.WING_PHASES
            ent.gait_t += dt
            if ent.flee_hold <= 0 and dist > flee_radius * 1.25:
                ent.state = "crawl"
                ent.state_t = 0.0
                ent.speed = crawl
            self._advance(ent, dt)
            return

        if st == "fly":
            ent.speed = fly
            ent.pose = "fly"
            ent.turn = 260.0
            ent.wing_phase = int(ent.gait_t * wing_hz * flyart.WING_PHASES) \
                % flyart.WING_PHASES
            ent.gait_t += dt
            if ent.state_t >= ent.fly_left:
                ent.state = "crawl"
                ent.state_t = 0.0
                ent.speed = crawl
                ent.pose = "crawl_a"
                ent.wing_phase = 0
            self._advance(ent, dt)
            return

        # ---- crawl ----
        ent.speed = crawl
        ent.turn = 170.0
        if ent.target is None:
            ent.target = self._new_target(ent)
        tx, ty = ent.target
        if (ent.x - tx) ** 2 + (ent.y - ty) ** 2 < 900:
            ent.target = self._new_target(ent)
            tx, ty = ent.target
        desired = math.degrees(math.atan2(tx - ent.x, -(ty - ent.y)))
        self._steer(ent, desired, dt)
        ent.gait_t += dt
        ent.pose = "crawl_a" if int(ent.gait_t / GAIT_PERIOD) % 2 == 0 else "crawl_b"
        ent.wing_phase = 0
        # 爬一会儿就停一下 / 忽然飞一下 (苍蝇就是这么讨厌)
        if ent.state_t >= ent.rest_gap:
            gap = self._rnd.uniform(REST_MIN_GAP, REST_MAX_GAP)
            if self._rnd.random() < 0.38 and float(self.prefs.get("rest_seconds")) > 0:
                ent.state = "rest"
                ent.state_t = 0.0
                ent.rest_left = float(self.prefs.get("rest_seconds")) \
                    * self._rnd.uniform(0.6, 1.4)
                ent.speed = 0.0
                self._place(ent)
                self._push_frame(ent)
                return
            ent.rest_gap = gap
            ent.state_t = 0.0
        if ent.state_t >= ent.fly_gap * 0.4 + 1.0 and self._rnd.random() < 0.02:
            ent.state = "fly"
            ent.state_t = 0.0
            ent.fly_left = self._rnd.uniform(*FLY_TIME)
        self._advance(ent, dt)

    def _new_target(self, ent: Entity) -> Tuple[float, float]:
        gx0, gy0, gx1, gy1 = self._geom
        # 偏好在"当前这一片区域"晃 (苍蝇不会横穿整个屏幕)
        cx = min(max(ent.x, gx0 + 60), gx1 - 60)
        cy = min(max(ent.y, gy0 + 60), gy1 - 60)
        return (min(max(cx + self._rnd.uniform(-260, 260), gx0 + 24), gx1 - 24),
                min(max(cy + self._rnd.uniform(-190, 190), gy0 + 24), gy1 - 24))

    def _steer(self, ent: Entity, desired: float, dt: float,
               quick: bool = False) -> None:
        diff = (desired - ent.angle + 180.0) % 360.0 - 180.0
        max_turn = (ent.turn or 170.0) * dt * (1.8 if quick else 1.0)
        ent.angle = (ent.angle + max(-max_turn, min(max_turn, diff))) % 360.0
        # 走位抖一抖, 不然像在轨道上滑
        ent.angle = (ent.angle + self._rnd.uniform(-6.0, 6.0)
                     * (0.5 if quick else 1.0) * dt * 10.0) % 360.0

    def _advance(self, ent: Entity, dt: float) -> None:
        rad = math.radians(ent.angle)
        ent.x += math.sin(rad) * ent.speed * dt
        ent.y += -math.cos(rad) * ent.speed * dt
        gx0, gy0, gx1, gy1 = self._geom
        m = 16.0
        if ent.x < gx0 + m:
            ent.x = gx0 + m
            ent.angle = (180.0 - ent.angle) % 360.0 + self._rnd.uniform(-12, 12)
            ent.target = None
        elif ent.x > gx1 - m:
            ent.x = gx1 - m
            ent.angle = (180.0 - ent.angle) % 360.0 + self._rnd.uniform(-12, 12)
            ent.target = None
        if ent.y < gy0 + m:
            ent.y = gy0 + m
            ent.angle = (360.0 - ent.angle) % 360.0 + self._rnd.uniform(-12, 12)
            ent.target = None
        elif ent.y > gy1 - m:
            ent.y = gy1 - m
            ent.angle = (360.0 - ent.angle) % 360.0 + self._rnd.uniform(-12, 12)
            ent.target = None
        self._place(ent)
        self._push_frame(ent)

    def _push_frame(self, ent: Entity) -> None:
        if ent.window is None:
            return
        key = ent.frame_key()
        if key == getattr(ent, "_last_frame", None):
            return
        ent._last_frame = key
        try:
            ent.window.set_frame(self.art.fly(key[0], key[1], key[2]))
            if ent.alpha < 255.0:
                ent.window.set_alpha(ent.alpha)
        except Exception:
            pass

    # ------------------------------------------------ 状态切换
    def _become_egg(self, ent: Entity) -> None:
        """拍死/任务完成后: 变成卵 (任务还在的话)。"""
        self._destroy_window(ent)
        ent.kind = "egg"
        ent.state = "egg"
        ent.state_t = 0.0
        ent.alpha = 0.0
        ent.pose = "crawl_a"
        ent.wing_phase = 0
        ent._last_frame = None
        hatch = float(self.prefs.get("hatch_seconds"))
        ent.hatch_at = time.time() + max(0.0, hatch)
        if not self._make_window(ent):
            self.entities.remove(ent)
            return
        self._place(ent)
        self._pending_save = True
        if float(self.prefs.get("hatch_seconds")) <= 0:
            ent.state = "hatch"

    def _become_fly(self, ent: Entity) -> None:
        self._destroy_window(ent)
        ent.kind = "fly"
        ent.state = "crawl"
        ent.state_t = 0.0
        ent.alpha = 255.0
        ent.hatch_at = None
        ent.speed = float(self.prefs.get("speed_crawl"))
        ent.target = None
        ent._last_frame = None
        if not self._make_window(ent):
            self.entities.remove(ent)
            return
        self._place(ent)
        self._pending_save = True

    def _final_remove(self, ent: Entity) -> None:
        self._destroy_window(ent)
        if ent in self.entities:
            self.entities.remove(ent)

    # ------------------------------------------------ 对外统计
    def stats(self) -> Dict[str, float]:
        flies = sum(1 for e in self.entities if e.kind == "fly" and not e.dead)
        eggs = sum(1 for e in self.entities if e.kind == "egg" and not e.dead)
        return {"flies": flies, "eggs": eggs, "fps": round(self._fps, 1),
                "paused": 1 if self._paused else 0,
                "battery": 1 if self._battery else 0,
                "warm": round(self.art.warm_ratio * 100),
                "windows": flywin.count()}

    def _interval_ms(self) -> int:
        if self._paused:
            return 700
        if self._swatter_on:
            return 16
        if self._battery and self.prefs.get("battery_low_fps"):
            return 50
        return 33
