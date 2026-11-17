# LC 运行时协议 v3（1.0 兼容扩展，产品 2.0）

## 本机 Agent 模型选择

气泡的模型与思考强度由当前原生 Agent 返回，客户端不维护固定模型列表。Codex 使用 `thread/start` 返回的实际模型、`reasoningEffort` 与分页 `model/list` 的 `supportedReasoningEfforts`、`defaultReasoningEffort`；Claude 使用 stream-json `initialize` 返回的 `models`、`resolvedModel` 和 `supportedEffortLevels`。未选择覆盖值时沿用 Agent 配置。

对话控制器的 `ready`、`model_changed`、`model_error` 事件携带 `{harness,model,resolved_model,effort,models}`。`models` 统一采用 `{model,displayName,description?,resolvedModel?,supportedReasoningEfforts:[{reasoningEffort,description?}],defaultReasoningEffort?}`；模型 ID 和档位保留原值。前端提交 `{model,effort}`，后端再次按当前能力列表校验；连接未就绪、录音或执行对话时禁用选择控件。失败时恢复后端实际选择，不显示尚未生效的配置。

模型选择在控制器队列中顺序应用，下一条消息才使用新值。Codex 保留同一 thread，并在 `turn/start` 传递 model/effort；“Agent 默认”优先使用原生配置的强度，新模型不支持时才使用该模型建议的默认值，避免空值继承上一轮覆盖。Claude 通过原生 `--model`、`--effort` 更新进程参数，已有对话使用原 session 的 `--resume`，首轮前复用 `--session-id`。切换不会创建关卡事务或清空聊天；图片变化仍遵循原有独立的图片会话规则。

成功选择按 Codex／Claude 分别保存在 `%LOCALAPPDATA%/LevelControl/agent-models.json`，重新连接时重新读取原生能力并校验保存值。失效选项回退到当前 Agent 配置并提示，不覆盖原设置文件。用户原生 Agent 的配置与凭据不被改写，此扩展不改变游戏 HTTP 协议号。

## 快速组合构建扩展

### 按场景变化选择短演出

`lgg_build.plan` 增加 `intent:new_structure|extension|edit|placement`（兼容默认 edit）和可选 `presentation:{mode:auto|off|on,camera:player|overview,music:none|architect-pulse}`。auto 仅在首次完整新建结构时播放 4 s，已有场景新增独立组件且不改动旧几何时播放 3 s；编辑、批量摆放和无变化不自动播放。on 用于用户明确要求展示，时长 5 s。camera/music 默认 player/none，只有 on 才允许启用。判断复用本次模型计划和当前场景差量，不新增模型请求。

`/v1/preview` 增加可选 `animate_ids`、`auto_skip`、`finish_delay_s`。entities 仍是完整目标快照，用于严格核对正式提交；animate_ids 指定本次实际播放的子集，其他实体不重复生成预览。auto_skip=true 时，玩家正在移动、处于演出区域或存在提交租约会跳过，播放期间重新检测移动/进入区域。finish_delay_s 默认 1，短演出显式使用 0，避免额外增加旧版收尾定格。短演出使用固定时间轴，不通过向后取音乐节拍延长时长；正式事务耗时、租约等待另行记录。

`POST /v1/presentation {req_id,action:"skip",revision?}` 跳过落位动画、停止音乐并归还镜头，保留待提交请求和持有租约；revision 可防止旧按钮操作新演出。cancel 保持取消待提交建造的原语义。状态增加 animated_count、auto_skip、instant、skip_reason。`GET /v1/player` 增加 speed_cm_s；`/health` 增加 short_presentation=true，候选包据此拒绝误连旧运行时。

`GET /health` 增加 `fast_build:true` 能力标记；新客户端启动时检查该能力，避免误连同协议号的旧游戏。独立快速构建候选包默认使用端口 18859，当前 2.0 用户包端口 18848 保留。

`lgg_build(plan, request_id)` 的组合协议见 [快速构建](lc_help/build.md)。Python 确定性展开局部坐标、嵌套阵列、资产和构造器，所有世界写入仍经过预览与 `/v1/commit`。

