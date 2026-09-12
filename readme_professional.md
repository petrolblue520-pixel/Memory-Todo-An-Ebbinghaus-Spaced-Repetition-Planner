# readme_professional · 艾宾浩斯日程表源码剖析（进阶篇）

> 面向**想读懂/学习这份源码**的读者：本文把"这个 App 是怎么做出来的"讲透——
> 分层结构、每个文件干什么、算法与数值怎么算、Windows 弹窗/托盘/后台/自启怎么做、
> 进程之间怎么协作（本文没有任何 TCP 端口，详见 6.6），以及开发中**遇到的问题和解决过程**。
>
> 配套入门文档（怎么用、怎么算的概览）见 **[README.md](./README.md)**，产品设计综述见 **[创作说明.md](./创作说明.md)**。

---

## 1. 30 秒看懂全景

- 语言/依赖：**纯 Python 3 标准库**（`tkinter` + `ctypes` + `subprocess`），Windows 专属，无第三方库。
- 运行：双击 `启动艾宾浩斯.vbs` → `pythonw app.py`（无黑窗）→ Tkinter 主窗口。
- 一句话逻辑：**一条"学习内容"= 一个 Series，自动带 4 次复习（Review）**；每次到期没打卡，下次启动自动顺延到今天（"永远不过期"）；到期/逾期用红色（复习）或绿色（学习计划）边框突出。
- 数据：单文件 JSON `appdata/data.json`，原子写入。
- 通知/常驻：主程序自身负责（缩托盘 + 定点整点弹系统通知）；`reminder.py` 是可选轻量替代品。

```
用户双击 .vbs ──► pythonw app.py ──► App(root)  (Tkinter 主线程)
                         │
        ┌────────────────┼───────────────────────────────┐
   models.py(纯逻辑)  notifier.py(系统弹窗)          tray.py(托盘)
   Store→data.json    PowerShell→WinRT Toast    ctypes→Shell_NotifyIcon
   postpone/改期/计数   phrases/*.txt 文案           隐藏窗口+消息泵
```

---

## 2. 文件地图（每个文件干什么）

### 2.1 代码主体

| 文件 | 作用（一句话） |
| --- | --- |
| [`app.py`](./app.py) | 主程序：三页界面、卡片、详情弹窗、托盘与提醒调度 |
| [`models.py`](./models.py) | 核心模型与复习/后移/改期算法、JSON 存取 |
| [`notifier.py`](./notifier.py) | Windows 系统通知 + 按时段组装提示语 |
| [`tray.py`](./tray.py) | 纯 ctypes 调 Win32 实现的系统托盘图标 |
| [`reminder.py`](./reminder.py) | 可选轻量进程：只定点发通知，无窗口 |
| [`test_models.py`](./test_models.py) | 核心逻辑单元测试（无界面可跑） |
| [`app_smoke.py`](./app_smoke.py) | 界面冒烟：临时数据建界面并截图 |
| [`创作说明.md`](./创作说明.md) | 产品/设计综述（含安卓移植思路） |

### 2.2 数据与文案

| 文件/目录 | 作用（一句话） |
| --- | --- |
| `appdata/data.json` | 数据存档：所有 Series 的 JSON 列表 |
| `phrases/*.txt` | 提示语文案外置，可改不改码 |
| `appdata/data.json.bak_20260902` | 某次整理的备份（随时可还原） |

### 2.3 启动 / 运维脚本

| 文件 | 作用（一句话） |
| --- | --- |
| `启动艾宾浩斯.vbs` | 无黑窗启动主程序（推荐双击入口） |
| `启动艾宾浩斯.bat` | 批处理启动入口（备选） |
| `run_app_bg.vbs` | 后台缩托盘启动（`--minimized`，自启指向它） |
| `run_reminder.vbs` | 后台启动轻量提醒代理 reminder.py |
| `创建桌面快捷方式.vbs` | 在桌面生成「艾宾浩斯日程表」快捷方式 |
| `开启开机提醒.vbs` | 写入 Startup 快捷方式并立即后台启动 |
| `关闭开机提醒.vbs` | 删除自启项并用 WMI 停掉后台进程 |
| `测试通知.bat` | 立即发一条测试通知（排错用） |
| `诊断_出错看这里.bat` | 带控制台运行 app.py，保留报错窗口 |
| `README.md` | 入门篇：使用 + 数值计算概览 |
| `readme_professional.md` | 本文：源码剖析 |

