"""Author and compile the shipped design corpus; never connect to a game."""

from __future__ import annotations

import json
import math
from copy import deepcopy
from pathlib import Path

from lgg.build import compile_plan

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tools/lgg/knowledge"
SOURCES = {
    "routes": {"title": "U.S. Access Board: Chapter 4, Accessible Routes",
               "url": "https://www.access-board.gov/ada/chapter/ch04/",
               "use": "仅引用通行、门洞与路线连续性原则；未复制图纸或全文。游戏尺度自行设定，不构成规范符合性证明。"},
    "stairs": {"title": "U.S. Access Board: Chapter 5, General Site and Building Elements",
               "url": "https://www.access-board.gov/ada/chapter/ch05/",
               "use": "仅引用楼梯与高差衔接主题；以实际玩家移动参数验收。未复制原文或图纸。"},
    "vitruvius": {"title": "Vitruvius, Ten Books on Architecture, Book III; Morgan translation (1914)",
                  "url": "https://www.gutenberg.org/files/20239/20239-h/20239-h.htm",
                  "use": "参考柱列、柱间距与柱础/柱头的分件组织；自写说明、自建几何。Project Gutenberg 标注美国公共领域，其他地区使用前核对当地条件。"},
}


def box(key, at, size, role, **semantic):
    return {"id": key, "key": key, "asset": "blockout/box", "at": at, "size": size,
            "semantic": {"name": key, "role": role, **semantic}}


def floor(key, at, size):
    return box(key, at, size, "floor", area="main")


