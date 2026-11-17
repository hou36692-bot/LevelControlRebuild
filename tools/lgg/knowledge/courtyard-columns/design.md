# 多圈庭院与廊柱

状态：待用户审核；未运行游戏验收。

将柱础、柱身、柱头归为一根逻辑廊柱，计数与渲染分件分开。

内外圈来自设计关系，左右顺序来自查询时玩家摄像机。

白盒与参数均为自建。OBJ 是静态交流产物；可编辑运行时实体以 plan.json 经 LGG 编译提交为准。

## 操作示例

先用 lc.context 获取当前项目；lgg_build 使用本目录 plan.json。查询采用 case.json 中的 query；within 可填本次构建返回的场景根 ID。

获得 selection_id 与 expected_version 后，可移动 100 cm：`apply move selected --delta 0,0,100cm`。再次提交原设计应保留该实例覆盖；使用原回执撤销。

## 参考

- [Vitruvius, Ten Books on Architecture, Book III; Morgan translation (1914)](https://www.gutenberg.org/files/20239/20239-h/20239-h.htm)：参考柱列、柱间距与柱础/柱头的分件组织；自写说明、自建几何。Project Gutenberg 标注美国公共领域，其他地区使用前核对当地条件。
- [U.S. Access Board: Chapter 4, Accessible Routes](https://www.access-board.gov/ada/chapter/ch04/)：仅引用通行、门洞与路线连续性原则；未复制图纸或全文。游戏尺度自行设定，不构成规范符合性证明。