> 其余：`smoke_*.png`（冒烟截图产物）、`memory_todo.rar`（整包备份）、`debug.log`（输入法无关日志，可忽略）、`.kivy/`、`.venv/`（历史遗留/测试虚拟环境，程序不依赖）。

---

## 3. 分层架构：为什么这样拆

```
┌───────────────────────────────────────────────┐
│  展示层  app.py（Tkinter）                      │  只负责界面与交互
│  弹窗/常驻  notifier.py / tray.py               │  系统能力封装
│  纯逻辑  models.py（无 GUI 依赖）                │  算法 + 存储
│  数据  appdata/data.json                        │  单文件持久化
└───────────────────────────────────────────────┘
```

- `models.py` **刻意不 import tkinter**（模块 docstring 明说），所以能脱离 GUI 用 `test_models.py` 单测算法。
- `app.py` 只做两件事：①把模型数据画成卡片；②把用户操作翻译成模型方法调用（`complete_review` / `restore_review` / `edit_review_date` / `change_learn_date` / `complete_learning` / `undo_learning`）。
- `notifier.py`、`tray.py` 都是"能力封装"：谁需要系统通知/托盘谁调用；`reminder.py` 只是复用 `notifier` 的轻量外壳。

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
| `kind` | `"review"`（复习日程）或 `"plan"`（学习计划/绿卡） |
| `done_learn` / `done_learn_date` | plan 是否已完成学习 / 完成日期 |

> 兼容性：`from_dict` 用 `d.get("kind", "review")` 等兜底——**旧版 JSON 没有这些字段也能正常加载**；保存后才补写新字段。

### 4.2 持久化（JSON）

- 默认路径：`os.path.dirname(__file__) + "appdata/data.json"`，可用环境变量 `EBBINGHAUS_DATA_DIR` 覆盖（冒烟测试就靠它指向临时目录，不碰真实数据）。
- **原子写**：先写 `data.json.tmp` 再 `os.replace(tmp, path)`——写入中途断电/崩溃不会留下半个文件；`load()` 有 `try/except` 兜底，坏了也不至于闪退。

---

## 5. 算法详解（核心）

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
- 当日（date == today）：保持日期；
- 过去（date < today）：改到今天，其后未完成复习**整体平移**，保持间隔；
- 未来（date > today）：不动。

### 5.4 自动后移（"不怕漏"的灵魂）

```python
if 最早未完成复习.date < today:            # 这些天都没打卡
    delta = today - 最早未完成复习.date      # 迟到天数
    for r in 该系列所有未完成复习: r.date += delta   # 整体顺延
# 效果：最早未完成复习正好落到"今天"；已完成的永远不动
```
触发时机：**只在启动时**（`App.__init__`）和**跨天 tick** 各跑一次——特意不在运行中反复跑，避免覆盖用户手动"改期"。

### 5.5 复习行"改期"（edit_review_date）

把第 n 次改到新日期：该次直接改；其后**未完成**的按相同天数整体平移（已完成不动）→ 复习间隔保持。

### 5.6 任务"学习日期"改期（change_learn_date）—— 最近新增的规则

对**未完成复习**按 新日期 + 偏移量 重排：
- 若**最早的未完成复习"应到日期" ≤ 今天** → 按"今日复习计划"处理：它落到今天，其余未完成按 `OFFSETS[i]−OFFSETS[k]`（即 +3/+10/+14）顺延，保持间隔；
- 若应到日期 > 今天 → 作为日程计划：未完成复习直接采用 `新日期 + OFFSETS[i]`；
- **已完成的复习日期一律不动**。

（学习计划绿卡改"应完成日期"只改警示日期本身——它的复习以"完成学习当天"为基准生成，见 5.7。）

### 5.7 学习计划（绿卡）生命周期

