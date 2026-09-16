# readme_professional · 艾宾浩斯日程表源码剖析（进阶篇）

> 面向**想读懂/学习这份源码**的读者：本文把"这个 App 是怎么做出来的"讲透——
> 分层结构、每个文件干什么、算法与数值怎么算、提醒调度系统是怎么设计的、
> Windows 弹窗/托盘/后台/自启怎么做、进程之间怎么协作（本文没有任何 TCP 端口，详见 7.6），
> 以及开发中**遇到的问题和解决过程**。
>
> 配套入门文档（怎么用、关键数值概览）见 **[README.md](./README.md)**。

---

## 1. 30 秒看懂全景

- 语言/依赖：**纯 Python 3 标准库**（`tkinter` + `ctypes` + `subprocess`），Windows 专属，无第三方库。
- 运行：双击 `启动艾宾浩斯.vbs` → `pythonw app.py`（无黑窗）→ Tkinter 主窗口。
- 一句话逻辑：**一条"学习内容"= 一个 Series，自动带 4 次复习（Review）**；每次到期没打卡，下次启动自动顺延到今天（"永远不过期"）；到期/逾期用红色（复习）或绿色（学习计划）边框突出。
- 数据：单文件 JSON `appdata/data.json`，原子写入。
- 提醒：主程序自身负责——缩托盘常驻 + **按触发点调度**（不是固定轮询）在 0/7/9/13/17/20 点弹系统通知，睡眠恢复后用 `WM_POWERBROADCAST` 校准（第 6 章）。
- `reminder.py` 是可选轻量替代品：只发通知、无窗口。

```
用户双击 .vbs ──► pythonw app.py ──► App(root)  (Tkinter 主线程)
                         │
        ┌────────────────┼────────────────────────────────────┐
   models.py(纯逻辑)  notifier.py(系统弹窗)             tray.py(托盘+电源广播)
   Store→data.json    PowerShell→WinRT Toast      ctypes→Shell_NotifyIcon
   postpone/改期/计数   phrases/*.txt 文案              隐藏窗口+消息泵
                         │
                提醒调度(第 6 章): 算出下一个触发点 → root.after 一次睡到那时
```

---

## 2. 文件地图（每个文件干什么）

### 2.1 代码主体

| 文件 | 作用（一句话） |
| --- | --- |
| [`app.py`](./app.py) | 主程序：三页界面、卡片、详情弹窗、搜索窗口、托盘接线与**提醒调度** |
| [`models.py`](./models.py) | 核心模型与复习/后移/改期算法、JSON 存取（**不 import tkinter**） |
| [`notifier.py`](./notifier.py) | Windows 系统通知 + 按时段组装提示语 |
| [`tray.py`](./tray.py) | 纯 ctypes 调 Win32 实现的系统托盘图标 + 电源广播转发 |
| [`reminder.py`](./reminder.py) | 可选轻量进程：只定点发通知，无窗口（分段小睡，≤30s） |
| [`test_models.py`](./test_models.py) | 核心逻辑单元测试（14 项，无界面可跑） |
| [`app_smoke.py`](./app_smoke.py) | 界面冒烟：临时数据建界面并逐页截图 |

### 2.2 数据与文案

| 文件/目录 | 作用（一句话） |
| --- | --- |
| `appdata/data.json` | 数据存档：所有 Series 的 JSON 列表（`appdata/` 已在 `.gitignore` 中） |
| [`phrases/`](./phrases) | 提示语文案外置：6 个问候语时段文件 + 2 个任务提示文件，可改不改码 |

### 2.3 启动 / 运维脚本

| 文件 | 作用（一句话） |
| --- | --- |
| `启动艾宾浩斯.vbs` | 无黑窗启动主程序（推荐双击入口；依次回退 `pythonw`/`pyw`/`python`，都没有就弹 MsgBox） |
| `启动艾宾浩斯.bat` | 批处理启动入口（备选，纯 ASCII） |
| `run_app_bg.vbs` | 后台缩托盘启动（`pythonw app.py --minimized`，自启指向它） |
| `run_reminder.vbs` | 后台启动轻量提醒代理 `reminder.py` |
| `创建桌面快捷方式.vbs` | 在桌面生成「艾宾浩斯日程表」快捷方式（中文名用 `ChrW` 拼，绕开脚本编码问题） |
| `开启开机提醒.vbs` | 写入 Startup 快捷方式（`EbbinghausApp.lnk` → `run_app_bg.vbs`）并立即后台启动 |
| `关闭开机提醒.vbs` | 删除 Startup 快捷方式，并用 WMI 按命令行停掉 `app.py` / `reminder.py` 进程 |
| `测试通知.bat` | 立即发一条测试通知（排错用） |
| `诊断_出错看这里.bat` | 带控制台运行 `app.py`，保留报错窗口 |
| `README.md` | 使用篇：怎么用 + 关键数值 |
| `readme_professional.md` | 本文：源码剖析 |

---

## 3. 分层架构：为什么这样拆

```
┌──────────────────────────────────────────────────────┐
│  展示层   app.py（Tkinter）                           │  界面与交互
│  调度层   app.py 的 _next_trigger/_schedule_next_tick │  何时提醒（第 6 章）
│           /_on_tick/_on_system_resume                 │
│  系统能力 notifier.py（Toast） / tray.py（托盘+电源） │  系统封装
│  纯逻辑   models.py（无 GUI 依赖）                    │  算法 + 存储
│  数据     appdata/data.json                          │  单文件持久化
└──────────────────────────────────────────────────────┘
```

