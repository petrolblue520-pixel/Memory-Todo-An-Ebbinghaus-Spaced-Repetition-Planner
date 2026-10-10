"""flies.py / prefs.py / models.due_keys 的无界面逻辑测试。

直接 `python test_flies.py` 运行。会真的创建分层窗口 (但不会显示动画循环),
所以需要有桌面会话; 没有 Tk 的环境会自动跳过窗口相关断言。
"""

import os
import tempfile
import time
from datetime import date, timedelta

os.environ["EBBINGHAUS_DATA_DIR"] = tempfile.mkdtemp()

import tkinter as tk                                   # noqa: E402

from models import Store                               # noqa: E402
from prefs import Prefs                                # noqa: E402


def _check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok - {msg}")


def _make_store(today):
    st = Store(os.path.join(os.environ["EBBINGHAUS_DATA_DIR"], "data.json"))
    st.add("高数-拉格朗日", today - timedelta(days=3))
    st.add("英语 Unit 5", today - timedelta(days=1))
    st.add("数据结构", today)
    st.add_plan("整理错题本", today)
    return st


class Harness:
    """一个最小夹具: 临时数据目录 + 隐藏的 Tk root + store/prefs/swarm。"""

    def __init__(self):
        import importlib
        import models
        importlib.reload(models)
        self.today = date.today()
        self.dir = tempfile.mkdtemp()
        self.store = _make_store(self.today)
        self.root = tk.Tk()
        self.root.withdraw()
        self.prefs = Prefs(os.path.join(self.dir, "prefs.json"), load=False)
        self.prefs.set("pause_when_covered", False, save=False)
        self.prefs.set("pause_when_idle", False, save=False)
        self.prefs.set("battery_low_fps", False, save=False)
        self.prefs.set("hide_from_capture", False, save=False)

    def due(self):
        self.today = date.today()
        return self.store.due_keys(self.today)

    def swarm(self, **kw):
        import flies
        self.prefs.update(kw, save=False)
        self.sw = flies.FlySwarm(self.root, self.prefs, lambda v: int(v),
                                 self.dir, self.due)
        return self.sw

    def step(self, swarm, seconds, dt=1.0 / 30.0):
        """手动推进状态机 (不跑 after 循环, 测试才可控)。"""
        n = max(1, int(seconds / dt))
        for _ in range(n):
            swarm._do_state(time.perf_counter(), dt)
            self.root.update()

    def close(self):
        try:
            if getattr(self, "sw", None) is not None:
                self.sw.shutdown()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass


def test_due_keys_matches_count():
    print("test_due_keys_matches_count")
    d = tempfile.mkdtemp()
    st = Store(os.path.join(d, "data.json"))
    today = date(2026, 3, 10)
    st.add("a", today - timedelta(days=2))
    st.add_simple_plan("b", today - timedelta(days=1))
    st.add("future", today + timedelta(days=3))
    keys = st.due_keys(today)
    _check(len(keys) == st.due_task_count(today), "due_keys 与 due_task_count 条数一致")
    _check(all(":" in k for k, _n in keys), "每个键都带任务标识")
    _check(len({k for k, _ in keys}) == len(keys), "键不重复")
    # 完成一张卡 -> 键消失
    first = st.series[0]
    first.complete_review(1, today)
    _check(len(st.due_keys(today)) == len(keys) - 1, "完成一张卡后键少一个")


def test_invariant_and_actions():
    print("test_invariant_and_actions")
    h = Harness()
    try:
        sw = h.swarm(max_entities=15)
        sw.sync()
        n = len(h.due())
        _check(len(sw.entities) == n, "初始实体数 == 今日待完成数 (%d)" % n)
        h.step(sw, 2.0)
        _check(all(e.state != "emerge" or e.state_t > 0 for e in sw.entities),
               "实体在推进状态机")

        killed = sw.kill_all_to_eggs()
        _check(killed == n, "杀死全部苍蝇 -> %d 只" % killed)
        h.step(sw, 1.2)
        _check(sum(1 for e in sw.entities if e.kind == "egg") == n,
               "全部变成卵, 数量不变 (不变量保持)")
        _check(len(sw.entities) == n, "实体总数没变")

        revived = sw.revive_all()
        _check(revived == n, "全部复活 -> %d 个卵破壳" % revived)
        h.step(sw, 1.0)
        _check(sum(1 for e in sw.entities if e.kind == "fly") == n, "全部变回苍蝇")

        # 上限调小 -> 超出的实体立即消失 (苍蝇和卵都算, 不变成卵)
        h.prefs.set("max_entities", 2, save=False)
        sw.sync()
        h.step(sw, 0.6)                       # 0.34s 淡出之后就彻底没了
        _check(len(sw.entities) == 2, "上限调小后实体数 == 2 (多余的立即消失)")
        _check(sum(1 for e in sw.entities if e.kind == "egg") == 0, "没有偷偷变成卵")
    finally:
        h.close()