`POST /v1/preview` 新增可选布尔 `instant`（默认 false）。true 时 timeline 每项必须为 start_s=0、duration_s=0，不允许 cameras 或音乐；预览在分帧预算内直接落位，提交后下一游戏帧结束演出，归还控制。省略该字段完全保留旧演出行为。

`POST /v1/commit` 新增可选 `environment:{preset?,values?}`。环境仅在正式事务内应用，复用环境域的参数校验，与该 source 的几何在同一撤销记录和 SQLite 事务中保存；任何一项非法或应用/持久化失败时整批拒绝或回滚。可选环境不改变旧请求的行为。最多 16384 项几何变更，环境状态不占几何名额。

客户端记录提交到队列、启动模型、首个模型事件、工具边界和最终对话时间；构建回执另带编译、预览、提交观测和正式画面/控制归还观测时间。HTTP 观测与游戏线程计时分别报告，不将其称为屏幕 scanout。

## 2.0 灯光与世界状态

`GET /v1/environment` 返回 `{version,preview_active,preview_version,state,committed,presets}`；`state` 是当前可见参数，`committed` 是正式参数。`GET /v1/environment/presets` 列出6个内置及自定义方案。内置 ID 为 `morning/noon/golden-hour/dusk/night/overcast`。

`POST /v1/environment` 沿用标准回执，字段为 `{req_id,action,expected_version?,preset?,values?,name?,rate_h_per_s?}`。`action`：`apply`直接提交、`preview`临时预览、`commit`提交当前预览、`cancel`取消、`save`将正式状态保存为命名自定义方案。`preview/apply`先加载指定方案，再合并`values`。未知字段、错误类型、非有限数值和越界参数拒绝；空修改拒绝。`commit`必须带预览的`expected_version`，提交过期预览拒绝。`save`不覆盖已有名字。

| 参数 | 单位/范围 |
|---|---|
| time_h | 0..24小时；设置时间自动计算太阳高度/方位，显式角度覆盖计算值 |
| sun_azimuth_deg / sun_elevation_deg | 0..360° / -90..90° |
| sun_intensity / sun_temperature_k | UE方向光强度0..150000 / 1000..12000 K |
| sky_intensity / atmosphere_density | 0..10 / 0..4（Rayleigh相对密度） |
| fog_density / fog_height_cm / fog_falloff | 0..0.1 / -100000..100000 cm / 0.001..2 |
| fog_color | 3个线性RGB分量，均0..1 |
| cloud_coverage | 0..1；预编译动态云材质的覆盖参数 |
| exposure_ev / contrast / saturation / temperature_k | -10..10 EV补偿 / 0..2 / 0..2 / 1500..15000 K |

`rate_h_per_s`只用于预览，0..2小时/秒；取消恢复正式状态，提交冻结当时的可见状态。读取不改变时间。领域预览与正式几何事务/演出互斥；必须先提交或取消领域预览再改几何。取消不写SQLite或撤销历史。

SQLite schema 2增`world_state`，环境作为独立世界状态存储。编辑历史和新检查点包含环境及自定义方案；旧schema 1自动备份后增表，实体ID不变。旧检查点仅恢复其中保存的几何，不凭空推断历史灯光。未知未来schema拒绝打开。标准`undo`恢复环境修改前状态；重启仅加载已提交状态。

## 2.0 表面与摆放

以下接口按开发路线分阶段实现，未通过运行时测试前不计为交付。

`GET /v1/surfaces` 返回18个预设，分石材/木材/金属/布料/玻璃/自发光6类。实体新增可选`surface`，包含`preset`和完整有限参数：`tint`线性RGB 0..1、`roughness/metallic/wetness` 0..1、`emissive` 0..50、`normal_strength` 0..2。省略时保留1.0白盒默认材质。

