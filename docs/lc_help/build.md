# 通用快速构建

`lgg_build(plan, request_id)` 用一次结构化调用提交完整场景。正常新建不需要先调用 lgg_card、lgg_draft、lc.help 或读取玩家。request_id 使用本轮主机给出的值；相同请求重放返回原回执，改变内容必须使用新请求 ID。

plan 示例（米、Z-up，局部 +Y 指向玩家前方，+X 为右侧，at 为几何中心）：

```json
{"name":"display","nodes":[{"id":"floor","asset":"blockout/floor","at":[0,0,0.1],"size":[8,8,0.2],"surface":{"preset":"stone-limestone"}},{"id":"display","at":[-2,0,0.2],"repeat":{"count":3,"step":[2,0,0]},"parts":[{"id":"table","asset":"prop/table","at":[0,0,0.4],"size":[1.4,0.8,0.8]},{"id":"items","asset":"blockout/column","at":[-0.4,0,0.95],"size":[0.15,0.15,0.3],"repeat":{"count":5,"step":[0.2,0,0]},"surface":{"tint":[0.1,0.5,0.8]}}]}],"environment":{"preset":"noon"}}
```

- 根字段：`name`（新建名称）、`scene`（仅修改已有快速构建时填写）、`expected_version`（主机给出的世界版本）、`anchor`、`definitions`、`nodes`、`environment`。新建默认在玩家前方留 2 m 空隙，自动计算旋转和场地框；`anchor:{"front_m":4}` 修改间距；`anchor:{"at":[x,y,z],"yaw":0}` 显式使用世界坐标原点。已有 scene 默认保留锚点。
- 每个 node 必须有稳定 `id`；内容选择 `asset`、`kind`、`parts`、`use` 之一。`asset` 引用已烘焙模板或预制体；`kind` 引用已有 floor/wall/doorway/colonnade/stairs/fountain 构造器，可带原构造器 `params`。`parts` 是局部子组件数组；`use` 引用 `definitions` 中同结构的组件定义。禁止循环引用。
- 共同字段：`at` 默认 `[0,0,0]`，`yaw` 默认 0，`size` 为完整尺寸，`surface`、`collision` 可在组合上继承。组合不支持整体非均匀缩放；尺寸只用于叶节点。
- `repeat:{"count":N,"step":[x,y,z]}` 包含原位实例，共 N 个；count 为 `[nx,ny,nz]` 时生成三维网格，step 分别为各轴间距。局部组合与阵列可嵌套，最多 12 层、16384 个展开实体。使用阵列表达重复物体，不逐件输出世界坐标。ID 与阵列索引稳定；改变尺寸或其他阵列数量不重编号已有实体。
- `surface` 支持 `preset/tint/roughness/metallic/emissive/normal_strength/wetness`，范围沿用表面协议；`environment` 只接受现有 `preset/values`。发光材质不是独立灯光。
- `intent` 应准确声明本轮用途：new_structure 是完整新建建筑或区域，extension 是已有建筑新增独立部分，edit 是修改/纠错，placement 是重复物体摆放。缺省 edit 兼容已有计划。新建结构默认 4 s，独立扩建默认 3 s，仅演本轮新增部分；移动、换材质、调灯光、重排和重试不自动演出。
- `presentation` 可设 `{mode:"off"}` 直接落位，或 `{mode:"on",camera:"overview",music:"architect-pulse"}` 明确要求 5 s 展示。默认 auto/player/none；镜头、音乐不能因自动演出被顺带开启。传统长演出仍可用 lgg_card/lgg_draft/lgg_run。
- 玩家移动、进入演出区域或录音持有提交时跳过自动演出。`lc.edit presentation skip` 只跳过演出，建造继续；`presentation cancel` 才取消建造。短演出不额外增加 1 s 定格，事务处理与持有租约不算视觉演出时长。
- 返回精简标准回执及 scene、entity_count、timings、presentation_ready。只有 `status=applied` 且 `presentation_ready=true` 表示本次构建已提交并交还控制；scheduled 不算完成。完整计划、实体、计时和回执存于 Saved/LevelControl/builds/<port>/，可按 request_id 查询原始回执。修改提交必须携带 expected_version，重新提交完整计划；删除的组件被调和移除，其他 scene 不受影响。
- 语音或其他提交租约仍持有时，工具及时返回 scheduled，不阻塞模型回合等待租约释放。桌面后台继续核对原请求；应用成功后保存设计卡并显示实际完成时间，取消/拒绝时不显示完成。
