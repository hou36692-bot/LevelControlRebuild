# 语义实体与查询

`lc.query(query)` 读取完整提交的场景快照，返回稳定身份和解释。自然语言由 Harness 转成结构化条件；UE 不解析自然语言，也不执行用户提供的 SQL。

## 查询契约

```json
{"role":"column","within":"courtyard","ring":"outer","side":"front","order":"view_left","ordinal":3,"fields":["name","role","bounds","transform"]}
```

字段：`ids` 为明确 ID 数组；`name` 精确匹配名称或别名；`role/source/area/pattern/ring/side` 为精确过滤；`within` 为祖先 ID；`parent` 为直接父实体；`bounds` 为 `{min_cm,max_cm}`；`near` 为 `{point_cm,radius_cm}`；`relation` 为 `{kind,target}`；`order` 为 `id/view_left/distance`；`ordinal` 从 1 开始；`limit` 默认 32、最多 256；`cursor` 使用返回的分页游标；`fields` 指定需要的字段；`mode` 为 `list/count/summary`；`state` 为 `committed/live`，默认 committed。

视角查询捕获摄像机位置、前向、右向和上向，使用水平投影排序。视角不等于可见性；后方对象、跨区域/跨侧候选或投影重合时返回 `ambiguous`，不生成写入凭证。缺失语义返回真实缺口，不按网格形状补猜角色。推断身份只能作为候选，不能直接产生有序写入凭证。

`lc.edit(command, request_id?, expected_version?, selection_id?)` 可使用查询返回的选择凭证。命令中用 `selected` 表示锁定对象，例如 `apply move selected --delta 0,0,100cm`。凭证固定 ID、提交版本与对象状态；转身不改变目标。凭证有效期 120 秒，过期、对象变化或重启必须重新查询。逻辑实体的移动、删除和表面操作展开到叶子构件；绝对 set 仅允许叶子，避免将整组构件重叠放置。

`lc.changes(cursor?)` 返回持久变更与实时变化的独立游标，删除有 tombstone。首次调用获取同步起点；重启、游标过期明确返回 `resync_required`。演出事件不属于实体变更流。

## 身份与 LGG

`lgg_build` 节点可增加 `key` 和 `semantic`。`key` 是不可变作者键，改名或移动层级时保留；没有 key 的旧计划继续使用路径。`semantic` 包含 `name,aliases,role,area,pattern,ring,side,provenance,relations`；provenance 为 `design/manual/inferred`。阵列可增加 `members`，与 count 对应，用不变成员键承接插入与排序修改。

组合产生逻辑实体，几何叶子关联父实体。几何模板不自动决定角色。构造器按其设计意图明确输出构件角色。

关系写为 `{"kind":"connects_to","target":"key:upper-platform"}` 时，编译器将作者 key 解析为本次场景的稳定实体 ID。重复成员使用 `key:columns/column-a`。也可直接引用已知运行时 ID；不能把显示名称当成唯一身份。

复用定义内部的 key 由实例路径限定，跨实例引用内部构件时使用身份映射中的对应 ID。更新已有 scene 时保留原放置锚点，旧计划中的 front_m 不会随玩家移动而重新定位整座建筑；明确重定位时提供 anchor.at。

直接修改记录覆盖字段及原生成值；删除记录删除标记。重新生成应用覆盖；新设计同时改动同一覆盖字段时返回冲突，不能静默覆盖。`override_policy` 默认 `preserve`，显式 `discard` 才放弃该场景的实例覆盖。

`lc.annotate(ids, semantic, request_id, expected_version)` 经 UE 事务写入标注，支持撤销。它不授予底层对象几何编辑权限。已有对象默认仅可读取和标注。

## 状态与身份寿命

注册覆盖 LC 构件、逻辑组合、已有 Actor、独立实例与动态对象。持久身份由 `ULCIdentityComponent.PersistentId` 或明确实例映射提供。未提供持久身份者标记 `session`，不可承诺重启身份连续。重复持久 ID 为冲突，不能合并对象。

committed 是最近一次完整事务，live 是 UE 实际采样；实时数据包含序号、时间和生命周期。注册不等于语义标注完整。预览和内部呈现对象不进入正式索引。