`POST /v1/edits`新增`surface`操作：`{op:"surface",selector,preset?,values?}`。先加载预设，再合并参数；未指定预设时修改原有参数。非法预设或参数整批拒绝。`set`允许可选`template`替换预烘焙资产，保留ID。实体`collision`为布尔值，`lod`为0（自动）或1..4（指定LOD）。所有字段进入现有撤销、检查点和SQLite实体JSON。

`POST /v1/design`接受`{req_id,action:"preview"|"commit"|"cancel",operations?,expected_version?}`，用于表面/道具预览。预览仅生成无碰撞临时组件并隐藏被替代的正式画面；实体索引与SQLite仍为正式状态。取消恢复正式画面；提交必须匹配`expected_version`，按同一组操作走普通事务。预览与灯光预览、LGG演出互斥。正式写入期间不可取消，需等待终态后撤销。

贴花使用`decal/stain`、`decal/marker`、`decal/path`模板，实际渲染为Deferred Decal，无碰撞。尺寸三个分量为局部投射深度、宽度、高度；地面投射使用`rotation_deg:[-90,0,0]`。沿用create/move/set/delete及设计预览，不能执行几何动画动作。默认污渍为棕黑圆斑、标记为十字、路径为箭头。

`GET /v1/catalog`返回实际随构建交付的`props`和`prefabs`目录；客户端按category、tag、search筛选。道具ID为`prop/<name>`、预制体为`prefab/<name>`。预制体由具名组件、资产ID、局部变换构成，放置时展开为`<instance>-<part>`稳定实体。整体按ID前缀/组选择，局部替换通过`set template`保留组件ID。

`create`可含`snap:true`，引擎对请求位置做向下射线，按旋转后包围盒底面落地；无支撑拒绝。客户端可以先对XY做显式网格取整。道具随机朝向/缩放必须带确定性seed；阵列和对齐在Python计算为普通事务，不绕过客户端。`expected_version`可用于普通事务的读改写冲突检查。

预烘焙网格记录实际原生包围盒尺寸和中心，实例缩放适配目标`size_cm`；不能假定所有资产都是100 cm立方体。目录未知ID、缺失资源、无效LOD拒绝。每个自有道具生成3级LOD，并使用精确静态网格碰撞，不支持动态物理模拟。

## 2.0 评审与性能

`POST /v1/checks {kind:"sightline",from_cm,to_cm}` 使用真实Visibility碰撞，返回`ok`（查询成功）、`blocked`、`entity_id`、`distance_cm`、`hit_cm`。`ok:true`不等于无遮挡，必须读取`blocked`。两个点重合、非有限坐标、事务应用中或几何预览期间拒绝；世界固定几何命中ID为稳定的`world`。不以包围盒替代实际碰撞。

`POST /v1/review {req_id,action:"performance",frames:120}` 采集30..3600帧，先返回scheduled；完成回执含`report`与保存路径。报告包含原始帧时、FPS p50/p95、帧时p95、DrawCall、启用且可见的场景灯光、进程物理内存、硬件、分辨率与引擎版本。DrawCall取RHI计数，若构建无法提供则明确标记不可用，不能填造数据。FPS从实际游戏帧间隔计算。`action:"cancel"`取消评审并归还镜头。

`POST /v1/capture`可增加`center_cm/radius_cm/height_cm`启用4机位，机位固定为north/east/south/west，朝向同一中心。回执含`captures`数组，每项包含机位ID、变换、绝对PNG路径；文件名包含世界版本和机位。期间阻止正式世界修改，完成或失败均恢复原镜头。PNG压缩和写盘在后台线程执行。

客户端`lc.capture compare --a <capture_req_id> --b <capture_req_id> --name <name>`从两份已完成回执生成同机位A/B并排PNG和JSON说明；相机定义不一致时拒绝。比较只生成评审文件，不改变游戏状态。

`GET /v1/frame-history?since=N`返回最多3600个连续游戏帧样本，包含序号、毫秒、版本、事务/截图标记和截断标记；可在编辑期间读取。验收脚本用它保留编辑及截图期间的尖峰，不以暂停写入的稳态采样代替编辑性能。