def plans():
    column = {"parts": [
        {"id": "base", "asset": "blockout/box", "at": [0, 0, .15], "size": [.8, .8, .3], "semantic": {"role": "column-base"}},
        {"id": "shaft", "asset": "blockout/column", "at": [0, 0, 2], "size": [.5, .5, 3.4], "semantic": {"role": "column-shaft"}},
        {"id": "capital", "asset": "blockout/box", "at": [0, 0, 3.85], "size": [.8, .8, .3], "semantic": {"role": "column-capital"}}]}
    rows = [floor("courtyard-floor", [0, 0, -.1], [20, 16, .2])]
    for ring, y in [("outer", -6), ("inner", -3)]:
        rows.append({"id": ring + "-columns", "key": ring + "-columns", "use": "column",
                     "at": [-7.5, y, 0], "repeat": {"count": 6, "step": [3, 0, 0],
                     "members": ["column-" + letter for letter in "abcdef"]},
                     "semantic": {"name": "外圈廊柱" if ring == "outer" else "内圈廊柱",
                                  "role": "column", "area": "courtyard", "pattern": ring + "-front",
                                  "ring": ring, "side": "front"}})
    yield "courtyard-columns", "多圈庭院与廊柱", ["courtyard", "column", "identity"], {
        "definitions": {"column": column}, "nodes": rows}, ["vitruvius", "routes"], [
        "将柱础、柱身、柱头归为一根逻辑廊柱，计数与渲染分件分开。",
        "内外圈来自设计关系，左右顺序来自查询时玩家摄像机。"], {
        "role": "column", "ring": "outer", "side": "front", "order": "view_left", "ordinal": 3}
    hall = [floor("hall-floor", [0, 0, -.1], [6, 12, .2]),
            box("west-wall", [-3, 0, 1.5], [.2, 12, 3], "wall"),
            box("east-wall", [3, 0, 1.5], [.2, 12, 3], "wall"),
            {"id": "entrance", "key": "entrance", "kind": "doorway", "at": [0, -4, 2],
             "size": [6, .4, 4], "params": {"opening_m": 2, "opening_height_m": 2.6},
             "semantic": {"role": "entrance", "aliases": ["入口门洞"], "area": "hall",
                          "relations": [{"kind": "opens_to", "target": "key:hall-floor"}]}}]
    yield "hall-corridor", "门厅、门洞与连接走廊", ["hall", "entrance", "doorway", "corridor", "clearance"], {
        "nodes": hall}, ["routes"], ["用明确门洞和连续地面表达入口，不用墙体包围盒代替通路。",
        "净空通过玩家胶囊检查；设计声明不能替代运行时碰撞结果。"], {"role": "entrance"}
    yield "stairs-landings", "平台、楼梯与高差衔接", ["stairs", "elevation", "route"], {"nodes": [
        floor("lower-platform", [-4, 0, -.1], [4, 3, .2]),
        {"id": "stair-flight", "key": "stair-flight", "kind": "stairs", "at": [0, 0, 1],
         "size": [4, 3, 2], "params": {"steps": 10}, "semantic": {"role": "stairs", "area": "transition",
         "relations": [{"kind": "connects_to", "target": "key:lower-platform"}, {"kind": "connects_to", "target": "key:upper-platform"}]}},
        floor("upper-platform", [4, 0, 1.9], [4, 3, .2])]}, ["stairs", "routes"], [
        "上下平台和楼梯是独立空间节点，路径关系必须说明高差。",
        "本例台阶高 0.2 m、踏面 0.4 m，为自定游戏尺度，仍需玩家参数验收。"], {"role": "step"}
    yield ("u-courtyard", "U 形围合与开放入口", ["enclosure", "courtyard", "entrance", "route"], {"nodes": [
        floor("court-ground", [0, 0, -.1], [16, 14, .2]),
        {"id": "u-walls", "key": "u-walls", "kind": "wall", "at": [0, 0, 2], "size": [16, 14, 4],
         "params": {"sides": ["north", "east", "west"]}, "semantic": {"role": "enclosure", "area": "courtyard"}}]},
        ["routes"], ["三面围合，南侧保留明确入口，避免封死场地。",
                     "把入口、场地与围墙作为不同设计角色，修改围墙不重命名场地。"], {"role": "enclosure"})
    loop = []
    for side, at, size in [("north", [0, 7, -.1], [18, 4, .2]), ("south", [0, -7, -.1], [18, 4, .2]),
                           ("east", [7, 0, -.1], [4, 10, .2]), ("west", [-7, 0, -.1], [4, 10, .2])]:
        loop.append(box(side + "-walk", at, size, "corridor", area="cloister", side=side, ring="outer", pattern="walk-loop"))
    yield "cloister-loop", "环廊与多入口连接", ["cloister", "loop", "entrance", "route"], {"nodes": loop}, ["routes", "vitruvius"], [
        "四段环廊共同表达回路；连接声明与碰撞可达性分别记录。",
        "跨侧排序会产生歧义，先用 side 或区域缩小查询。"], {"role": "corridor", "side": "west"}
    yield "extension-overrides", "建筑扩建与实例修改", ["extension", "override", "identity", "corridor"], {"nodes": hall + [
        floor("east-wing", [7, 2, -.1], [8, 4, .2])]}, ["routes"], [
        "扩建分配新节点键，原入口、墙体和平台身份保持不变。",
        "移动或删除单件后重放设计应保留覆盖；同字段冲突必须显式处理。"], {"name": "east-wing"}


def obj(entities):
    lines = ["# Original LevelControl whitebox; meters, Z-up"]
    offset = 1
    for entity in entities:
        t = entity["transform"]; cx, cy, cz = [v / 100 for v in t["position_cm"]]
        sx, sy, sz = [v / 200 for v in t["size_cm"]]; angle = math.radians(t["rotation_deg"][1])
        lines.append("o " + entity["id"])
        if entity["template"] == "blockout/column":
            vertices = [(math.cos(i * math.tau / 12) * sx, math.sin(i * math.tau / 12) * sy, z) for z in [-sz, sz] for i in range(12)]
            faces = [list(reversed(range(12))), list(range(12, 24))] + [[i, (i + 1) % 12, (i + 1) % 12 + 12, i + 12] for i in range(12)]
        else:
            vertices = [(x, y, z) for z in [-sz, sz] for y in [-sy, sy] for x in [-sx, sx]]
            faces = [[0, 2, 3, 1], [4, 5, 7, 6], [0, 1, 5, 4], [2, 6, 7, 3], [0, 4, 6, 2], [1, 3, 7, 5]]
        for x, y, z in vertices:
            lines.append(f"v {cx + x*math.cos(angle)-y*math.sin(angle):.6f} {cy+x*math.sin(angle)+y*math.cos(angle):.6f} {cz+z:.6f}")
        lines.extend("f " + " ".join(str(offset + v) for v in face) for face in faces)
        offset += len(vertices)
    return "\n".join(lines) + "\n"


