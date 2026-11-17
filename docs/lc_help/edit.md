# lc.edit

所有编辑经事务服务执行，返回回执。工具轮询 `scheduled` 到终态；`applied` 才表示完成。

```text
apply create front-column --template blockout/column --size 1m,1m,5m --front 4m
apply create wall-east --template blockout/wall --size 0.2m,5m,3m --at 5m,0m,1.5m
apply move front-column --delta 1m,0m,0m
apply set front-column --size 1m,1m,6m
apply delete front-column
undo --last
select front-column
select wall-*,front-column --nearest
```

`--front` 在玩家朝向前方落地，使用玩家当前脚底高度，创建位置为几何中心。`--at` 指定中心位置。两者只能选一个。角度使用 `--rotation pitch,yaw,roll`，单位为度。

ID 为小写 kebab-case。选择器可使用 ID、`*` 通配和逗号多选；未知 ID 与空匹配会被拒绝。批量删除自动存检查点。脚本必须通过 `lc.client` 调用，不能直接修改数据库或游戏对象。
# W2–W3 扩展

`checkpoint save pre-change` 保存命名检查点；`restore --checkpoint pre-change` 恢复实体快照；重名保存拒绝。恢复自动保存恢复前状态，可以 undo。`presentation cancel` 丢弃未提交预览；`presentation camera-player` 交还玩家镜头。正在正式应用的实体事务不能通过取消预览中止。

W4：有限时间动作见 `lc.help actions`；玩家传送和 3C 参数见 `lc.help checks`。动作返回 scheduled 后使用 receipt 查询，pause/resume/stop 可控制执行；oscillate 先持久化检查点。