```
创建(未来日期) → kind="plan", reviews=[]        （1 张绿卡，应完成日期=所选日期）
   │  到期/逾期：绿框 + 红字"今日到期/拖欠N天"，计入"还剩N项"
   ▼ 勾选"完成学习"(complete_learning)
reviews = 完成日 +1/+4/+14/+28 生成 4 次复习       （完成学习记录进"完成"清单）
   │  取消勾选(undo_learning)：删掉复习，放回学习计划
```
说明：绿卡**只作计划提醒，不参与自动后移**（程序不改"应完成日期"，逾期就红字警示）。

### 5.8 统计口径（顶部/托盘/系统通知/进度条共用）

- 今日待办 `due_task_count(today)` =
  `未完成复习且 date ≤ today` 数量 ＋ `未完成学习计划且 learn_date ≤ today` 数量。
- 进度条 `_today_progress`：`pct = 已完成 / (已完成 + 未完成)`；分母为 0 → 100%。
  - 已完成：今天勾掉的到期复习（`done_date==today 且 date<=today`）＋ 今天完成的到期学习计划；
  - 未完成：同上 due_task_count。
- 拖欠天数：绿卡上 `today − learn_date`（红字）。

### 5.9 学习表排序（`_home_entry_key`）

1. **今日/过期块**（date ≤ today）在最前：块内先复习卡、后学习计划卡，块内再按日期；
2. **未来块**：严格按日期，同一天内复习在前、学习计划在后；
3. 兜底比较 `created_at`。已"完成学习"的绿卡按复习事件参与排序。

### 5.10 定时与文案

- 通知时刻 `TRIGGER_HOURS = [0, 7, 9, 13, 17, 20]`（0 即 24 点）。
- `notifier.greeting()` 按 `hour` 分 6 个时段文件；`task_phrase(n)` 从"有未完成/全部完成"两组文案抽一句，`*` 替换成剩余数。
- 主界面顶部提示语：**只在待办数变化时重随**（缓存 `_last_due_count`），避免每次刷新跳字。

---

## 6. Windows 集成：弹窗 / 托盘 / 后台 / 单实例 / 自启

### 6.1 系统通知怎么弹的（notifier.py）

不引第三方库，借道 **PowerShell 的 WinRT Toast API**：

1. Python 组好 PowerShell 脚本字符串（`ToastNotificationManager` → `ToastText02` 模板 → 写两行 text）；
2. **编码防坑**：`base64(脚本.encode("utf-16-le"))`，用 `powershell -NoProfile -EncodedCommand <base64>` 执行——彻底绕开命令行中文乱码/引号注入；
3. `creationflags=CREATE_NO_WINDOW`（不闪黑窗）、`timeout=20`、异常静默（通知失败不影响主程序）。

### 6.2 托盘常驻怎么做的（tray.py）

- 用 `ctypes` 声明 Win32 结构体（`NOTIFYICONDATAW`、`WNDCLASSW`）后调 `Shell_NotifyIconW(NIM_ADD/…)` 加图标；
- **单线程设计**：`RegisterClassW` 注册一个隐藏窗口接收托盘回调消息，但不在独立线程里跑消息循环，而是由 Tk 主线程用 `root.after(200, pump)` 周期性 `PeekMessageW/DispatchMessageW` 派发——回调都在主线程，能安全操作 Tk；
- 双击托盘 → `on_open`（显示主窗）；右键 → `CreatePopupMenu` + `TrackPopupMenu`（打开/退出）。

### 6.3 单实例 + "第二个进程把已有窗口唤出来"

- `app.py` 里 `CreateMutexW`（名 `EbbinghausScheduleAppSingleton`）：拿不到锁说明已有实例 → `_signal_existing_instance()`；
- 新进程用 `FindWindowW(None, tray.TRAY_WINDOW_TITLE)` 找到老进程的托盘隐藏窗，`PostMessageW(WM_TRAY_SHOW)` → 老进程把主界面 `deiconify` 显示，新进程直接退出。

### 6.4 关窗不退出、后台定点提醒（app.py 自管）

