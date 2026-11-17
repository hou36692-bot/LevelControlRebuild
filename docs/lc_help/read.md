# lc.read

```text
templates --kind blockout
player-status --fields transform
objects * --count
objects front-column transform,bounds
objects wall-*,front-column --nearest
objects * --limit 100
consistency
```

距离字段单位为 cm。读取默认最多 256 个实体；大量对象先读 `--count`，再缩小选择器。`consistency` 由 UE 检查 SQLite、内存完整状态与渲染实例数量，Python 不读取数据库。忙时等待当前事务回执再读取。
# W2–W3 扩展

`presentation` 查询施工状态、镜头与实测耗时；`events --since 0` 读取有限事件流；`checkpoints` 列出命名检查点。实体字段增加 source，表示正式调和的所有者。

W4：支持 tag:name、group:name 选择器；`jump-check`、`clearance`、`walk-graph` 见 `lc.help checks`。`player-status` 包含实际 3C 参数；`clock` 供测试估计跨进程时钟偏移。