- `models.py` **刻意不 import tkinter**（模块 docstring 明说），所以能脱离 GUI 用 `test_models.py` 单测算法。
- `app.py` 只做两件事：①把模型数据画成卡片；②把用户操作翻译成模型方法调用（`complete_review` / `restore_review` / `edit_review_date` / `change_learn_date` / `complete_learning` / `undo_learning` / `set_review_mode` / `to_simple_plan`）。
- `notifier.py`、`tray.py` 都是"能力封装"：谁需要系统通知/托盘谁调用；`reminder.py` 只是复用 `notifier` 的轻量外壳。
- 调度层也放在 `app.py` 里（而不是单独 `scheduler.py`），因为它必须直接驱动 `root.after` 与 Tk 主线程状态（`self.tray` / `self.today` / `self.refresh_lists`），拆出去反而要来回穿透。

---

## 4. 数据模型与持久化（models.py）

### 4.1 字段

**Review（一次复习事件）**

| 字段 | 含义 |
| --- | --- |
| `n` | 第几次复习（1~4） |
| `date` | 计划复习日期 |
| `done` / `done_date` | 是否完成 / 实际完成日期 |

**Series（一条学习内容）**

| 字段 | 含义 |
| --- | --- |
| `id` / `name` / `created_at` | 唯一标识 / 名称 / 创建时间（同日排序用） |
| `learn_date` | 首次学习日期（复习日程）或应完成日期（学习计划） |
| `reviews` | 4 个 Review；学习计划在"完成学习"前为空 |
| `kind` | `"review"` 复习日程 / `"plan"` 学习计划（需复习）/ `"simple_plan"` 学习计划（不复习） |
| `done_learn` / `done_learn_date` | plan / simple_plan 是否已完成学习 / 完成日期 |
| `detail` | 任务详情（纯文本，可换行分段）：挂在**整条任务**上，三种类型共用一份。空详情存 `""`（不存字面"无"），界面用 `detail_text` 属性统一显示成"无"；`set_detail()` 会把纯空白归一化成空串并返回是否变化 |

```python
REVIEW_OFFSETS = [1, 4, 14, 28]
PLAN_KINDS = ("plan", "simple_plan")   # 判断"是不是学习计划"一律用这个元组
```

> 兼容性：`from_dict` 用 `d.get("kind", "review")`、`d.get("done_learn", False)`、
> `d.get("detail") or ""` 等兜底——**旧版 JSON 没有这些字段也能正常加载**；保存后才补写新字段。

### 4.2 持久化（JSON）

- 默认路径：`os.path.dirname(__file__) + "appdata/data.json"`，可用环境变量 `EBBINGHAUS_DATA_DIR` 覆盖（冒烟测试就靠它指向临时目录，不碰真实数据）。
- **原子写**：先写 `data.json.tmp` 再 `os.replace(tmp, path)`——写入中途断电/崩溃不会留下半个文件；`load()` 有 `try/except` 兜底，坏了也不至于闪退。
- `save()` 只在真正有变化时被调用（如 `run_daily_postpone` 返回 `changed` 才存），减少磁盘写入。

---

## 5. 算法详解（models.py）

### 5.1 复习日期生成

```python
REVIEW_OFFSETS = [1, 4, 14, 28]      # 首次学习后第 1/4/14/28 天复习
第 i 次复习日期 = learn_date + REVIEW_OFFSETS[i-1]
```

### 5.2 完成与"当前复习"

- `complete_current()`：完成**最早未完成**的那一次（`min(未完成, key=(date,n))`）。
- `complete_review(n)`：指定第 n 次。
- `is_completed`：全部 Review done（学习计划还要 `done_learn` 为真才算完成）。

### 5.3 还原（取消勾选）是级联的

`undo_review_cascade(n)`：还原第 n 次时，**n~4 一起还原**（前面的没完成，后面逻辑上不算完成）。
`restore_review(n, today)` 还原后按到期情况回学习表：

- 当日（`date == today`）：保持日期；
- 过去（`date < today`）：改到今天，其后未完成复习**整体平移**，保持间隔；
- 未来（`date > today`）：不动。

### 5.4 自动后移（"不怕漏"的灵魂）

```python
if 最早未完成复习.date < today:            # 这些天都没打卡
    delta = today - 最早未完成复习.date      # 迟到天数
    for r in 该系列所有未完成复习: r.date += delta   # 整体顺延
# 效果：最早未完成复习正好落到"今天"；已完成的永远不动
```

触发时机：**启动时**（`App.__init__`）、**跨天时**、**睡眠/休眠恢复后**各跑一次——
特意不在运行中反复跑，避免覆盖用户手动"改期"（见第 9 章 #8）。

### 5.5 复习行"改期"（edit_review_date）

把第 n 次改到新日期：该次直接改；其后**未完成**的按相同天数整体平移（已完成不动）→ 复习间隔保持。

### 5.6 任务"学习日期"改期（change_learn_date）

对**未完成复习**按 新日期 + 偏移量 重排：

- 若**最早的未完成复习"应到日期" ≤ 今天** → 按"今日复习计划"处理：它落到今天，其余未完成按 `OFFSETS[i]−OFFSETS[k]`（即 +3/+10/+14）顺延，保持间隔；
- 若应到日期 > 今天 → 作为日程计划：未完成复习直接采用 `新日期 + OFFSETS[i]`；
- **已完成的复习日期一律不动**。

