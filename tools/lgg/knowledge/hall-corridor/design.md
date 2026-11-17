# 门厅、门洞与连接走廊

状态：待用户审核；未运行游戏验收。

用明确门洞和连续地面表达入口，不用墙体包围盒代替通路。

净空通过玩家胶囊检查；设计声明不能替代运行时碰撞结果。

白盒与参数均为自建。OBJ 是静态交流产物；可编辑运行时实体以 plan.json 经 LGG 编译提交为准。

## 操作示例

先用 lc.context 获取当前项目；lgg_build 使用本目录 plan.json。查询采用 case.json 中的 query；within 可填本次构建返回的场景根 ID。

获得 selection_id 与 expected_version 后，可移动 100 cm：`apply move selected --delta 0,0,100cm`。再次提交原设计应保留该实例覆盖；使用原回执撤销。

## 参考

- [U.S. Access Board: Chapter 4, Accessible Routes](https://www.access-board.gov/ada/chapter/ch04/)：仅引用通行、门洞与路线连续性原则；未复制图纸或全文。游戏尺度自行设定，不构成规范符合性证明。
