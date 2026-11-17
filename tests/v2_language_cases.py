"""Real-language acceptance cases with world-state and scope assertions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from lc.command import parse


@dataclass
class Case:
    domain: str
    prompt: str
    check: Callable[[dict, dict, dict], bool]
    scope: str = ""


def near(actual, expected) -> bool:
    if isinstance(expected, (list, tuple)):
        if not isinstance(actual, (list, tuple)):
            return False
        return len(actual) == len(expected) and all(near(a, b) for a, b in zip(actual, expected))
    return abs(actual - expected) <= 0.001


def env(values: dict, preview: bool = False):
    def check(before, after, turn):
        state = after["environment"]
        return state["preview_active"] == preview and all(
            near(state["state"][key], value) for key, value in values.items()
        )

    return check


def surface(group: str, values: dict):
    def check(before, after, turn):
        selected = [item for item in after["entities"].values() if item["group"] == group]
        return bool(selected) and all(
            all(
                item.get("surface", {}).get(key) == value
                if isinstance(value, str)
                else near(item.get("surface", {}).get(key, -1000), value)
                for key, value in values.items()
            )
            for item in selected
        )

    return check


def used(turn: dict, tool: str, prefix: str) -> bool:
    return any(
        call["tool"] == tool and call["arguments"].get("command", "").startswith(prefix)
        for call in turn.get("tools", [])
    )


def cross_cases() -> list[Case]:
    return [
        Case("cross", "把庭院灯光改成清晨方案，直接提交。", env({"time_h": 7}), "environment"),
        Case(
            "cross",
            "云量设成0.7，其他灯光参数保持不变。",
            env({"cloud_coverage": 0.7}),
            "environment",
        ),
        Case(
            "cross",
            "太阳高度设30度、方位120度，曝光设为1 EV。",
            env({"sun_elevation_deg": 30, "sun_azimuth_deg": 120, "exposure_ev": 1}),
            "environment",
        ),
        Case(
            "cross",
            "预览夜晚灯光，不要提交。",
            lambda b, a, t: (
                a["environment"]["preview_active"]
                and a["environment"]["committed"] == b["environment"]["committed"]
            ),
            "environment",
        ),
        Case(
            "cross",
            "取消刚才的灯光预览，恢复正式灯光。",
            lambda b, a, t: (
                not a["environment"]["preview_active"]
                and a["environment"]["state"] == a["environment"]["committed"]
            ),
            "environment",
        ),
        Case(
            "cross",
            "把reference-wall组的所有围墙换成石灰石材质，其他实体不动。",
            surface("reference-wall", {"preset": "stone-limestone"}),
            "group:reference-wall",
        ),
        Case(
            "cross",
            "围墙reference-wall组粗糙度设0.4、金属度设0。",
            surface("reference-wall", {"roughness": 0.4, "metallic": 0}),
            "group:reference-wall",
        ),
        Case(
            "cross",
            "地面reference-floor组的色调改为线性RGB 0.6,0.5,0.4。",
            surface("reference-floor", {"tint": [0.6, 0.5, 0.4]}),
            "group:reference-floor",
        ),
        Case(
            "cross",
            "给reference-path组预览黄铜材质，不要提交。",
            lambda b, a, t: a["presentation"]["design_preview_active"],
        ),
        Case(
            "cross",
            "取消刚才的材质预览。",
            lambda b, a, t: not a["presentation"]["design_preview_active"],
        ),
        Case(
            "cross",
            "在2200,-600,100厘米处放一张长椅，ID为review-bench，吸附到地面并提交。",
            lambda b, a, t: a["entities"].get("review-bench", {}).get("template") == "prop/bench",
            "prefix:review-bench",
        ),
        Case(
            "cross",
            "把review-bench的偏航角设为90度，俯仰和滚转为0。",
            lambda b, a, t: near(
                a["entities"]["review-bench"]["transform"]["rotation_deg"], [0, 90, 0]
            ),
            "prefix:review-bench",
        ),
        Case(
            "cross",
            "把review-bench沿Y方向每隔2米复制一张，新增3张，ID前缀review-copies。",
            lambda b, a, t: sum(k.startswith("review-copies-") for k in a["entities"]) == 3,
            "prefix:review-copies-",
        ),
        Case(
            "cross",
            "将review-copies开头的三张长椅全部沿X方向移动1米。",
            lambda b, a, t: (
                sum(k.startswith("review-copies-") for k in a["entities"]) == 3
                and all(
                    near(v["transform"]["position_cm"][0], 2300)
                    for k, v in a["entities"].items()
                    if k.startswith("review-copies-")
                )
            ),
            "prefix:review-copies-",
        ),
        Case(
            "cross",
            "在1400,-600,0厘米处放置rest-stop休息站预制体，ID为review-rest，落地并提交。",
            lambda b, a, t: sum(k.startswith("review-rest-") for k in a["entities"]) == 3,
            "prefix:review-rest-",
        ),
        Case(
            "cross",
            "把review-rest-part-00替换为stool凳子，使用凳子默认尺寸，其他组件不动。",
            lambda b, a, t: (
                a["entities"].get("review-rest-part-00", {}).get("template") == "prop/stool"
            ),
            "id:review-rest-part-00",
        ),
        Case(
            "cross",
            "检查从1200,0,150厘米到2000,0,150厘米的视线是否被挡，报告首个命中。",
            lambda b, a, t: any(
                call["tool"] == "lc.read"
                and call["arguments"].get("command", "").startswith("sightline")
                and parse("read", call["arguments"]["command"]).data.get("from_cm")
                == [1200, 0, 150]
                and parse("read", call["arguments"]["command"]).data.get("to_cm") == [2000, 0, 150]
                for call in t.get("tools", [])
            ),
        ),
        Case(
            "cross",
            "给庭院拍四机位截图，中心20米,0,2米，半径18米，相对高度8米。",
            lambda b, a, t: any(len(r.get("captures", [])) == 4 for r in t.get("terminal", [])),
        ),
        Case(
            "cross",
            "采样120帧，保存当前性能报告。",
            lambda b, a, t: any(
                r.get("report", {}).get("samples") == 120 for r in t.get("terminal", [])
            ),
        ),
        Case(
            "cross",
            "撤销最后一次世界编辑，把刚才替换成凳子的那个组件恢复成长椅。",
            lambda b, a, t: (
                a["entities"].get("review-rest-part-00", {}).get("template") == "prop/bench"
            ),
            "id:review-rest-part-00",
        ),
    ]


def lighting_cases() -> list[Case]:
    cases = [
        Case("lighting", f"应用{label}灯光方案并提交。", env({"time_h": hour}), "environment")
        for label, hour in [
            ("清晨", 7),
            ("正午", 12),
            ("黄金时刻", 17.5),
            ("黄昏", 18.7),
            ("夜晚", 23),
            ("阴天", 12),
        ]
    ]
    cases += [
        Case("lighting", prompt, env(values), "environment")
        for prompt, values in [
            ("时间调到上午9点，提交。", {"time_h": 9}),
            ("雾密度设为0.04。", {"fog_density": 0.04}),
            ("天光强度设为1.1。", {"sky_intensity": 1.1}),
            ("太阳方位45度，高度35度。", {"sun_azimuth_deg": 45, "sun_elevation_deg": 35}),
            ("太阳强度7.5，太阳色温5100 K。", {"sun_intensity": 7.5, "sun_temperature_k": 5100}),
            ("曝光补偿设为0.5 EV。", {"exposure_ev": 0.5}),
            ("整体饱和度0.8、对比度1.1。", {"saturation": 0.8, "contrast": 1.1}),
            ("后处理白平衡色温设为5600 K。", {"temperature_k": 5600}),
            ("雾高度3米，雾高度衰减0.15。", {"fog_height_cm": 300, "fog_falloff": 0.15}),
            ("雾颜色设为线性RGB 0.2,0.3,0.4。", {"fog_color": [0.2, 0.3, 0.4]}),
            ("云量改成0.65。", {"cloud_coverage": 0.65}),
        ]
    ]
    cases += [
        Case(
            "lighting",
            "将当前正式灯光另存为validation-evening方案。",
            lambda b, a, t: "validation-evening" in a["environment"]["presets"],
            "environment",
        ),
        Case(
            "lighting",
            "从凌晨3点开始预览时间流逝，速度为每秒0.1小时，不提交。",
            lambda b, a, t: (
                a["environment"]["preview_active"]
                and a["environment"]["committed"] == b["environment"]["committed"]
            ),
            "environment",
        ),
        Case(
            "lighting",
            "取消时间流逝预览，恢复正式灯光。",
            lambda b, a, t: (
                not a["environment"]["preview_active"]
                and a["environment"]["state"] == a["environment"]["committed"]
            ),
            "environment",
        ),
    ]
    assert len(cases) == 20
    return cases


def scope_valid(case: Case, before: dict, after: dict) -> bool:
    if case.scope != "environment" and (
        before["environment"]["committed"] != after["environment"]["committed"]
        or before["environment"]["presets"] != after["environment"]["presets"]
    ):
        return False
    for identity in before["entities"].keys() | after["entities"].keys():
        old, new = before["entities"].get(identity), after["entities"].get(identity)
        if old == new:
            continue
        allowed = (
            (case.scope.startswith("prefix:") and identity.startswith(case.scope[7:]))
            or (case.scope.startswith("id:") and identity == case.scope[3:])
            or (case.scope.startswith("group:") and (old or new).get("group") == case.scope[6:])
        )
        if not allowed:
            return False
    return True
