# 3C 与通行检查

```text
player-status
jump-check --from 11.5m,0m,2.25m --heading 0 --speed 5m
clearance --from 10m,0m,0m --to 12m,0m,0m
walk-graph tag:walkable --from-id platform-a --to-id platform-b
```

长度显式使用 m/cm；speed 表示每秒对应距离。查询使用当前角色胶囊和重力，返回阻挡、实际碰撞落点和稳定实体 ID。walk-graph 最多分析 256 个站立表面，检查的是步行连通性；跳跃捷径另用 jump-check 验证。

```text
lc.edit player teleport --feet 11m,0m,2.25m
lc.edit player configure --speed 5m --jump-velocity 5m --gravity-scale 1
```

传送点重叠时拒绝。玩家参数在进程内生效，实体检查点不恢复这些参数。