桌面助手的供应商、API Key、模型和思考强度属于客户端配置，不新增游戏 HTTP 接口。供应商切换建立新的对话会话，保留游戏世界；同一供应商的模型与思考强度通过下一轮请求参数切换。API Key 使用 Windows DPAPI 加密，配置保存在本机用户目录，不进入项目或发行包。

本文定义本仓库实现的协议。研究视频只提供行为参考，不保证字段与原实现相同。

## 运行与传输

UE 5.8.2，`ProjectLC.uproject -game`。只监听 `127.0.0.1:18844`；端口可由 `-LCPort=N` 覆盖。HTTPServer、Json、DeveloperSettings 是模块，SQLiteCore 是插件。运行时代码不依赖编辑器。

第 1 步场景为 `/Game/Maps/LC_Empty`：100 m × 100 m 地面、PlayerStart、天空与日光。Manny 和 Enhanced Input 内容来自本机 UE 模板，`scripts/bootstrap.ps1` 从引擎安装目录恢复到 `.local-assets`。输入为 WASD、鼠标、Space；初始速度 500 cm/s，跳跃初速度 500 cm/s，胶囊半径 42 cm、半高 96 cm。坐标 Z-up，UE 内部距离单位为 cm。

## 健康检查（第 2 步）

`GET /health` 返回 `{"ok":true,"world":"LC_Empty","port":18844}`。仅游戏世界注册路由；世界销毁时解绑。端口绑定失败必须记录错误，不允许假报 ready。

## 模板与实体（第 3–4 步）

模板为 `blockout/box`、`blockout/column`、`blockout/wall`、`blockout/floor`。盒、墙、地板共享立方体基础网格；柱使用圆柱。每个模板使用 ISM 合批。尺寸是完整边长，位置是几何中心；柱的 X/Y 尺寸分别为直径。实体句柄是稳定的语义 ID，不暴露实例索引、对象名或指针。

```json
{"id":"front-column","template":"blockout/column","transform":{"position_cm":[400,0,250],"rotation_deg":[0,0,0],"size_cm":[100,100,500]},"tags":[],"group":"","version":1}
```

`bounds` 为旋转后世界坐标 AABB 的 `{min_cm,max_cm}`。ID 必须为小写 kebab-case；冲突直接拒绝，不自动重命名。所有数值必须有限，尺寸为正。HTTP 全部距离单位为 cm；工具边界接受显式 `m` / `cm` 并仅换算一次。

SQLite 由 UE 独占写入，路径为 `Saved/LevelControl/entities.sqlite3`。四张表为 `templates`、`entities`、`edits`、`checkpoints`。实体 JSON 与版本持久化，修改记录包含前后状态和回执；启动时恢复实体。

## 事务（第 5 步）

查询接口：`GET /v1/templates`、`GET /v1/player`、`GET /v1/entities?selector=*&fields=transform,bounds&nearest=true&count=true&limit=256`。各查询参数可省略。`count=true` 只返回数量；默认实体列表最多 256 项，同时返回总数与 `truncated`。`GET /v1/consistency` 在 UE 进程内核对实体完整状态、SQLite 和 ISM 数量；事务忙时不返回一致性结论。

POST 仅接受原生客户端 `Content-Type: application/json`，不接受浏览器 Origin；JSON 请求体上限 8 MiB。

写请求必须携带 `req_id`。同 ID 重放返回已有回执，不重复执行。客户端保留原始单行 `command` 用于审计，并通过统一解析器生成结构化 `operations`。引擎校验结构化字段，不重复解析命令语言。

- `POST /v1/edits`：`{req_id,command,operations}`。操作为 `create`（`entity`）、`set`（`selector` 与 `transform`）、`move`（`selector,delta_cm`）、`delete`（`selector`）。
- `POST /v1/undo`：`{req_id,last:true}` 或 `{req_id,tx_id}`。只允许撤销当前最新的未撤销编辑，历史编辑存在后续依赖时拒绝。
- `GET /v1/receipt?req_id=...`：读取请求回执。

