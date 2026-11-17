# U 形围合与开放入口

状态：待用户审核；未运行游戏验收。

三面围合，南侧保留明确入口，避免封死场地。

把入口、场地与围墙作为不同设计角色，修改围墙不重命名场地。

白盒与参数均为自建。OBJ 是静态交流产物；可编辑运行时实体以 plan.json 经 LGG 编译提交为准。

## 操作示例

先用 lc.context 获取当前项目；lgg_build 使用本目录 plan.json。查询采用 case.json 中的 query；within 可填本次构建返回的场景根 ID。

获得 selection_id 与 expected_version 后，可移动 100 cm：`apply move selected --delta 0,0,100cm`。再次提交原设计应保留该实例覆盖；使用原回执撤销。

## 参考

- [U.S. Access Board: Chapter 4, Accessible Routes](https://www.access-board.gov/ada/chapter/ch04/)：仅引用通行、门洞与路线连续性原则；未复制图纸或全文。游戏尺度自行设定，不构成规范符合性证明。