（学习计划绿卡改"应完成日期"只改警示日期本身——它的复习以"完成学习当天"为基准生成，见 5.7。）

### 5.7 学习计划（绿卡）生命周期

```
创建(今天/未来日期) → kind="plan" / "simple_plan", reviews=[]   （1 张绿卡，应完成日期=所选日期）
   │  到期/逾期：绿框 + 红字"今日到期/拖欠N天"，计入"还剩N项"
   ▼ 勾选"完成学习"(complete_learning)
   plan        → reviews = 完成日 +1/+4/+14/+28 生成 4 次复习
   simple_plan → reviews 保持为空（不复习）
   ▼ 取消勾选(undo_learning)：清空复习，放回学习计划
```

说明：绿卡**只作计划提醒，不参与自动后移**（程序不改"应完成日期"，逾期就红字警示）。

### 5.8 "复习方式"切换（set_review_mode / to_simple_plan）

| 起点 → 目标 | 行为 |
| --- | --- |
| 未完成学习：`plan ⇄ simple_plan` | 只改 `kind`（此时本来没有复习） |
| 已完成学习：`plan → simple_plan` | 删除已生成的复习，保留"完成学习"记录 |
| 已完成学习：`simple_plan → plan` | 以 `done_learn_date` 为基准重新生成 4 次复习 |
| 任意 → `simple_plan`（`to_simple_plan`） | 删除全部复习事件（含已完成），名称/学习日期/"完成学习"标记保留 |

### 5.9 统计口径（顶部/托盘/系统通知/进度条共用）

- 今日待办 `due_task_count(today)` =
  `未完成复习且 date ≤ today` 数量 ＋ `未完成学习计划且 learn_date ≤ today` 数量。
- 进度条 `_today_progress`：`pct = 已完成 / (已完成 + 未完成)`；分母为 0 → 100%。
  - 已完成：今天勾掉的到期复习（`done_date == today 且 date <= today`）＋ 今天完成的到期学习计划；
  - 未完成：同上 `due_task_count`。
- 拖欠天数：绿卡上 `today − learn_date`（红字）。

### 5.10 学习表排序（`_home_entry_key`）

1. **今日/过期块**（`date ≤ today`）在最前：块内先复习卡、后学习计划卡，块内再按日期；
2. **未来块**：严格按日期，同一天内复习在前、学习计划在后；
3. 兜底比较 `created_at`。已"完成学习"的绿卡按复习事件参与排序（见第 9 章 #14）。

### 5.11 文案与提示语（notifier.py）

- `greeting()` 按 `hour` 分 6 个时段文件（0-7 / 7-9 / 9-13 / 13-17 / 17-20 / 20-24）；
- `task_phrase(n)`：`n > 0` 抽"有未完成"组并把 `*` 替换成数字，否则抽"全部完成"组；
- 文件缺失时用内置兜底文案（`_FALLBACK_*`），`#` 开头的行是注释；
- 主界面顶部提示语：**只在待办数变化时重随**（缓存 `_last_due_count`），避免每次刷新跳字。

---

## 6. 提醒调度系统（本版重构重点）

### 6.1 旧版是怎么做的、问题在哪

旧版只有一句：

```python
self.root.after(20000, self._tick)     # 每 20 秒轮询一次
...
def _tick(self):
    now = datetime.now()
    if now.date() != self.today: ...   # 跨天校正
    self._maybe_notify(now)            # 到点就发
    self.root.after(20000, self._tick) # 继续轮询
```

问题有三个：

1. **整天无意义唤醒**：一天 4320 次 tick，只为命中 6 个提醒点，绝大多数是空转；
2. **睡眠/休眠后补发一串过期通知**：电脑睡了一夜醒来，20 秒内就会检测到"7 点、9 点还没发过"，于是连着弹几条早已过期的提醒；
3. **没有"被唤醒"的概念**：系统恢复后只能等下一次 tick 才发现时间跳变，跨天后的自动后移也跟着延后。

### 6.2 设计目标

| 目标 | 落地手段 |
| --- | --- |
| 减少唤醒次数 | 用当前时间**算出下一个触发时刻**，`root.after` 一次睡到那一刻 |
| 时间必须"自我校准" | 每次重排都用 `datetime.now()` 重新锚定，误差不累积 |
| 睡眠恢复立刻纠正 | 监听 `WM_POWERBROADCAST`，恢复即重排（6.5） |
| 不打扰 | 迟到超过**30 秒**的提醒直接跳过，不补发 |
| 数据不错乱 | 跨天校正**独立于通知**，即使通知被跳过也照常执行 |
| 托盘不可用时也能兜底 | 仅当 `self.tray is None` 时给等待加**1 小时上限** |

### 6.3 常量与状态

```python
TRIGGER_HOURS = [0, 7, 9, 13, 17, 20]   # 提醒时刻 (0 即 24 点), 每天 6 次
_GRACE_SECONDS = 30                      # 触发点宽限窗口 (秒)
_MAX_WAIT_MS = 60 * 60 * 1000            # 无托盘兜底的最长等待 (毫秒)

self._next_at = None    # 本次 after 对应的触发时刻 (算 lateness 用)
self._tick_job = None   # 当前挂着的 Tk after 句柄
```