回执统一为 `{req_id,tx_id,status,applied,entity_ids,version,warnings,ms}`。HTTP 200；`status` 为 `scheduled`、`applied`、`rejected` 或 `failed`。`ms` 是请求进入引擎后的毫秒数，其他时钟以秒记录。失败不伪装成成功。异步请求先返回 `scheduled`，客户端轮询到终态。

单批最多 16384 个实体。无效选择器、未知模板、重复 ID、非法几何或与玩家胶囊重叠均拒绝整批。变更串行执行；忙时拒绝新事务，已有 `req_id` 的轮询与重放仍可读取。几何调度按每帧 2 ms 预算执行；记录实际最大耗时，不能将预算目标当成测量结果。

批量删除在同一事务中自动保存检查点。未实现的物理与破碎命令明确拒绝。预览与正式实体接口分开。

## 选择器与指示器（第 6 步）

`POST /v1/select {req_id,selector,nearest:false}` 返回选择回执，并显示青色线框。选择器支持精确 ID、`*`、逗号并集、按玩家距离最近一项；同距离按 ID 排序。空匹配编辑被拒绝；读取 `*` 可返回 0 项。选择状态只在当前世界有效，不写实体或编辑历史；撤销选择之后仍撤销最近的实体编辑。指示器使用独立 PrimitiveComponent / SceneProxy，正常渲染路径绘制 12 条边，不调用 `DrawDebug*`。

## 截图（第 7 步）

`POST /v1/capture {req_id,path:"photo.png",camera:"player"}`。只接受 PNG 文件名，输出到 `Saved/LevelControl/Captures/<tx_id>/`，终态回执含绝对 `path`。先返回 `scheduled`，确认文件保存后才返回 `applied`。截图不进入实体撤销历史。无渲染视口时 10 s 超时失败。

`first_visible_game_frame_s` 是首次修改游戏线程几何的时刻，属于可见反馈代理值，不能宣称为实际显示器出帧时间。`commit_s` 是 SQLite 提交完成时刻；回执随后持久化再送达。W1 没有演出，`presentation_s=null`。验收脚本另外记录工具调用墙钟时间，不能拿引擎 `ms` 代替模型端到端时间。

## 预览与演出（W2–W3）

`POST /v1/preview` 接受 `{req_id,source,revision,regions,entities,timeline,cameras}`。`source` 为稳定 kebab-case 名称；`revision` 为正整数，同一运行进程内每次发布必须递增。`regions` 是 `{center_cm,size_cm}` 数组，最多 256 个；实体最多 16384 个，沿用实体 JSON 格式。`timeline` 与实体一一对应，元素为 `{id,start_s,duration_s}`，开始时间 0..110 s，时长 0.05..10 s，总长不超过 120 s。镜头数据包含 `overview`、`hero` 和以组名为键的 `groups`，每个机位为 `{position_cm,target_cm}`。镜头可以省略，默认保留玩家视角。

所有数据先校验，再替换当前预览；无效请求不得清除旧预览。预览用无碰撞的独立 ISM 和自建线框组件，不进入实体注册表、SQLite、撤销历史或选择集。`applied` 回执表示发布成功，画面在后续 Tick 呈现。完整时间轴在 UE 中执行，不占住 MCP 调用。只有一个活动预览；替换时取消尚未开始正式应用的旧提交。修订号保存在运行进程内，不是 SQLite 世界版本。

`GET /v1/presentation` 返回 source、revision、state（idle/previewing/settled/committed/completed/cancelled）、预览数量、落位数量、镜头阶段、游戏线程首个反馈代理时间、演出时间、实际 Tick 最大耗时及帧时统计。`POST /v1/presentation {req_id,action:"cancel"|"camera-player"}` 可随时取消未提交预览或交还镜头。正式事务已经应用时不接受取消；实体事务只能使用其回执撤销。

`GET /v1/events?since=N` 返回 `{session_id,events,next,oldest,truncated}`。事件有递增 seq、游戏进程单调时钟 at_s、type、source、revision。类型包括 preview_published、first_visible、module_camera、preview_settled、cancelled、committed、presentation_complete。环形保留最近 512 项；进程重启后 session_id 改变，客户端重置游标。时钟不能直接与 Python perf_counter 相减。

