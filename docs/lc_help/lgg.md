# LGG：分阶段白盒设计

W4：`lgg_run play --music architect-pulse --duration 12` 使用项目原创配乐，并按分析得到的节拍落位；`--music none` 不播放配乐。工具立即返回，施工在游戏继续。改变输出设备后须复核音画补偿。

`lgg_card init --scene courtyard` 初始化设计卡并返回总则及玩家信息；先阅读返回的总则。`lgg_card status` 返回当前草稿、模块、编号和提交请求。初始化不删除正式实体。

草稿参数只有一个单行 command。JSON 放在单引号中，内部使用双引号。数字距离统一为米。site 的 center_m 是场地中心，其他模块 center_m 是世界坐标中心。

优先使用紧凑参数，避免长 JSON 数组拼写错误。以下参数中的坐标和尺寸数字均为米，不加单位后缀：

```text
lgg_draft #1 site --center 18,0,4 --size 24,20,8
lgg_draft #2 coarse --module 1 ground floor 18,0,0.1 24,20,0.2 --module 2 enclosure wall 18,0,2.2 24,20,4 --module 3 fountain fountain 18,0,1.4 5,5,2.4
lgg_draft #3 module --id 1
lgg_draft #4 module --id 2
lgg_draft #5 module --id 3 --complete
lgg_draft #6 module --id 3 --size 3,3,2.4 --complete
```

`--module` 后依次是 id、name、kind、center_m、size_m。高级参数仍用下面的 JSON 形式。

`status` 中 commit_version_current 表示提交之后世界版本是否变化；false 时不能用旧回执声称当前世界仍等于设计。undo 或 restore 后再次 play 会重新调和当前世界，而不是重复返回已经过期的完成状态。

```text
lgg_draft #1 site --json '{"center_m":[18,0,4],"size_m":[24,20,8]}'
lgg_draft #2 coarse --json '[{"id":1,"name":"ground","kind":"floor","center_m":[18,0,0.1],"size_m":[24,20,0.2]},{"id":2,"name":"enclosure","kind":"wall","center_m":[18,0,2],"size_m":[24,20,4]},{"id":3,"name":"fountain","kind":"fountain","center_m":[18,0,1.2],"size_m":[5,5,2.4]}]'
lgg_draft #3 module --id 1 --json '{}'
lgg_draft #4 module --id 2 --json '{"params":{"sides":["north","east","west"]}}'
lgg_draft #5 module --id 3 --json '{}' --complete
lgg_run play
lgg_run status
lgg_run cancel
```

`#N` 必须紧接上次编号；同编号、同内容重放不重复发布。`--complete` 仅在所有 coarse 模块都已细化时通过。修改模块使用下一个编号，例如 `#6 module --id 3 --json '{"size_m":[3,3,1.8]}' --complete`，然后再次 `lgg_run play`。新草稿丢弃旧预览并重演，尚未执行的旧提交会取消；已提交世界只在新正式提交时变更。不要为修改尺寸重新 init 或改 ID。

模块字段：id（1..99）、area（默认 1，1..99）、name（kebab-case）、kind、center_m、size_m、yaw_deg（默认 0）、params。coarse 只画框，module 才编译几何；module patch 可以改 center_m、size_m、yaw_deg、params，不改身份、构造器类型。site 内必须容纳模块的旋转包围盒。

构造器参数：wall 的 sides（north/east/south/west，默认 north/east/west）、thickness_m（默认 0.25）、segment_m（默认 2）；floor 的 tile_m（默认 2）；colonnade 的 count（默认 6）、diameter_m（默认 0.5）；doorway 的 opening_m（默认 2）、opening_height_m（默认 2.4）；stairs 的 steps（默认 8）；fountain 的 segments（默认 16）、rim_m（默认 0.3）。无水模拟，喷泉由底盘、围沿、立柱和上碗表达。未知字段或超出范围参数拒绝。

`lgg_run play --duration 12` 指定预览重演秒数（1..110）；默认 8 s。工具立即返回 scheduled，不等待施工，用户可以继续输入。用 status 查看最终回执和演出状态，只有 commit.status=applied 才能说正式完成。取消和把镜头还给玩家也可分别用 `lc.edit presentation cancel`、`lc.edit presentation camera-player`。
