"""prefs.py 的无 GUI 逻辑测试。直接 `python test_prefs.py` 运行
(与 test_models.py 同一套风格: 无第三方依赖, 不碰 Win32)。
"""

import json
import os
import tempfile

from prefs import (DEFAULTS, Prefs, coerce, hours_ok, join_hatch,
                   minutes_ok, split_hatch)


def _check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok - {msg}")


def _tmp_path():
    return os.path.join(tempfile.mkdtemp(), "prefs.json")


def test_defaults_when_missing():
    print("test_defaults_when_missing")
    p = Prefs(_tmp_path())
    _check(p.get("enabled") is True, "默认启用苍蝇")
    _check(p.get("layer") == "desktop", "默认只在桌面级")
    _check(p.get("max_entities") == 15, "默认上限 15 个实体(苍蝇+卵)")
    _check(p.get("hatch_seconds") == 3600, "默认孵化 1 小时")
    _check(p.get("swatter") is False, "默认不开苍蝇拍")


def test_clamp_and_types():
    print("test_clamp_and_types")
    p = Prefs(None, load=False)
    p.set("max_entities", 999, save=False)
    _check(p.get("max_entities") == 30, "上限被钳到 30")
    p.set("max_entities", 0, save=False)
    _check(p.get("max_entities") == 1, "下限被钳到 1")
    p.set("fly_scale", 0.1, save=False)
    _check(abs(p.get("fly_scale") - 0.6) < 1e-9, "尺寸下限 0.6")
    p.set("speed_crawl", "abc", save=False)
    _check(abs(p.get("speed_crawl") - 60.0) < 1e-9, "非法数字回退默认")
    p.set("speed_crawl", float("nan"), save=False)
    _check(abs(p.get("speed_crawl") - 60.0) < 1e-9, "NaN 回退默认")
    p.set("layer", "往天上飘", save=False)
    _check(p.get("layer") == "desktop", "非法枚举回退默认")
    p.set("enabled", "false", save=False)
    _check(p.get("enabled") is False, "字符串 false 能转成 False")
    p.set("max_entities", "12", save=False)
    _check(p.get("max_entities") == 12 and isinstance(p.get("max_entities"), int),
           "数字字符串转成 int")
    _check(coerce("nonexistent", 1) == 1, "未知键原样返回")


def test_rest_seconds_is_free_input():
    print("test_rest_seconds_is_free_input")
    p = Prefs(None, load=False)
    p.set("rest_seconds", 3.5, save=False)
    _check(abs(p.get("rest_seconds") - 3.5) < 1e-9, "静止时间接受小数, 输入多少是多少")
    p.set("rest_seconds", 120, save=False)
    _check(abs(p.get("rest_seconds") - 120.0) < 1e-9, "静止时间没有上限")
    p.set("rest_seconds", -5, save=False)
    _check(abs(p.get("rest_seconds") - 0.0) < 1e-9, "静止时间负数钳到 0")


def test_hatch_hms():
    print("test_hatch_hms")
    _check(split_hatch(3600) == (1, 0), "3600 秒 = 1 小时 0 分")
    _check(split_hatch(5400) == (1, 30), "5400 秒 = 1 小时 30 分")
    _check(split_hatch(0) == (0, 0), "0 秒 = 立即孵化")
    _check(join_hatch(1, 30) == 5400, "(1,30) -> 5400 秒")
    _check(join_hatch(-1, 99) == 59 * 60, "越界输入被截断 (小时 0, 分 59)")
    _check(minutes_ok(0) and minutes_ok(59), "分钟 0 与 59 合法")
    _check(not minutes_ok(60), "分钟 60 非法 (你要求的 min < 60)")
    _check(not minutes_ok(-1), "分钟 -1 非法")
    _check(not minutes_ok(30.5), "分钟必须是整数")
    _check(not minutes_ok(""), "空字符串非法")
    _check(hours_ok(0) and hours_ok(24), "小时 0 与 24 合法")
    _check(not hours_ok(-1), "小时负数非法")


def test_roundtrip_and_extra_fields():
    print("test_roundtrip_and_extra_fields")
    path = _tmp_path()
    p = Prefs(path)
    p.set("speed_fly", 880, save=False)
    p.set("swatter", True, save=False)
    p.set("future_field", {"a": 1}, save=False)      # 未识别字段: 原样保留
    p.flush()

    q = Prefs(path)
    _check(abs(q.get("speed_fly") - 880.0) < 1e-9, "速度改动能落盘并读回")
    _check(q.get("swatter") is True, "苍蝇拍开关能落盘并读回")
    _check(q.get("future_field") == {"a": 1}, "未识别字段原样保留 (降级不丢配置)")
    _check(q.get("max_entities") == 15, "没写过的字段仍是默认值")


def test_corrupt_file():
    print("test_corrupt_file")
    path = _tmp_path()
    with open(path, "w", encoding="utf-8") as f:
        f.write("{ this is not json ")
    p = Prefs(path)
    _check(p.get("max_entities") == 15, "坏 JSON 回退默认值, 不抛异常")

    path2 = _tmp_path()
    with open(path2, "w", encoding="utf-8") as f:
        json.dump([1, 2, 3], f)
    p2 = Prefs(path2)
    _check(p2.get("layer") == "desktop", "顶层不是 dict 也回退默认值")


def test_subscribe_and_change_detection():
    print("test_subscribe_and_change_detection")
    p = Prefs(None, load=False)
    seen = []
    off = p.subscribe(seen.append)
    _check(p.set("swatter", True, save=False) is True, "第一次改动返回 True")
    _check(p.set("swatter", True, save=False) is False, "重复写同一个值返回 False")
    _check(seen == ["swatter"], "只有真正变化时才广播")
    off()
    p.set("swatter", False, save=False)
    _check(seen == ["swatter"], "退订后不再收到广播")

    def boom(_key):
        raise RuntimeError("订阅者炸了")
    p.subscribe(boom)
    p.set("enabled", False, save=False)
    _check(p.get("enabled") is False, "订阅者抛异常不影响写入")


def test_reset():
    print("test_reset")
    p = Prefs(None, load=False)
    p.set("max_entities", 3, save=False)
    p.set("speed_fly", 200, save=False)
    p.reset()
    _check(p.get("max_entities") == 15 and abs(p.get("speed_fly") - 600.0) < 1e-9,
           "恢复默认把所有项还原")


if __name__ == "__main__":
    for _fn in (test_defaults_when_missing, test_clamp_and_types,
                test_rest_seconds_is_free_input, test_hatch_hms,
                test_roundtrip_and_extra_fields, test_corrupt_file,
                test_subscribe_and_change_detection, test_reset):
        _fn()
    print("\n全部 prefs 测试通过 (DEFAULTS=%d 项)" % len(DEFAULTS))