## 差量提交

常驻对话宿主接收新消息时，若旧提交仍在等待预览，调用 `presentation hold` 暂缓提交；这一轮结束或异常退出时调用 `presentation resume`。预览动画继续播放，正式实体暂不变更，给模型处理用户修改留出时间。hold 是最长 120 s 的租约，宿主崩溃不会无限锁住游戏；status 包含 commit_waiting 和 commit_held。显式取消始终可用。hold 不能打断已经进入应用阶段的事务。

`POST /v1/commit {req_id,source,revision,expected_version,mode:"reconcile",entities}`。实体增加 `source` 字段，旧实体默认空串。source 归属随实体持久化；只能调和该 source 的实体，冲突 ID 不得接管。未变化实体保留版本，变更实体增加世界版本；新增、更新、删除合计最多 16384 项。删除多项自动保存检查点。空差量保留世界版本，不消耗实体撤销历史。

提交必须匹配当前预览 source、revision 和完整实体内容。先返回 `scheduled`，等预览落位后再次检查 expected_version 与玩家碰撞，再执行一笔事务、一条最终回执。等待期间允许替换或取消预览，被取代的回执变为 `rejected`，实体状态不变。SQLite 成功后清除预览，播放英雄镜头并交还原视角。提交回执记录 commit_s；演出结束独立通过状态与事件读取，不能把提交回执当作演出完成。

## 命名检查点

`POST /v1/checkpoints {req_id,action:"save"|"restore",name}`。名称为 kebab-case。save 保存完整实体状态与 source，同名拒绝覆盖，不增加世界版本、不占用撤销历史。restore 校验完整目标状态及玩家重叠，以差量事务恢复；恢复前自动保存当前状态，恢复自身可 undo。`GET /v1/checkpoints` 返回名称与版本。检查点及最终回执持久化；预览、镜头、物理和玩家位置不在快照范围。

## LGG 工具边界

W4 增加以下契约。实现与验收状态统一列于 `docs/roadmap.md`。

### 配乐与节拍

`lgg_run play --music architect-pulse --duration 12` 使用本项目原创 PCM 配乐。`--music none` 保持无配乐。Python 分析 PCM 能量起音，输出节拍表；时间轴把落位时间量化到检测到的节拍。HTTP preview 增加可选 `music:"architect-pulse"`；资源从运行时插件 Resources 目录读取，禁止提供任意文件路径。UE 音频生成回调建立播放时钟，几何使用同一时钟。取消或替换预览停止旧音频，正式提交后的英雄镜头结束时停止音频。

`GET /v1/clock` 返回游戏进程单调时钟 `clock_s`，仅用于以请求往返中点估计时钟偏移。事件增加 `flight_settled`，包含实体语义 ID、目标落位时间、实际落位时间和误差；presentation 增加音乐状态、最大落位误差。音频回调对齐与实际扬声器输出延迟分别验证，不混为同一指标。

游戏失去焦点时仍播放声音，保证用户操作桌面气泡时配乐持续。hold/resume 支持可选 lease_id；语音采集与模型处理使用不同租约，释放一个租约不会解除另一个租约。所有租约均在 120 s 后过期，取消预览会清除租约。

### 3C 与几何查询

`GET /v1/player` 增加实际角色的行走速度、跳跃初速度、重力、最大台阶高度和可行走坡度。`POST /v1/checks` 是只读查询：

- `kind:"jump"`：`start_cm` 为脚底位置，`heading_deg` 为水平角度，可选 `speed_cm_s`、`max_time_s`。使用当前角色胶囊和真实碰撞世界，对重力轨迹逐段 Sweep；返回轨迹、阻挡、落点、时间、水平距离及命中的稳定实体 ID。
- `kind:"clearance"`：`from_cm,to_cm` 为脚底位置，可选 `radius_cm,half_height_cm`。执行胶囊 Sweep，返回 blocked 和命中位置；不移动玩家。
- `kind:"support"`：查询脚底附近的实际可站立表面，供步行图验证使用。