### 6.4 方法逐个说明

**`_next_trigger(now)` —— 找下一个触发点（严格晚于 now，最多往后找两天）**

```python
for offset in (0, 1, 2):
    day = now.date() + timedelta(days=offset)
    for h in sorted(TRIGGER_HOURS):
        cand = datetime(day.year, day.month, day.day, h)
        if cand > now:
            return cand
# 兜底: 保证一定返回 datetime
```

- 用 `sorted(TRIGGER_HOURS)` 保证取的是当天**最早**的候选（列表顺序变了也不会错）；
- 严格 `>` 而不是 `>=`：**恰好落在整点**（如 09:00:00.000）时排到下一个点（13:00），不会自己触发自己；
- 最多找两天是为了覆盖"今天最后一个点（20 点）已过"的情况——最近的触发点在**明天 0 点**。

**`_schedule_next_tick()` —— 重排（唯一排闹钟的地方）**

```python
if self._tick_job is not None:
    try: self.root.after_cancel(self._tick_job)
    except Exception: pass          # 已经跑过的 job 取消失败也无所谓
    self._tick_job = None
now = datetime.now()
self._next_at = self._next_trigger(now)
delay = int((self._next_at - now).total_seconds() * 1000)
delay = max(1000, delay)            # 至少 1 秒, 防止 while 式空转
if self.tray is None:               # 只有无托盘才加 1 小时上限
    delay = min(delay, _MAX_WAIT_MS)
self._tick_job = self.root.after(delay, self._on_tick)
```

> **顺序依赖**：`_setup_background()` 里必须先建托盘、再调 `_schedule_next_tick()`——
> 因为"要不要加 1 小时上限"取决于 `self.tray` 是不是 `None`。

**`_on_tick()` —— 时间到了**

```python
now = datetime.now()
if now.date() != self.today:                 # ① 跨天校正: 独立于通知, 一定执行
    self.today = now.date()
    self.store.run_daily_postpone(self.today)
    self.refresh_lists()
lateness = None
if self._next_at is not None:
    lateness = (now - self._next_at).total_seconds()
if lateness is not None and 0 <= lateness <= _GRACE_SECONDS:   # ② 宽限窗口内才发
    self._maybe_notify(now)
self._schedule_next_tick()                   # ③ 无论如何都用"现在"重新锚定
```

**`_on_system_resume()` —— 系统醒来了**

```python
now = datetime.now()
if now.date() != self.today:                 # 同样的跨天校正
    self.today = now.date()
    self.store.run_daily_postpone(self.today)
    self.refresh_lists()
self._schedule_next_tick()                   # 立刻按当前时间重排
```

（注意：恢复时**刻意不补发通知**——睡了 5 小时醒来立刻弹一条"该复习了"很突兀，交给下一个触发点即可。）

**`_maybe_notify(now)` —— 保持不变**

```python
key = (now.date(), now.hour)
if now.hour in TRIGGER_HOURS and self._last_notify_key != key:
    self._last_notify_key = key
    notifier.notify_tasks(now)      # (date, hour) 去重: 同一小时只发一次
```

### 6.5 唤醒校准：WM_POWERBROADCAST 链路

`tray.py` 里新增电源广播常量，并让托盘隐藏窗口把它们转发给上层：

```python
WM_POWERBROADCAST = 0x0218
PBT_APMRESUMEAUTOMATIC = 0x0012   # 自动唤醒 (如系统定时从睡眠恢复)
PBT_APMRESUMESUSPEND   = 0x0007   # 用户操作唤醒了挂起的系统
PBT_APMRESUMECRITICAL  = 0x0006   # 电量耗尽等临界状态后恢复

class TrayIcon:
    def __init__(self, tip, on_open, on_quit, on_resume=None):
        self._on_resume = on_resume        # 可为 None (不关心唤醒)

    def _on_message(self, hwnd, msg, wparam, lparam):
        ...
        if msg == WM_POWERBROADCAST:       # 睡眠 / 休眠恢复: 让上层重新校准定时
            if (wparam in (PBT_APMRESUMEAUTOMATIC, PBT_APMRESUMESUSPEND,
                           PBT_APMRESUMECRITICAL)
                    and self._on_resume is not None):
                self._safe(self._on_resume)
            return 0
```

完整链路（**全程在 Tk 主线程**，不碰线程安全问题）：

```
Windows 从睡眠/休眠恢复
   │  广播 WM_POWERBROADCAST (wParam = 0x0012 / 0x0007 / 0x0006)
   ▼
托盘隐藏窗口的消息队列          ← 消息先躺在队列里, 不阻塞任何人
   │  Tk 主线程每 200ms: _pump_tray() → tray.pump()
   │      PeekMessageW(hwnd 过滤) / TranslateMessage / DispatchMessageW
   ▼
TrayIcon._on_message 命中 WM_POWERBROADCAST
   │  self._safe(self._on_resume)   ← 回调异常静默, 绝不影响消息泵
   ▼
App._on_system_resume(): 跨天校正 + _schedule_next_tick()
```

`_safe()` 是托盘里统一的"回调不抛异常"包装（`on_open`/`on_quit` 也用它），
所以即使 `_on_system_resume` 内部出错，托盘与消息泵也不会崩。

### 6.6 一次完整的时间轴