def main():
    templates = [{"id": "blockout/" + name, "size_cm": [100, 100, 100]} for name in ["box", "column", "floor", "wall"]]
    for identity, title, tags, content, sources, principles, query in plans():
        folder = OUT / identity; folder.mkdir(parents=True, exist_ok=True)
        plan = {"name": identity, "intent": "new_structure", "anchor": {"front_m": 3}, **content,
                "constraints": [{"kind": "walk_connectivity", "status": "declared"}]}
        extension = None
        if identity == "extension-overrides":
            extension = deepcopy(plan)
            extension["intent"] = "extension"
            plan["nodes"] = [node for node in plan["nodes"] if node["id"] != "east-wing"]
            (folder / "extension.json").write_text(json.dumps(extension, ensure_ascii=False, indent=2) + "\n", "utf-8")
        slots, semantics = {}, {}
        entities, anchor = compile_plan(plan, identity, {"props": [], "prefabs": []}, templates, slots,
                                        {"feet_cm": [0, 0, 0], "forward": [0, 1, 0]}, semantic_out=semantics)
        case = {"id": identity, "title": title, "review_status": "pending_user_review", "tags": tags,
                "principles": principles, "sources": [SOURCES[key] for key in sources], "query": query,
                "geometry_license": "CC0-1.0", "documentation": "Original LevelControl case notes; source texts are not redistributed.",
                "runtime_validation": "not_run", "compiled_entity_count": len(entities)}
        for filename, data in [("case.json", case), ("plan.json", plan),
                                ("whitebox.json", {"entities": entities, "semantic_nodes": semantics["nodes"], "anchor": anchor}),
                                ("identity-map.json", {"slots": slots, "nodes": semantics["nodes"],
                                                       "entities": {e["id"]: e["semantic"] for e in entities}})]:
            (folder / filename).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", "utf-8")
        (folder / "whitebox.obj").write_text(obj(entities), "utf-8")
        if extension:
            expanded, _ = compile_plan(extension, identity, {"props": [], "prefabs": []}, templates, slots,
                                       {"feet_cm": [0, 0, 0], "forward": [0, 1, 0]}, anchor)
            (folder / "extension-whitebox.obj").write_text(obj(expanded), "utf-8")
        (folder / "design.md").write_text(f"# {title}\n\n状态：待用户审核；未运行游戏验收。\n\n" +
            "\n\n".join(principles) + "\n\n白盒与参数均为自建。OBJ 是静态交流产物；可编辑运行时实体以 plan.json 经 LGG 编译提交为准。\n\n" +
            "## 操作示例\n\n先用 lc.context 获取当前项目；lgg_build 使用本目录 plan.json。查询采用 case.json 中的 query；within 可填本次构建返回的场景根 ID。\n\n" +
            ("本例先提交 plan.json，再读取 extension.json，填入第一次回执的 scene 和最新 expected_version 后扩建；最后查询 east-wing。\n\n" if extension else "") +
            "获得 selection_id 与 expected_version 后，可移动 100 cm：`apply move selected --delta 0,0,100cm`。再次提交原设计应保留该实例覆盖；使用原回执撤销。\n\n" +
            "## 参考\n\n" + "\n".join(f"- [{SOURCES[key]['title']}]({SOURCES[key]['url']})：{SOURCES[key]['use']}" for key in sources) + "\n", "utf-8")


if __name__ == "__main__":
    main()