Python `walk-graph` 使用选定平面上的候选站位和 UE 净空查询建立可行走连通关系；默认上限 256 个表面，返回摘要和可选最短路径。跳跃与步行分开标记，不能把可跳跃的捷径称为普通步行连接。

### 运行时控制

`POST /v1/player/control {req_id,action:"teleport"|"configure",...}`。teleport 使用 feet_cm 并检查胶囊重叠；configure 只接受 `max_walk_speed_cm_s,jump_velocity_cm_s,gravity_scale`，均做范围检查。玩家控制仅在当前进程有效，实体检查点不包含玩家状态。

`POST /v1/actions {req_id,selector,kind,duration_s,...}` 支持有限时间的 move、spin、scale、oscillate。返回 scheduled，不占住对话。动作期间只修改实例呈现，完成后使用原事务路径提交最终状态；失败／取消恢复原呈现。oscillate 开始前先持久化自动检查点。动作支持单独的 pause、resume、stop 控制；动作期间其他实体变更拒绝，读取回执与控制不阻塞。不得以动画中间态冒充 SQLite 已提交状态。

### 桌面对话与语音

桌面 UI 使用同一 ResidentAgent、同一 lc.client。线程事件只驱动界面状态，不直接修改世界。麦克风只在按住说话按钮或 F8 时采集，松开后完成最终转写并发送；显示明显的录音状态。原始录音默认不保存。离线中文识别模型位于 `.local-assets/speech/`，模型来源、许可证及校验值记录在本地清单；桌面不会向语音云服务上传录音。

透明气泡包含文本输入、最终／部分转写、AI 回复、连接和施工状态、最近三条工具名称／参数／耗时、取消与镜头交还按钮。窗口可拖动和折叠，提供点击穿透模式；原创建筑助手立绘为项目自制 SVG。文本和语音进入完全相同的模型调用路径。

具体命令见 [LGG 帮助](lc_help/lgg.md)。设计卡和草稿为 Python 侧 JSON，原子保存于 `project/Saved/LevelControl/design-<port>.json`。必须先 site、coarse，再提交 module。构造器只有围墙、地坪、柱廊、门洞、台阶、喷泉。模型写模块参数，程序展开实体；不能直接写数据库或引擎对象。

原生草稿固定为米、Z-up，中心坐标与尺寸均为完整边长，旋转只支持绕 Z 的 yaw_deg。外部 Y-up 数据须显式通过编译器适配 `(x,y,z) -> (x,-z,y)`，不得对 Z-up 草稿再换轴。米到厘米仅在编译器边界执行一次。实体 ID 按显式 area/module 和构造器内固定 part 编号派生 `<scene>-<area:02d>-<module:02d>-<part:02d>`；排序、尺寸调整不改变已有构件的身份。
# 桌面图像输入契约

桌面 QueuedPrompt 和两条 Agent.turn 接受可选的不可变附件元组 images，默认空元组，保持纯文本兼容。每个附件是已校验的客户端临时副本，包含哈希、MIME、宽高、编码字节数和预处理秒数。队列固定发送时的整组附件，不读取之后的界面状态。

每组上限 4 张，源文件上限 20 MiB / 40 百万像素，处理后最长边 2048 px、每张 1 MiB。仅 PNG、JPEG、静态 WebP。API 用户内容使用 input_text 与 input_image（data URL、detail:auto）；文字上下文上限 4 MiB，完整请求上限 12 MiB。Resident 使用 turn/start.localImage；替换/清除旧组通过新 ephemeral 线程及 thread/inject_items 保留角色明确的文字记录和精简回执。同组不重复附加。

图片仅是参考资料；纯图片消息补充仅分析指令。几何、环境、撤销和完成判定仍使用既有事务与回执，本扩展不增加 UE HTTP 接口。日志记录 count、width、height、bytes、sha256、preprocess_s、request_bytes，不记录图片载荷或原始绝对路径。详见 [图像输入](lc_help/images.md)。
# 3.0 原生 Harness 接入

