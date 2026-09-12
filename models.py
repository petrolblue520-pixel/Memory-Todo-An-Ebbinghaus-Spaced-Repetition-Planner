"""艾宾浩斯日程表 - 核心数据模型与调度逻辑 (纯 Python, 无 GUI 依赖)。

该模块负责:
- Series / Review 数据结构
- 艾宾浩斯复习日期生成 (首次学习 +1d / +4d / +14d / +28d)
- JSON 持久化
- "当日未完成则整条系列后移一天" 的追赶算法
- 完成流转 (全部复习完成 -> 进入完成 list)

刻意不 import kivy, 以便可脱离 GUI 做单元测试。
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import List, Optional, Tuple

# 艾宾浩斯遗忘曲线: 首次学习之后第 1/4/14/28 天各复习一次
REVIEW_OFFSETS = [1, 4, 14, 28]

# 学习计划类任务的 kind:
#   "plan"        学习计划(需复习), 完成学习后生成 4 次复习
#   "simple_plan" 学习计划(不复习), 完成学习后不生成任何复习
PLAN_KINDS = ("plan", "simple_plan")

_DATE_FMT = "%Y-%m-%d"


def parse_date(s: str) -> date:
    return datetime.strptime(s, _DATE_FMT).date()


def fmt_date(d: date) -> str:
    return d.strftime(_DATE_FMT)


@dataclass
class Review:
    """一次复习事件。"""

    n: int                       # 第几次复习 (1..4)
    date: date                   # 计划复习日期
    done: bool = False           # 是否完成
    done_date: Optional[date] = None  # 实际完成日期

    def to_dict(self) -> dict:
        return {
            "n": self.n,
            "date": fmt_date(self.date),
            "done": self.done,
            "done_date": fmt_date(self.done_date) if self.done_date else None,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Review":
        return cls(
            n=d["n"],
            date=parse_date(d["date"]),
            done=d.get("done", False),
            done_date=parse_date(d["done_date"]) if d.get("done_date") else None,
        )


@dataclass
class Series:
    """一条学习内容及其 4 次复习日程。"""

    name: str
    learn_date: date
    reviews: List[Review] = field(default_factory=list)
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    # "review" = 复习日程(自带4次复习); "plan" = 学习计划/绿卡(先完成学习, 再生成复习);
    # "simple_plan" = 学习计划(不复习, 完成学习后不生成任何复习)
    kind: str = "review"
    done_learn: bool = False       # plan / simple_plan: 是否已完成学习
    done_learn_date: Optional[date] = None  # plan / simple_plan: 实际完成学习的日期

    # ---- 构造 ----
    @classmethod
    def create(cls, name: str, learn_date: date) -> "Series":
        s = cls(name=name.strip(), learn_date=learn_date)
        s.reviews = [
            Review(n=i + 1, date=learn_date + timedelta(days=off))
            for i, off in enumerate(REVIEW_OFFSETS)
        ]
        return s

    @classmethod
    def create_plan(cls, name: str, plan_date: date) -> "Series":
        """创建"学习计划"(绿卡): 设定应完成/学习日期, 学习完成前没有复习卡。"""
        return cls(name=name.strip(), learn_date=plan_date, kind="plan")

    @classmethod
    def create_simple_plan(cls, name: str, plan_date: date) -> "Series":
        """创建"学习计划(不复习)": 只有应完成日期, 完成后也不会生成复习。"""
        return cls(name=name.strip(), learn_date=plan_date, kind="simple_plan")

    # ---- 查询 ----
    @property
    def is_completed(self) -> bool:
        """全部复习完成即视为该系列完成 (未完成学习的绿卡不算完成)。"""
        if self.kind in PLAN_KINDS and not self.done_learn:
            return False
        return all(r.done for r in self.reviews)

    def pending_reviews(self) -> List[Review]:
        return [r for r in self.reviews if not r.done]

    def current_review(self) -> Optional[Review]:
        """最早的未完成复习 (即卡片当前展示的那一次)。"""
        pend = self.pending_reviews()
        if not pend:
            return None
        return min(pend, key=lambda r: (r.date, r.n))

    def is_due_today(self, today: Optional[date] = None) -> bool:
        today = today or date.today()
        cur = self.current_review()
        return cur is not None and cur.date <= today

    # ---- 变更 ----
    def complete_current(self, today: Optional[date] = None) -> bool:
        """完成当前 (最早未完成) 复习。返回是否发生变化。"""
        today = today or date.today()
        cur = self.current_review()
        if cur is None:
            return False
        cur.done = True
        cur.done_date = today
        return True

    def complete_review(self, n: int, today: Optional[date] = None) -> bool:
        """完成第 n 次复习。返回是否发生变化。"""
        today = today or date.today()
        for r in self.reviews:
            if r.n == n and not r.done:
                r.done = True
                r.done_date = today
                return True
        return False

    def undo_review_cascade(self, n: int) -> bool:
        """还原第 n 次复习为未完成；其后 (n+1..4) 的复习也一并还原。

        因为若较早一次尚未完成, 后续复习在逻辑上也不应处于已完成状态。
        """
        changed = False
        for r in self.reviews:
            if r.n >= n and r.done:
                r.done = False
                r.done_date = None
                changed = True
        return changed

    def recompute_dates(self, new_learn_date: date) -> None:
        """修改首次学习日期后, 按偏移量重算所有复习日期 (保留完成状态)。"""
        self.learn_date = new_learn_date
        for i, r in enumerate(self.reviews):
            r.date = new_learn_date + timedelta(days=REVIEW_OFFSETS[i])

    def edit_review_date(self, n: int, new_date: date) -> None:
        """修改第 n 次复习日期; 其后 *未完成* 的复习按相同天数整体平移,
        保持复习间隔不变 (已完成的复习不动)。
        """
        idx = n - 1
        delta = (new_date - self.reviews[idx].date).days
        self.reviews[idx].date = new_date
        if delta:
            for r in self.reviews[idx + 1:]:
                if not r.done:
                    r.date = r.date + timedelta(days=delta)

    def restore_review(self, n: int, today: Optional[date] = None) -> None:
        """把第 n 次复习还原成未完成 (并级联还原 n+1..4), 再按到期情况安排回复习表:

        - 当日任务 (第 n 次日期 == 今天): 保持日期, 直接回到今天的复习表。
        - 过去任务 (第 n 次日期 < 今天): 改期到今天, 其后未完成的复习按相同间隔
          整体平移, 保持复习次数与时间间隔的对应关系。
        - 未来任务 (第 n 次日期 > 今天): 到期时间不变, 仅回到复习表。
        """
        today = today or date.today()
        self.undo_review_cascade(n)
        idx = n - 1
        if self.reviews[idx].date < today:
            self.edit_review_date(n, today)

    def change_learn_date(self, new_learn_date: date,
                          today: Optional[date] = None) -> None:
        """修改任务的学习/应完成日期 (整条任务日期), 并同步调整复习计划。

        规则 (已完成复习不动):
        - 未完成复习按 新日期 + 艾宾浩斯偏移(+1/+4/+14/+28) 计算"应到日期";
        - 若最早的未完成复习"应到日期" <= 今天 -> 属今日复习计划:
          它落到今天, 其后未完成复习按 +3/+10/+14 的固定间隔顺延 (保持复习间隔);
        - 若应到日期在今天之后 -> 直接采用 新日期+偏移量 (作为日程计划, 不另改动)。
        """
        today = today or date.today()
        self.learn_date = new_learn_date
        und = [i for i, r in enumerate(self.reviews) if not r.done]
        if not und:
            return
        k = min(und)
        cand_k = new_learn_date + timedelta(days=REVIEW_OFFSETS[k])
        if cand_k <= today:
            for i in und:
                self.reviews[i].date = today + timedelta(
                    days=REVIEW_OFFSETS[i] - REVIEW_OFFSETS[k])
        else:
            for i in und:
                self.reviews[i].date = new_learn_date + timedelta(
                    days=REVIEW_OFFSETS[i])

    def complete_learning(self, today: Optional[date] = None) -> bool:
        """绿卡: 勾选"完成学习"。返回是否发生变化。

        - kind == "plan"        -> 以完成学习日为基准生成 +1/+4/+14/+28 的 4 次复习;
        - kind == "simple_plan" -> 不复习, reviews 保持为空。
        """
        today = today or date.today()
        if self.kind not in PLAN_KINDS or self.done_learn:
            return False
        if self.kind == "plan":
            self.reviews = [
                Review(n=i + 1, date=today + timedelta(days=off))
                for i, off in enumerate(REVIEW_OFFSETS)
            ]
        else:
            self.reviews = []
        self.done_learn = True
        self.done_learn_date = today
        return True

    def undo_learning(self) -> bool:
        """绿卡: 还原"完成学习" -> 回到待学习绿卡 (不复习类型本来就没有复习可删)。"""
        if self.kind not in PLAN_KINDS or not self.done_learn:
            return False
        self.reviews = []
        self.done_learn = False
        self.done_learn_date = None
        return True

    def set_review_mode(self, want_review: bool,
                        today: Optional[date] = None) -> bool:
        """切换学习计划的"是否需要复习" (plan <-> simple_plan)。返回是否变化。

        - 未完成学习: 两个方向都只改 kind (此时本来就没有复习);
        - 已完成学习 + 改为"不复习": 删除已生成的复习, 保留"完成学习"记录;
        - 已完成学习 + 改为"需复习": 以完成学习日为基准重新生成 4 次复习。
        """
        today = today or date.today()
        if self.kind not in PLAN_KINDS:
            return False
        target = "plan" if want_review else "simple_plan"
        if self.kind == target:
            return False
        self.kind = target
        if not self.done_learn:
            self.reviews = []
        elif want_review:
            base = self.done_learn_date or today
            self.reviews = [
                Review(n=i + 1, date=base + timedelta(days=off))
                for i, off in enumerate(REVIEW_OFFSETS)
            ]
        else:
            self.reviews = []
        return True

    def to_simple_plan(self) -> bool:
        """把复习日程(或需复习学习计划)改成"不复习的学习计划": 删除全部复习事件。

        学习日期/名称/已有的"完成学习"标记都保留。返回是否变化。
        """
        if self.kind == "simple_plan":
            return False
        self.kind = "simple_plan"
        self.reviews = []
        return True

    def apply_postpone(self, today: Optional[date] = None) -> bool:
        """追赶算法: 若最早的未完成复习已过期 (计划日期 < 今天),
        说明这些天结束时都未打钩, 按规则整条系列的未完成日程后移,
        使最早未完成复习正好落到今天。已完成的日程不动。

        返回是否发生了移动。
        """
        today = today or date.today()
        pend = self.pending_reviews()
        if not pend:
            return False
        earliest = min(pend, key=lambda r: r.date)
        if earliest.date >= today:
            return False
        offset = (today - earliest.date).days
        for r in pend:
            r.date = r.date + timedelta(days=offset)
        return True

    # ---- 序列化 ----
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "learn_date": fmt_date(self.learn_date),
            "reviews": [r.to_dict() for r in self.reviews],
            "created_at": self.created_at,
            "kind": self.kind,
            "done_learn": self.done_learn,
            "done_learn_date": (fmt_date(self.done_learn_date)
                                if self.done_learn_date else None),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Series":
        s = cls(
            name=d["name"],
            learn_date=parse_date(d["learn_date"]),
            id=d.get("id", uuid.uuid4().hex),
            created_at=d.get("created_at", datetime.now().isoformat()),
            kind=d.get("kind", "review"),
            done_learn=d.get("done_learn", False),
            done_learn_date=(
                parse_date(d["done_learn_date"]) if d.get("done_learn_date") else None),
        )
        s.reviews = [Review.from_dict(r) for r in d.get("reviews", [])]
        return s


class Store:
    """所有系列的集合 + JSON 持久化。"""

    def __init__(self, path: str):
        self.path = path
        self.series: List[Series] = []
        self.load()

    # ---- 持久化 ----
    def load(self) -> None:
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.series = [Series.from_dict(x) for x in data.get("series", [])]
            except (json.JSONDecodeError, KeyError, ValueError):
                self.series = []
        else:
            self.series = []

    def save(self) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        data = {"series": [s.to_dict() for s in self.series]}
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    # ---- 业务操作 ----
    def add(self, name: str, learn_date: date) -> Series:
        s = Series.create(name, learn_date)
        self.series.append(s)
        self.save()
        return s

    def add_plan(self, name: str, plan_date: date) -> Series:
        """新增一条"学习计划"(绿卡): 先设定应完成日期, 学习完成后再生成复习。"""
        s = Series.create_plan(name, plan_date)
        self.series.append(s)
        self.save()
        return s

    def add_simple_plan(self, name: str, plan_date: date) -> Series:
        """新增一条"学习计划(不复习)"绿卡: 只有应完成日期, 完成后不生成复习。"""
        s = Series.create_simple_plan(name, plan_date)
        self.series.append(s)
        self.save()
        return s

    def due_task_count(self, today: Optional[date] = None) -> int:
        """今日待办总数 = 到期的未完成复习 + 到期的未完成学习计划(含不复习类型)。"""
        today = today or date.today()
        n = 0
        for s in self.series:
            if s.kind in PLAN_KINDS and not s.done_learn:
                if s.learn_date <= today:
                    n += 1
                continue
            for r in s.reviews:
                if not r.done and r.date <= today:
                    n += 1
        return n

    def pending_plans(self, today: Optional[date] = None) -> List[Series]:
        """未完成学习的绿卡列表: 到期/过期的在前, 其次按应完成日期升序。"""
        today = today or date.today()
        items = [s for s in self.series
                 if s.kind in PLAN_KINDS and not s.done_learn]
        return sorted(items, key=lambda s: (
            0 if s.learn_date <= today else 1, s.learn_date, s.created_at))

    def completed_learnings(self) -> List[Series]:
        """已完成学习的绿卡 (学习记录, 含不复习类型), 最近完成的在前。"""
        items = [s for s in self.series if s.kind in PLAN_KINDS and s.done_learn]
        return sorted(items, key=lambda s: s.done_learn_date or s.learn_date,
                      reverse=True)

    def remove(self, series_id: str) -> None:
        self.series = [s for s in self.series if s.id != series_id]
        self.save()

    def get(self, series_id: str) -> Optional[Series]:
        return next((s for s in self.series if s.id == series_id), None)

    def run_daily_postpone(self, today: Optional[date] = None) -> bool:
        """对所有未完成系列执行追赶后移。返回是否有变化。"""
        today = today or date.today()
        changed = False
        for s in self.series:
            if not s.is_completed and s.apply_postpone(today):
                changed = True
        if changed:
            self.save()
        return changed

    def active_series(self, today: Optional[date] = None) -> List[Series]:
        """首页复习表: 未完成的系列, 按当前复习日期排序 (今日到期在前)。"""
        today = today or date.today()
        items = [s for s in self.series if not s.is_completed]

        def key(s: Series):
            cur = s.current_review()
            due = cur.date if cur else date.max
            # 今日到期(<=today) 优先, 其次按日期升序
            return (0 if due <= today else 1, due, s.created_at)

        return sorted(items, key=key)

    def completed_series(self) -> List[Series]:
        items = [s for s in self.series if s.is_completed]
        return sorted(items, key=lambda s: s.created_at, reverse=True)

    # ---- 按复习事件展开的卡片 (一条任务 -> 最多 4 张卡片) ----
    def pending_cards(self, today: Optional[date] = None) -> List[Tuple[Series, Review]]:
        """复习表: 所有未完成的复习事件。今日/过期到期在前, 再按日期升序。"""
        today = today or date.today()
        cards = [(s, r) for s in self.series for r in s.reviews if not r.done]
        cards.sort(key=lambda sr: (
            0 if sr[1].date <= today else 1,
            sr[1].date, sr[0].created_at, sr[1].n,
        ))
        return cards

    def completed_cards(self) -> List[Tuple[Series, Review]]:
        """完成 list: 所有已完成的复习事件, 最近完成的在前。"""
        cards = [(s, r) for s in self.series for r in s.reviews if r.done]
        cards.sort(key=lambda sr: (sr[1].done_date or sr[1].date, sr[1].n), reverse=True)
        return cards