def test_task_completion_removes_entity():
    print("test_task_completion_removes_entity")
    h = Harness()
    try:
        sw = h.swarm(max_entities=15)
        sw.sync()
        n = len(sw.entities)
        # 找到第一张卡的 series, 完成它
        st = h.store
        target = None
        for k, _name in h.due():
            sid = k.split(":")[0]
            target = st.get(sid)
            break
        target.complete_current(h.today)
        sw.sync()
        h.step(sw, 1.0)
        _check(len(sw.entities) == n - 1, "任务完成后绑定它的苍蝇消失")
    finally:
        h.close()


def test_killed_egg_dies_when_task_done():
    print("test_killed_egg_dies_when_task_done")
    h = Harness()
    try:
        sw = h.swarm(max_entities=15)
        sw.sync()
        fly = next(e for e in sw.entities if e.kind == "fly")
        key = fly.key
        sw.swat(fly)
        h.step(sw, 1.0)
        eggs = [e for e in sw.entities if e.kind == "egg"]
        _check(len(eggs) == 1, "拍死后原地留了一个卵")
        # 1 小时内该任务完成 -> 卵消失
        sid = key.split(":")[0]
        h.store.get(sid).complete_current(h.today)
        sw.sync()
        h.step(sw, 1.0)
        _check(not any(e.kind == "egg" for e in sw.entities), "任务完成后对应的卵消失")
    finally:
        h.close()


def test_hatch_and_persistence():
    print("test_hatch_and_persistence")
    h = Harness()
    try:
        sw = h.swarm(max_entities=15)
        sw.sync()
        fly = next(e for e in sw.entities if e.kind == "fly")
        sw.swat(fly)
        h.step(sw, 1.0)
        egg = next(e for e in sw.entities if e.kind == "egg")
        _check(egg.hatch_at and egg.hatch_at > time.time(), "卵带着孵化时刻")

        # 把孵化时间拨到过去 -> 自动破壳
        egg.hatch_at = time.time() - 1
        h.step(sw, 1.0)
        _check(sw.stats()["flies"] >= 1, "到点后原地孵出苍蝇")

        # 持久化: 关掉再开, 卵要还在 (且倒计时接着算)
        sw2 = h.swarm(max_entities=15)
        sw2.entities.clear()
        sw2.sync()
        fly2 = next(e for e in sw2.entities if e.kind == "fly")
        sw2.swat(fly2)
        h.step(sw2, 1.0)
        sw2._save_state()
        state = os.path.join(h.dir, "flies.json")
        _check(os.path.exists(state), "卵写进了 flies.json")
        sw2.shutdown()
        h.sw = None

        import flies
        sw3 = flies.FlySwarm(h.root, h.prefs, lambda v: int(v), h.dir, h.due)
        _check(any(e.kind == "egg" for e in sw3.entities), "重开后卵还在")
        sw3.shutdown()
        h.sw = None
    finally:
        h.close()


def test_swatter_toggle():
    print("test_swatter_toggle")
    h = Harness()
    try:
        sw = h.swarm(max_entities=3, swatter=False)
        sw.start()
        h.step(sw, 0.2)
        _check(all(not e.window.clickable for e in sw.entities), "纯装饰模式苍蝇不吃点击")
        h.prefs.set("swatter", True, save=False)
        h.step(sw, 0.2)
        _check(all(e.window.clickable for e in sw.entities if e.kind == "fly"),
               "开苍蝇拍后苍蝇可点击")
        _check(sw.swatter is not None and sw.swatter.enabled, "苍蝇拍窗口已就绪")
        h.prefs.set("swatter", False, save=False)
        h.step(sw, 0.2)
        _check(all(not e.window.clickable for e in sw.entities if e.kind == "fly"),
               "关掉苍蝇拍后又变成穿透")
    finally:
        h.close()


def test_flee_from_cursor():
    print("test_flee_from_cursor")
    h = Harness()
    try:
        sw = h.swarm(max_entities=2)
        sw.sync()
        h.step(sw, 2.0)
        fly = next(e for e in sw.entities if e.kind == "fly")
        fly.state = "crawl"
        fly.state_t = 0.0
        fly.target = None
        fly.speed = 0.0
        mx, my = fly.x, fly.y                      # 鼠标就压在苍蝇身上
        for _ in range(12):                        # 0.4s: 反应延迟之后正在逃
            sw._step_entity(fly, 1.0 / 30.0, mx, my, time.perf_counter())
        d = ((fly.x - mx) ** 2 + (fly.y - my) ** 2) ** 0.5
        _check(fly.state == "flee", "鼠标贴脸 -> 进入逃跑状态")
        _check(d > 5.0, "逃跑时确实在远离 (%0.1f px)" % d)
        # 鼠标走开 -> 回到爬行
        for _ in range(40):
            sw._step_entity(fly, 1.0 / 30.0, mx + 3000, my + 3000, time.perf_counter())
        _check(fly.state in ("crawl", "rest"), "鼠标走开后恢复爬行")
    finally:
        h.close()


if __name__ == "__main__":
    for _fn in (test_due_keys_matches_count, test_invariant_and_actions,
                test_task_completion_removes_entity, test_killed_egg_dies_when_task_done,
                test_hatch_and_persistence, test_swatter_toggle, test_flee_from_cursor):
        _fn()
    print("\n全部苍蝇测试通过")