```
有托盘 (正常使用)
 08:00 ────────────────► 09:00 ────────────────► 13:00 ────────► ...
   │  _schedule_next_tick(): delay = 4h? 不, 现在是 08:00, delay = 1h
   │  一次 after 睡到 09:00
   ▼
 09:00 _on_tick(): lateness ≈ 0 → _maybe_notify → 重排到 13:00 (delay = 4h)
 13:00 _on_tick(): lateness ≈ 0 → 通知 → 重排到 17:00
 ...                                  全天共 6 次唤醒 (0/7/9/13/17/20)

无托盘 (兜底)
 09:01 _schedule_next_tick(): 下一触发点 13:00 = 4h > 1h → delay 截成 1h
 10:01 _on_tick(): lateness = 10:01 − 13:00 < 0 → 不发通知, 重排 (再截 1h)
 11:01 / 12:01 同上
 12:01 之后: 距 13:00 只剩 59 分钟 ≤ 1h → 不再截断, 一次睡到 13:00
 13:00 _on_tick(): lateness ≈ 0 → 正常通知
```

> 关键点：无托盘的中间唤醒只做"校准"，**因为 lateness 为负所以不会误发通知**。

### 6.7 边界条件与不变量

| 情形 | 行为 | 为什么 |
| --- | --- | --- |
| `now` 正好 = 触发点 | 排到**下一个**触发点 | 严格 `>`，避免自己触发自己 |
| `now` = 今天 20:00:00 | 排到**明天 00:00** | 最多找两天的第 2 个候选 |
| 距触发点不足 1 秒 | `delay` 抬到 1000ms | 防 0/负延迟导致忙等 |
| 迟到 0 ~ 30 秒 | 正常发通知 | 宽限窗口（`_GRACE_SECONDS`） |
| 迟到 > 30 秒（睡眠/卡死/关机） | **跳过不补发**，重排下一次 | 过期通知是打扰 |
| `lateness < 0`（系统时间被往回调） | 不发通知，重排 | 只认 `0 <= lateness` |
| 跨天 + 本次通知被跳过 | `today` 更新 + 自动后移 + 刷新列表**照常执行** | 跨天校正独立于通知 |
| 有托盘，下一触发点在 4 小时后 | `delay = 4h`，**不加**上限 | 有 `WM_POWERBROADCAST` 兜着 |
| 无托盘，下一触发点在 4 小时后 | `delay = min(4h, 1h) = 1h` | 没人叫醒它，得定期自查 |
| `_on_tick` 时 `_next_at is None` | 不发通知，直接重排 | 兜底，不让调度断链 |
| 重复调用 `_schedule_next_tick` | 先 `after_cancel` 旧 job（失败静默） | 保证同一时刻只有一个待跑 job |

### 6.8 新旧对比

| 维度 | 旧版（20 秒轮询） | 新版（按触发点调度） |
| --- | --- | --- |
| 每天唤醒次数（有托盘） | 4320 次 | **6 次** |
| 每天唤醒次数（无托盘） | 4320 次 | ≤ 24 次（1 小时上限） |
| 睡眠恢复后的表现 | 补发一串过期通知 | 电源广播立刻重排；迟到的一次跳过 |
| 跨天校正时机 | 下个 tick（≤20 秒后） | 下次触发点 / 唤醒恢复时立即 |
| 新增 API | — | `_next_trigger` / `_schedule_next_tick` / `_on_tick` / `_on_system_resume` |
| 删除 API | `_tick` | — |
| 保持不变 | — | `_maybe_notify` 去重、`_pump_tray` 每 200ms 派发、托盘消息仍在主线程处理 |

### 6.9 这次重构的验收清单

开发期用一个临时校验脚本逐条跑过（**脚本不随仓库保留**，避免给仓库塞一次性文件），共 60 项断言全绿：

| 分组 | 检查内容 |
| --- | --- |
| `_next_trigger` | 10 组定点（含 06:59:59→07:00、07:00→09:00、20:00→次日 00:00、23:59:59→次日 00:00）+ 全天 72 个采样点：严格晚于 `now`、间隔 ≤ 2 天、必为整点 |
| 延迟计算 | 无托盘 09:01 → `3600000`（1 小时上限生效）；有托盘同刻 → `14340000`（不截断）；09:00 整 → `14400000`；不足 1 秒 → `1000`；重排前确实 `after_cancel` 旧 job |
| 宽限窗口 | 迟到 0s / 30s（边界）→ 发通知；31s、3.5h → 跳过；负 lateness → 不触发；同一小时只发一次且 `_last_notify_key` 正确 |
| 跨天 | 通知被跳过时 `today` / `run_daily_postpone` / `refresh_lists` 照常执行；准时触发时后移与通知都执行 |
| 唤醒恢复 | 跨天校正 + 重排 + 不补发通知；同一天不重复后移 |
| tray.py | 4 个常量取值；`on_resume` 默认 `None`；三个恢复 wParam 都触发且 `return 0`；挂起（0x0004）不触发；回调抛异常被静默 |
| 端到端 | 对**真实隐藏窗口** `PostMessageW(WM_POWERBROADCAST)` → `pump()` → `on_resume` 确实被调用；普通消息不误触发 |
| 接线与兜底 | `App` 创建托盘时确实传入 `on_resume=self._on_system_resume`；模拟托盘不可用 → `self.tray is None` 并走上限 |
| 回归 | `python -m py_compile app.py tray.py` 通过；`python app_smoke.py` 输出 `SMOKE_OK`，界面与改造前一致 |