MCP 增加 `lc.context()` 与 `lc.receipt(request_id)`；`lc.edit`、`lc.capture` 增加可选 `request_id`，已有命令与 UE HTTP 协议保持兼容。上下文给出项目、运行 session_id、world_version、玩家状态、新请求标识与场景摘要。查询回执时同步收敛已持久化的构建记录；unknown 不自动重新提交。

MCP 配置用 LC_PROJECT_ID、LC_PORT/LC_BIND_PORT、LC_DATA_DIR、LC_WORKSPACE 显式绑定。数据与源码分离，绑定后不能改端口。截图终态回执同时作为 structuredContent 返回，PNG 内容使用 MCP ImageContent，原始文件保留。

# Semantic core extension (protocol 4)

The additive runtime endpoints are `POST /v1/query`, `POST /v1/changes`,
`POST /v1/annotations`, and `GET /v1/semantic-state`. Their public contract is
documented in `lc_help/semantic.md`. Existing routes retain their meaning.
Semantic metadata, logical nodes and instance overrides share the entity
transaction, undo and checkpoint boundary. Schema 3 is opened on a copied
database; the original schema 2 world is preserved. Runtime observations have a
separate session sequence and are not persisted every frame.

# 3.1 客户端会话补充

3.1 继续使用协议 3，不改变本文件的 HTTP 读写契约。Codex 与 Claude Code 适配器向气泡界面提供相同的回复增量、工具轨迹、审批/提问、构建回执与结束事件。审批回复仅对应当前会话的请求；取消、切换项目或关闭客户端后，不得把旧回复发给新会话。未知或失败回执不得转成成功。

## 3.2 外挂能力加载

客户端提供插件、技能、工作流、外部 MCP 四个分类总开关，默认关闭。设置仅作用于 LevelControl 创建的 Agent 会话，不修改原生 Agent 的全局配置。应用开关后新建会话，保留世界与设计数据，不重发未完成操作。LevelControl 核心关卡 MCP 始终启用；原生联网搜索、图片输入与读图工具默认启用，仍遵循原生权限和模型支持范围。

关闭技能时禁用实际技能清单，不能仅隐藏目录；关闭外部 MCP 时禁用继承的服务器及插件提供的 MCP，不能只写入空表。工作流范围包含动态工作流、用户自动指令文件、hooks 和自动记忆；LevelControl 自身的领域约束保留。组织强制策略由原生 Agent 管理。插件自身含有的技能、工作流和 MCP 同时受所属类别开关控制。

# 向上生长与增量预览扩展

截图请求可附加 `include_ui:true`，用于核对实际施工标注；默认 false 保持原视口截图语义。截图期间的帧时不纳入性能样本。

`POST /v1/preview` 保留 source、revision、entities、regions、timeline 的完整目标快照形式。新增 `incremental`（默认 false）；true 只允许修改当前活动 source，以稳定实体 ID 比较并保留未变的实例、进度和镜头。删除只影响差量实体。请求和 revision 仍保持幂等、单调及事务互斥检查。

所有动画统一为世界 Z 高度裁切，网格不发生位移/旋转/缩放。timeline 兼容旧结构，但同一分组使用共同起止时间及合并包围盒。新增 `growth_duration_s`（0.05–10，默认 3）、`seal`（默认 false，最后交稿压缩剩余演出至最多 4 秒）、`director`（默认 false）及 `caption`（最多 160 字符）。regions 可附加 id、label，线框同样自下而上显露。GET /v1/presentation 增加 growth_groups（边界、阈值、进度及实例数）、effect=upward、caption 和 geometry_transform_updates=0。

增量发布不等于正式提交；封稿后仍调用 /v1/commit 校验最终完整快照、expected_version，并将几何与环境在同一事务应用。演出控制及回执语义保持：skip 只跳过演出，cancel 取消待提交，hold/resume 保留租约。director 模式下移动/视角输入只还镜头；Esc 跳过。非 director 旧自动短演出保留已有自动跳过条件。

