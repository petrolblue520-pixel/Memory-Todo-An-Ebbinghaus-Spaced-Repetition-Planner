"""models.py 的无 GUI 逻辑测试。直接 `python test_models.py` 运行。"""

import os
import tempfile
from datetime import date, timedelta

from models import Series, Store, REVIEW_OFFSETS, fmt_date


def _check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok - {msg}")


def test_review_generation():
    print("test_review_generation")
    s = Series.create("学习高数拉格朗日", date(2026, 1, 1))
    dates = [fmt_date(r.date) for r in s.reviews]
    _check(dates == ["2026-01-02", "2026-01-05", "2026-01-15", "2026-01-29"],
           f"复习日期应为 2/5/15/29 日, 实际 {dates}")
    _check([r.n for r in s.reviews] == [1, 2, 3, 4], "复习次数编号 1..4")
    _check(REVIEW_OFFSETS == [1, 4, 14, 28], "偏移量 +1/+4/+14/+28")


def test_current_and_due():
    print("test_current_and_due")
    learn = date(2026, 1, 1)
    s = Series.create("x", learn)
    # 第一次复习日 (1/2) 当天到期
    _check(s.current_review().n == 1, "当前复习应是第1次")
    _check(s.is_due_today(date(2026, 1, 2)), "1/2 应为到期")
    _check(not s.is_due_today(date(2026, 1, 1)), "1/1 (学习当天) 尚未到期第1次复习? 实际第一次为1/2")


def test_complete_flow():
    print("test_complete_flow")
    learn = date(2026, 1, 1)
    s = Series.create("x", learn)
    s.complete_current(date(2026, 1, 2))
    _check(s.reviews[0].done and str(s.reviews[0].done_date) == "2026-01-02", "完成第1次并记录完成日期")
    _check(s.current_review().n == 2, "完成后当前变为第2次")
    _check(not s.is_completed, "尚未全部完成")
    for d in [date(2026, 1, 5), date(2026, 1, 15), date(2026, 1, 29)]:
        s.complete_current(d)
    _check(s.is_completed, "四次全部完成后系列完成")
    _check(s.current_review() is None, "全部完成后无当前复习")


def test_postpone_catch_up():
    print("test_postpone_catch_up")
    learn = date(2026, 1, 1)  # 复习: 1/2, 1/5, 1/15, 1/29
    s = Series.create("x", learn)
    # 今天是 1/8, 第1次(1/2)一直没完成 -> 过期 6 天
    changed = s.apply_postpone(date(2026, 1, 8))
    _check(changed, "过期未完成应触发后移")
    dates = [fmt_date(r.date) for r in s.reviews]
    # 最早未完成(第1次)应落到今天 1/8, 其余各 +6 天
    _check(dates == ["2026-01-08", "2026-01-11", "2026-01-21", "2026-02-04"],
           f"整条后移6天, 实际 {dates}")


def test_postpone_keeps_completed():
    print("test_postpone_keeps_completed")
    learn = date(2026, 1, 1)  # 1/2, 1/5, 1/15, 1/29
    s = Series.create("x", learn)
    # 第1次按时完成 (1/2)
    s.complete_current(date(2026, 1, 2))
    # 今天 1/10, 第2次(1/5)未完成 -> 过期5天
    s.apply_postpone(date(2026, 1, 10))
    dates = [fmt_date(r.date) for r in s.reviews]
    _check(dates[0] == "2026-01-02", "已完成的第1次日期不变")
    _check(dates[1] == "2026-01-10", "第2次后移到今天 1/10")
    _check(dates[2] == "2026-01-20" and dates[3] == "2026-02-03", "第3/4次同步后移5天")
    _check(s.reviews[0].done, "第1次仍为完成状态")


def test_postpone_no_change_when_future():
    print("test_postpone_no_change_when_future")
    s = Series.create("x", date(2026, 1, 1))
    # 今天 1/1, 第1次在 1/2 (未来), 不应后移
    _check(not s.apply_postpone(date(2026, 1, 1)), "未到期不后移")


def test_store_persistence_and_lists():
    print("test_store_persistence_and_lists")
    tmp = os.path.join(tempfile.mkdtemp(), "data.json")
    st = Store(tmp)
    a = st.add("A", date(2026, 1, 1))
    b = st.add("B", date(2026, 1, 1))
    # 完成 B 的四次
    for d in [date(2026, 1, 2), date(2026, 1, 5), date(2026, 1, 15), date(2026, 1, 29)]:
        b.complete_current(d)
    st.save()

    st2 = Store(tmp)  # 重新加载
    _check(len(st2.series) == 2, "持久化后应有两条系列")
    _check(len(st2.active_series()) == 1, "活跃列表只含未完成的 A")
    _check(len(st2.completed_series()) == 1, "完成列表含 B")
    _check(st2.completed_series()[0].name == "B", "完成的是 B")

    st2.remove(a.id)
    _check(len(st2.series) == 1, "删除后剩一条")


def test_active_sorting_due_first():
    print("test_active_sorting_due_first")
    tmp = os.path.join(tempfile.mkdtemp(), "data.json")
    st = Store(tmp)
    # 今天设为 1/10
    today = date(2026, 1, 10)
    future = st.add("future", date(2026, 1, 20))   # 第1次复习 1/21 (未来)
    due = st.add("due", date(2026, 1, 1))          # 第1次复习 1/2 (过期)
    st.run_daily_postpone(today)  # 让 due 落到今天
    order = [s.name for s in st.active_series(today)]
    _check(order[0] == "due", f"今日到期的应排最前, 实际顺序 {order}")