---

## 7. Windows 集成：弹窗 / 托盘 / 后台 / 单实例 / 自启

### 7.1 系统通知怎么弹的（notifier.py）

不引第三方库，借道 **PowerShell 的 WinRT Toast API**：

1. Python 组好 PowerShell 脚本字符串（`ToastNotificationManager` → `ToastText02` 模板 → 写两行 text）；
2. **编码防坑**：`base64(脚本.encode("utf-16-le"))`，用 `powershell -NoProfile -EncodedCommand <base64>` 执行——彻底绕开命令行中文乱码/引号注入（脚本里的单引号还会翻倍转义）；
3. `creationflags=CREATE_NO_WINDOW`（不闪黑窗）、`timeout=20`、异常静默（通知失败不影响主程序）。

### 7.2 托盘常驻怎么做的（tray.py）

- 用 `ctypes` 声明 Win32 结构体（`NOTIFYICONDATAW`、`WNDCLASSW`、`POINT`）后调 `Shell_NotifyIconW(NIM_ADD/…)` 加图标；
- **单线程设计**：`RegisterClassW` 注册一个隐藏窗口接收托盘回调消息，但不在独立线程里跑消息循环，而是由 Tk 主线程用 `root.after(200, pump)` 周期性 `PeekMessageW/DispatchMessageW` 派发——回调都在主线程，能安全操作 Tk；
- 消息分支：`WM_APP_TRAY`（双击 `WM_LBUTTONDBLCLK` → 打开；右键 `WM_RBUTTONUP`/`WM_CONTEXTMENU` → 菜单）、`WM_TRAY_SHOW`（第二个实例请求唤起）、`WM_POWERBROADCAST`（睡眠恢复校准，见 6.5）；
- 双击托盘 → `on_open`（显示主窗）；右键 → `CreatePopupMenu` + `TrackPopupMenu`（打开主界面/退出）；
- `set_tip()` 更新悬停文字（`NIM_MODIFY`），`remove()` 删图标并销毁隐藏窗。

### 7.3 单实例 + "第二个进程把已有窗口唤出来"

- `app.py` 里 `CreateMutexW`（名 `EbbinghausScheduleAppSingleton`）：拿不到锁说明已有实例 → `_signal_existing_instance()`；
- 新进程用 `FindWindowW(None, tray.TRAY_WINDOW_TITLE)` 找到老进程的托盘隐藏窗，`PostMessageW(WM_TRAY_SHOW)` → 老进程把主界面 `deiconify` 显示，新进程直接退出。

### 7.4 关窗不退出、后台常驻

- `root.protocol("WM_DELETE_WINDOW", hide_window)`：点 X 时**有托盘** → `root.withdraw()` 缩后台；**无托盘** → 真正 `quit_app()`；
- `_pump_tray`：`root.after(200, ...)` 驱动托盘消息（**每 200ms，本次重构未改动**）；
- `--minimized`（等价 `--tray`）启动 = 开机自启模式：`App(..., notify_on_start=True)` 直接缩托盘，并 `after(1500)` 先发一条"今天任务"通知；
- 启动时若**恰在提醒整点**，先把 `_last_notify_key` 预记成 `(今天, 该小时)`，避免刚开机就重复推一条（与旧版一致）。

### 7.5 开机自启怎么做的（VBS 一族）

- `开启开机提醒.vbs`：向 **Startup 文件夹**写 `EbbinghausApp.lnk`，目标 = `run_app_bg.vbs`，并立刻运行它（顺便删掉旧版遗留的 `EbbinghausReminder.lnk`）；`run_app_bg.vbs` 用无窗解释器跑 `pythonw app.py --minimized`；
- `关闭开机提醒.vbs`：删快捷方式，并用 **WMI（`Win32_Process`）按 `CommandLine` 包含 `app.py`/`reminder.py`** 杀掉后台 python——不做中文比较，跨进程稳定。

### 7.6 "端口调用"？—— 本程序没有网络端口，用的是这几条"接口"

没有监听 TCP/UDP 端口，进程/系统间的"接口调用"是这些通道：

| 通道 | 干什么 |
| --- | --- |
| ctypes → user32 / shell32 / kernel32 | 托盘、隐藏窗、弹菜单、互斥、跨进程消息、接收电源广播 |
| `WM_POWERBROADCAST`（系统 → 托盘隐藏窗） | 睡眠/休眠恢复后通知程序校准定时（6.5） |
| `root.after`（Tk 内部事件队列） | 到点执行 `_on_tick`；每 200ms 驱动 `_pump_tray` |
| subprocess → PowerShell | 弹 WinRT 系统通知 |
| WScript/VBS（双击 .vbs） | 无黑窗启动、快捷方式、开机自启 |
| Startup 快捷方式 + `--minimized` 参数 | 开机进托盘 |
| CreateMutex / FindWindow / PostMessage | 单实例与"唤起旧窗口" |
| WMI `Win32_Process` | 关闭脚本按命令行杀后台进程 |
| 环境变量 `EBBINGHAUS_DATA_DIR` | 外部指定数据目录（测试/多数据） |

---

## 8. 界面实现要点（app.py）