- `root.protocol("WM_DELETE_WINDOW", hide_window)`：点 X 只 `root.withdraw()`（有托盘）缩后台；
- `_tick`：`root.after(20000)` 周期跑——跨天时更新 `self.today` + 再跑一次自动后移；到整点触发 `_maybe_notify`（用 `(date,hour)` 缓存去重，同一小时只发一次）；
- `--minimized` 启动 = 开机自启模式：直接缩托盘，并 `after(1500)` 先发一条"今天任务"通知；若启动时正处整点，先记下避免立刻重复发。

### 6.5 开机自启怎么做的（VBS 一族）

- `开启开机提醒.vbs`：向 **Startup 文件夹**写 `EbbinghausApp.lnk`，目标 = `run_app_bg.vbs`，并立刻运行它；`run_app_bg.vbs` 用无窗解释器跑 `pythonw app.py --minimized`；
- `关闭开机提醒.vbs`：删快捷方式，并用 **WMI（Win32_Process）按 CommandLine 包含 app.py/reminder.py** 杀掉后台 python——不用中文比较，跨进程稳定。

### 6.6 "端口调用"？—— 本程序没有网络端口，用的是这几条"接口"

没有监听 TCP/UDP 端口，进程/系统间的"接口调用"是这些通道：

| 通道 | 干什么 |
| --- | --- |
| ctypes → user32 / shell32 / kernel32 | 托盘、隐藏窗、弹菜单、互斥、跨进程消息 |
| subprocess → PowerShell | 弹 WinRT 系统通知 |
| WScript/VBS（双击 .vbs） | 无黑窗启动、快捷方式、开机自启 |
| Startup 快捷方式 + `--minimized` 参数 | 开机进托盘 |
| CreateMutex / FindWindow / PostMessage | 单实例与"唤起旧窗口" |
| WMI Win32_Process | 关闭脚本按命令行杀后台进程 |
| 环境变量 `EBBINGHAUS_DATA_DIR` | 外部指定数据目录（测试/多数据） |

---

## 7. 界面实现要点（app.py）

- **三页切换**：三个 `Frame` 全部 `place(relwidth=1, relheight=1)` 叠在一起，`show_page` 用 `.lift()` 换层；底部导航高亮选中项。
- **可滚动卡片列表**：`tk.Canvas` + `ttk.Scrollbar` + 内部 Frame，`<Configure>` 更新 scrollregion，`<Enter>/<Leave>` 绑定/解绑滚轮。
- **两种卡片一张列表**：`_build_home_cards` 把"复习事件"和"学习计划"合成一个列表统一排序渲染（复习卡红/灰框、绿卡绿/灰框）；右侧 `Checkbutton` 即"打卡"。
- **详情弹窗（模态）**：`tk.Toplevel` + `grab_set()`；`DatePicker` 用 `Spinbox` 拼年/月/日并 `wait_window` 等结果；行内按钮"改期/修改名称/删除"直接操作内存 Series 后 `store.save()` + `refresh_lists()`。
- **进度条**：底部一条 `Frame`（灰底 = 未完成），内部 `Frame` 用 `place(relwidth=percent/100, relheight=1)` 当绿色填充，窗口缩放自动跟随；百分比文字同步。
- **中文字体 + 高分屏**：选"微软雅黑→黑体→宋体"，并把默认字体族设成中文字体；`SetProcessDpiAwareness(1)` + `tk scaling = dpi/72`，布局尺寸用 `px()` 按 `dpi/96` 放大。

---

## 8. 遇到的问题 & 解决过程（都是真实踩过的）

