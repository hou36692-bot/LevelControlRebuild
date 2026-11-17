# 建筑扩建与实例修改

状态：待用户审核；未运行游戏验收。

扩建分配新节点键，原入口、墙体和平台身份保持不变。

移动或删除单件后重放设计应保留覆盖；同字段冲突必须显式处理。

白盒与参数均为自建。OBJ 是静态交流产物；可编辑运行时实体以 plan.json 经 LGG 编译提交为准。

## 操作示例

先用 lc.context 获取当前项目；lgg_build 使用本目录 plan.json。查询采用 case.json 中的 query；within 可填本次构建返回的场景根 ID。

本例先提交 plan.json，再读取 extension.json，填入第一次回执的 scene 和最新 expected_version 后扩建；最后查询 east-wing。

获得 selection_id 与 expected_version 后，可移动 100 cm：`apply move selected --delta 0,0,100cm`。再次提交原设计应保留该实例覆盖；使用原回执撤销。

## 参考

- [U.S. Access Board: Chapter 4, Accessible Routes](https://www.access-board.gov/ada/chapter/ch04/)：仅引用通行、门洞与路线连续性原则；未复制图纸或全文。游戏尺度自行设定，不构成规范符合性证明。