- **三页切换**：三个 `Frame` 全部 `place(relwidth=1, relheight=1)` 叠在一起，`show_page` 用 `.lift()` 换层；底部导航高亮选中项。
- **可滚动卡片列表**：`tk.Canvas` + `ttk.Scrollbar` + 内部 Frame，`<Configure>` 更新 scrollregion，`<Enter>/<Leave>` 绑定/解绑滚轮。
- **两种卡片一张列表**：`_build_home_cards` 把"复习事件"和"学习计划"合成一个列表统一排序渲染（复习卡红/灰框、绿卡绿/灰框）；右侧 `Checkbutton` 即"打卡"。
- **详情弹窗（模态）**：`tk.Toplevel` + `grab_set()`；`DatePicker` 用 `Spinbox` 拼年/月/日并 `wait_window` 等结果；行内按钮"改期/修改名称/删除"直接操作内存 Series 后 `store.save()` + `refresh_lists()`。
  - 弹窗内**不自带整体滚动**，而是"分区域各自滚动"：顶部 `ScrollingTitle`（Canvas + 单行文本 + 横向 `ttk.Scrollbar`）只在标题溢出时显示滚动条，鼠标在标题上滚轮 = 左右滚；中间 `tk.Text` 详情框配 `AutoVScroll`，只在内容溢出时显示竖向滚动条，滚轮 = 上下滚，两者互不影响。
  - 详情**默认只读**（`state="disabled"`），点「编辑详情」才切到可编辑；空详情只读时显示"无"、进入编辑时自动清空。
  - **卡片标题一行不换行**：`ElidedLabel` 在 `<Configure>` 里按当前像素宽度用 `elide_text()` 二分裁剪并补中文省略号"…"，窗口缩放/滚动条出现导致的宽度变化都会自动重算。
  - **创建任务两段式**：录入页第一次回车先弹 `DetailEditor`（详情编辑窗），确认后才落库；取消/关窗/Esc 用 `askyesno` 问"是否保留刚刚创建的任务"。
  - 详情编辑的键盘规则集中在 `bind_detail_keys()`：Enter 提交、Shift+Enter 换行、Ctrl+S 暂存不关闭；提交前用 `ime_composing()`（IMM32 `ImmGetCompositionStringW` 查组合串）判断中文输入法是否在选字，是则忽略这次回车。
- **搜索窗口**：独立 `Toplevel`（关掉它不影响主程序），`_match_series` 做 `casefold()` 子串匹配并按任务去重；`_start_spinner`/`_rotate_spinner` 用 Canvas 画圆弧 + `after(80)` 改起始角度做旋转动画，`_search_seq` 让"窗口已关/又搜了一次"的旧回调自动失效；结果至少展示 250ms 再渲染。
- **进度条**：底部一条 `Frame`（灰底 = 未完成），内部 `Frame` 用 `place(relwidth=percent/100, relheight=1)` 当绿色填充，窗口缩放自动跟随；百分比文字同步。
- **中文字体 + 高分屏**：选"微软雅黑→黑体→宋体"，并把默认字体族设成中文字体；`SetProcessDpiAwareness(1)` + `tk scaling = dpi/72`，布局尺寸用 `px()` 按 `dpi/96` 放大。

---

## 9. 遇到的问题 & 解决过程（都是真实踩过的）

| # | 问题 | 解决方式 |
| --- | --- | --- |
| 1 | **中文输入法候选框**在部分 GUI 框架里不弹 | 从 Kivy 改用 **Tkinter**（原生 Windows 文本控件，拼音候选框正常） |
| 2 | 双击启动闪黑窗、控制台中文乱码 | 用 `.vbs` + `pythonw` 无窗启动；出错时用 `.bat` 保留控制台看 Traceback |
| 3 | **Toast 中文乱码 / 引号注入** | PowerShell `-EncodedCommand`：UTF-16LE + Base64 传参，单引号翻倍转义 |
| 4 | **ctypes 64 位句柄截断**导致崩溃 | 所有句柄函数显式声明 `restype`/`argtypes`（如 `c_void_p`/`HWND`） |
| 5 | **托盘回调被 GC** 后窗口收消息崩溃 | `WNDPROCTYPE(self._on_message)` 存成 `self._wndproc` 并保持引用 |
| 6 | 托盘消息循环与 Tk 主线程冲突 | 不另开线程：隐藏窗消息由 `root.after(200)` 的 `pump()` 在主线程派发；绝不在 Tk 线程外碰控件 |
| 7 | JSON 写到一半损坏 | 先写 `tmp` 再 `os.replace` 原子替换；读文件异常兜底为空列表不闪退 |
| 8 | 自动后移会**覆盖用户手动改期** | 只在启动/跨天/唤醒恢复时跑一次，运行中不再动 |
| 9 | 开机自启 + 手动双击开两份、发两条通知 | 单实例互斥锁；自启只挂主程序，`reminder.py` 仅保留作可选 |
| 10 | 同一整点重复通知 | 缓存 `(date, hour)` 去重，启动恰在整点时预记一次 |
| 11 | 顶部提示语每次刷新都跳 | 只有待办**数目变化**才重随机 |
| 12 | 高分屏字体发虚/布局缩错 | DPI Aware + `tk scaling` + `px()` 尺寸换算 |
| 13 | 提醒代理错过休眠后的整点 | `reminder.py` 分段小睡（≤30s）重算目标时刻；主程序改为 `WM_POWERBROADCAST` 唤醒校准 + 30 秒宽限（第 6 章） |
| 14 | **绿卡"完成学习"后复习卡不出现（用户实测发现的 bug）** | 根因：首页构建对 `kind=="plan"` 只画"未完成学习"的绿卡分支，完成学习后生成的 reviews 被跳过。修复：完成后按普通复习事件展开渲染，排序键改判"是否复习事件" |
| 15 | 全绿卡片无法突出"今天要完成" | 绿卡改为**只有到期/逾期才绿框**，未来未到期与普通卡一致灰框 |
| 16 | 学习计划逾期该不该顺延 | 设计为**不自动后移**：应完成日期灰字固定 + 红字"拖欠N天"，持续计入任务数，直到完成/删除（计划是承诺，不是流水线） |
| 17 | **每 20 秒轮询**：一天空转 4320 次；睡眠醒来还会补发一串过期通知 | 改成**按触发点调度**（第 6 章）：算出下一个触发点一次睡到；30 秒宽限；迟到跳过不补发；`_next_at` 每次用 `now` 重新锚定 |
| 18 | 托盘不可用时没人"叫醒"程序校准 | 只在 `self.tray is None` 时给 `delay` 加 **1 小时上限**；有托盘仍一次睡到触发点（`_MAX_WAIT_MS` 与宽限窗口是两回事） |
| 19 | 跨天校正一度和通知耦合：通知被跳过时后移也跟着丢 | `_on_tick` 把跨天校正放在**通知判断之前**且无条件执行；`_on_system_resume` 同样先校正再重排 |
| 20 | 托盘类新增唤醒回调怕影响已有三个回调 | `on_resume` 设为**可选参数**（默认 `None`），且统一走 `_safe()` 包装：回调异常静默，消息泵照常 |