def test_complete_review_specific():
    print("test_complete_review_specific")
    s = Series.create("x", date(2026, 1, 1))
    _check(s.complete_review(2, date(2026, 1, 5)), "可完成指定的第2次")
    _check(s.reviews[1].done and not s.reviews[0].done, "只完成第2次, 第1次不受影响")


def test_undo_cascade():
    print("test_undo_cascade")
    s = Series.create("x", date(2026, 1, 1))
    for n in (1, 2, 3, 4):
        s.complete_review(n, date(2026, 1, 1))
    _check(all(r.done for r in s.reviews), "四次全部完成")
    s.undo_review_cascade(2)  # 还原第2次 -> 2,3,4 都还原
    done = [r.done for r in s.reviews]
    _check(done == [True, False, False, False], f"第1次保留, 2/3/4 还原, 实际 {done}")
    _check(s.reviews[1].done_date is None, "还原后清除完成日期")


def test_edit_review_date_shifts_later_incomplete():
    print("test_edit_review_date_shifts_later_incomplete")
    s = Series.create("x", date(2026, 1, 1))  # 1/2, 1/5, 1/15, 1/29
    s.complete_review(1, date(2026, 1, 2))     # 第1次已完成
    # 把第2次从 1/5 改到 1/8 (+3天), 第3/4次(未完成)同步 +3, 已完成第1次不动
    s.edit_review_date(2, date(2026, 1, 8))
    dates = [fmt_date(r.date) for r in s.reviews]
    _check(dates == ["2026-01-02", "2026-01-08", "2026-01-18", "2026-02-01"],
           f"改期后未完成项按间隔平移, 实际 {dates}")


def test_restore_review_reschedules_past_to_today():
    print("test_restore_review_reschedules_past_to_today")
    # 很久以前学的, 4 次全部完成过; 复习: 1/2, 1/5, 1/15, 1/29
    s = Series.create("x", date(2026, 1, 1))
    for n in (1, 2, 3, 4):
        s.complete_review(n, date(2026, 1, 1))
    today = date(2026, 2, 10)
    # 还原第 2 次(过去 1/5): 2/3/4 级联还原, 第2次改到今天, 3/4 保持间隔平移
    s.restore_review(2, today)
    done = [r.done for r in s.reviews]
    _check(done == [True, False, False, False], f"第1次保留, 2/3/4 还原, 实际 {done}")
    dates = [fmt_date(r.date) for r in s.reviews]
    # 第2次落到 2/10; 原 1/5->1/15 间隔10天 => 3次 2/20; 1/15->1/29 间隔14天 => 4次 3/6
    _check(dates[1] == "2026-02-10", f"过去任务改期到今天, 实际 {dates[1]}")
    _check(dates[2] == "2026-02-20" and dates[3] == "2026-03-06",
           f"其后未完成项保持间隔平移, 实际 {dates}")


def test_restore_review_future_keeps_date():
    print("test_restore_review_future_keeps_date")
    s = Series.create("x", date(2026, 1, 1))  # 1/2, 1/5, 1/15, 1/29
    for n in (1, 2, 3, 4):
        s.complete_review(n, date(2026, 1, 1))
    today = date(2026, 1, 10)
    # 还原第 3 次(未来 1/15): 到期时间不变
    s.restore_review(3, today)
    done = [r.done for r in s.reviews]
    _check(done == [True, True, False, False], f"1/2 保留, 3/4 还原, 实际 {done}")
    dates = [fmt_date(r.date) for r in s.reviews]
    _check(dates[2] == "2026-01-15" and dates[3] == "2026-01-29",
           f"未来任务到期时间不变, 实际 {dates}")


def test_pending_and_completed_cards():
    print("test_pending_and_completed_cards")
    tmp = os.path.join(tempfile.mkdtemp(), "data.json")
    st = Store(tmp)
    today = date(2026, 1, 10)
    a = st.add("A", date(2026, 1, 8))   # 复习: 1/9(过期), 1/12, 1/22, 2/5
    a.complete_review(1, date(2026, 1, 9))
    b = st.add("B", date(2026, 1, 6))   # 复习: 1/7(过期)...
    st.run_daily_postpone(today)

    pend = st.pending_cards(today)
    # A: 3 未完成 + B: 4 未完成 = 7
    _check(len(pend) == 7, f"未完成复习卡片应为 7, 实际 {len(pend)}")
    # 最靠前的应是今日到期(<=today)的
    _check(pend[0][1].date <= today, "第一张卡片应是今日/过期到期")

    comp = st.completed_cards()
    _check(len(comp) == 1 and comp[0][0].name == "A", "已完成卡片仅 A 的第1次")


def main():
    tests = [
        test_review_generation,
        test_current_and_due,
        test_complete_flow,
        test_postpone_catch_up,
        test_postpone_keeps_completed,
        test_postpone_no_change_when_future,
        test_store_persistence_and_lists,
        test_active_sorting_due_first,
        test_complete_review_specific,
        test_undo_cascade,
        test_edit_review_date_shifts_later_incomplete,
        test_restore_review_reschedules_past_to_today,
        test_restore_review_future_keeps_date,
        test_pending_and_completed_cards,
    ]
    for t in tests:
        t()
    print("\nALL TESTS PASSED")


if __name__ == "__main__":
    main()