| # | 问题 | 解决方式 |
| --- | --- | --- |
| 1 | **中文输入法候选框**在部分 GUI 框架里不弹 | 从 Kivy 改用 **Tkinter**（原生 Windows 文本控件，拼音候选框正常）；`.kivy/` 目录是遗留物，不再使用 |
| 2 | 双击启动闪黑窗、控制台中文乱码 | 用 `.vbs` + `pythonw` 无窗启动；出错时用 `.bat` 保留控制台看 Traceback |
| 3 | **Toast 中文乱码 / 引号注入** | PowerShell `-EncodedCommand`：UTF-16LE + Base64 传参，单引号翻倍转义 |
| 4 | **ctypes 64 位句柄截断**导致崩溃 | 所有句柄函数显式声明 `restype`/`argtypes`（如 `c_void_p`/`HWND`） |
| 5 | **托盘回调被 GC** 后窗口收消息崩溃 | `WNDPROCTYPE(self._on_message)` 存成 `self._wndproc` 并保持引用 |
| 6 | 托盘消息循环与 Tk 主线程冲突 | 不另开线程：隐藏窗消息由 `root.after(200)` 的 `pump()` 在主线程派发；提醒另开则用独立进程 `reminder.py`，绝不在 Tk 线程外碰控件 |
| 7 | JSON 写到一半损坏 | 先写 `tmp` 再 `os.replace` 原子替换；读文件异常兜底为空列表不闪退 |
| 8 | 自动后移会**覆盖用户手动改期** | 只在启动/跨天时跑一次，运行中不再动 |
| 9 | 开机自启 + 手动双击开两份、发两条通知 | 单实例互斥锁；自启只挂主程序（不再挂 reminder），`reminder.py` 仅保留作可选 |
| 10 | 同一整点重复通知 | 缓存 `(date, hour)`，启动恰在整点时预记一次 |
| 11 | 顶部提示语每次刷新都跳 | 只有待办**数目变化**才重随机 |
| 12 | 高分屏字体发虚/布局缩错 | DPI Aware + `tk scaling` + `px()` 尺寸换算 |
| 13 | 提醒代理错过休眠后的整点 | `reminder.py` 分段小睡（≤30s）重算目标时刻；主程序另有 20s tick 跨天校正 |
| 14 | **绿卡"完成学习"后复习卡不出现（用户实测发现的 bug）** | 根因：首页构建对 `kind=="plan"` 只画"未完成学习"的绿卡分支，完成学习后生成的 reviews 被跳过。修复：完成后按普通复习事件展开渲染，排序键改判"是否复习事件" |
| 15 | 全绿卡片无法突出"今天要完成" | 绿卡改为**只有到期/逾期才绿框**，未来未到期与普通卡一致灰框 |
| 16 | 学习计划逾期该不该顺延 | 设计为**不自动后移**：应完成日期灰字固定 + 红字"拖欠N天"，持续计入任务数，直到完成/删除（计划是承诺，不是流水线） |

> 更完整的产品演进理由可读 `创作说明.md`；每条算法都有对应单测（`test_models.py`）。

---

## 9. 测试与冒烟

```powershell
python test_models.py     # 14 项算法单测（复习生成/完成后移/还原/改期/统计…）
python app_smoke.py       # 建临时数据 → 建界面 → 逐页截图（smoke_*.png）
python reminder.py --once # 只发一条通知，验证弹窗链路
python 测试通知.bat        # 图形化排错入口
```

冒烟与单测都通过 `EBBINGHAUS_DATA_DIR` 指向临时目录，**不会污染真实数据**。

---

## 10. "如果我想改……"（上手指引）

| 想改什么 | 去哪个文件改 |
| --- | --- |
| 提示语文案 | `phrases/*.txt`（加删行即可，`#` 是注释） |
| 通知时刻 | `app.py` / `reminder.py` 顶部 `TRIGGER_HOURS` |
| 复习间隔（+1/+4/+14/+28） | `models.py` 的 `REVIEW_OFFSETS`（注意：只对新生成/重算生效） |
| 主题色/边框色 | `app.py` 顶部常量 `ACCENT/RED/GREEN/…` |
| 卡片显示内容 | `app.py` 的 `_make_pending_card` / `_make_plan_card` / `_make_completed_card` |
| 排序规则 | `app.py` 的 `_home_entry_key` |
| 进度条口径 | `app.py` 的 `_today_progress` |
| 数据结构/新字段 | `models.py`（字段 + `to_dict/from_dict`，注意旧数据兼容兜底） |
| 窗口默认尺寸/最小尺寸 | `app.py` 的 `root.geometry` / `root.minsize` |

---

*祝你读码愉快——先 `test_models.py` 跑一遍算法，再对照第 5 章看实现，最后用 `app_smoke.py` 看它怎么被画到屏幕上。*