> 每条算法都有对应单测（`test_models.py`）；调度部分见 6.9 的验收清单。

---

## 10. 测试与冒烟

```powershell
python test_models.py       # 16 项算法单测（复习生成/完成后移/还原/改期/统计/详情字段…）
python test_ui.py           # 界面行为测试（标题单行省略/详情窗横竖滚动/详情编辑/两段式创建）
python app_smoke.py         # 建临时数据 → 建界面 → 逐页截图（smoke_*.png），含超长标题与空/长详情
python reminder.py --once   # 只发一条通知，验证弹窗链路
python 测试通知.bat          # 图形化排错入口
```

- 单测与冒烟都通过 `EBBINGHAUS_DATA_DIR` 指向临时目录，**不会污染真实数据**；
- 单测不需要显示器/Tk，冒烟需要 Windows 桌面（会真的建窗口并截图，需要 Pillow 才能存图，没装就只验证"不报错"）。

---

## 11. "如果我想改……"（上手指引）

| 想改什么 | 去哪个文件改 |
| --- | --- |
| 提示语文案 | `phrases/*.txt`（加删行即可，`#` 是注释，`*` 会被替换成剩余数） |
| 通知时刻（6 个整点） | `app.py` 顶部 `TRIGGER_HOURS`；`reminder.py` 顶部同名常量 |
| 迟到多久就不补发 | `app.py` 的 `_GRACE_SECONDS`（默认 30 秒） |
| 无托盘时的最长等待 | `app.py` 的 `_MAX_WAIT_MS`（默认 1 小时） |
| 唤醒后要不要补发通知 | `app.py` 的 `_on_system_resume`（目前只校正 + 重排，不补发） |
| 复习间隔（+1/+4/+14/+28） | `models.py` 的 `REVIEW_OFFSETS`（注意：只对新生成/重算生效） |
| 主题色/边框色 | `app.py` 顶部常量 `ACCENT/RED/GREEN/…` |
| 卡片显示内容 | `app.py` 的 `_make_pending_card` / `_make_plan_card` / `_make_completed_card` |
| 卡片标题省略规则 | `app.py` 的 `ElidedLabel` / `elide_text()`（省略号 `ELLIPSIS = "…"`） |
| 详情框大小/键盘规则 | `app.py` 的 `open_detail`（`height=5`）/ `bind_detail_keys()` |
| 创建时的详情窗 | `app.py` 的 `DetailEditor` |
| 排序规则 | `app.py` 的 `_home_entry_key` |
| 进度条口径 | `app.py` 的 `_today_progress` |
| 数据结构/新字段 | `models.py`（字段 + `to_dict/from_dict`，记得保留旧数据兼容兜底） |
| 窗口默认尺寸/最小尺寸 | `app.py` 的 `root.geometry` / `root.minsize` |
| 托盘菜单项 | `tray.py` 的 `_show_menu`（`ID_OPEN` / `ID_QUIT`） |

---

## 12. 建议阅读路线

1. `python test_models.py` 先跑一遍——**用输出反推**算法期望值；
2. 读 `models.py` 的 `Series` / `Store`（第 4、5 章），把"一条任务 = 一个 Series"刻进脑子；
3. 读 `app.py` 的 `App.__init__` → `_setup_background` → `_next_trigger`/`_schedule_next_tick`/`_on_tick`（第 6 章），理解"提醒是怎么被排出来的"；
4. 再回到界面：`_build_home_cards` + `open_detail`，看数据是怎么变成卡片和弹窗的；
5. 最后读 `tray.py` / `notifier.py`（第 7 章），看 Windows 那层是怎么被 `ctypes` 和 PowerShell 撬开的。

*祝你读码愉快——这套代码的全部复杂度基本都在"时间"上：把复习日期算对，把提醒时刻排对。*
