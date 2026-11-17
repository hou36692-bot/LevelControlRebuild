# 架构：四层边界与运行时约束

LevelControl 的目标不是"在编辑器里生成资产"，而是在游戏已经以 Standalone 模式运行时修改世界。这个前提决定了整套架构：运行时功能不能依赖编辑器，所有修改必须可回执、可撤销、可观测，且不能打断正在游玩的用户。

## 分层

```
01 对话层   Codex app-server / Claude Code CLI
                 │  只认 lc.* / lgg_* 工具
02 设计层   tools/lc、tools/lgg、tools/presenter（Python）
                 │  只认 HTTP + JSON，不认 UE 类型
03 实体层   ue/Plugins/LevelControlRuntime（C++）
                 │  只懂实体、选择器、事务、回执、预览、检查点
04 演出层   tools/presenter + UE 呈现
                    只消费交接事件与时间轴
```

边界是硬约束，违反即返工：

- UE 插件里不得出现 LGG 概念（模块、条款、草稿）；它只处理实体与事务。
- Python 里不得出现 `AActor`、`UWorld` 等 UE 类型；跨层只传 JSON 与 HTTP。
- 演出层可以调用运行时控制接口，但不得直接写实体表。
- SQLite 只由 UE 进程持写锁；Python 永不写库。

## 一次修改的生命周期

1. Agent 调用 `lc.*` / `lgg_*` 工具，MCP 服务器把请求转成命令。
2. `tools/lc/client.py` 是脚本与工具唯一的写入口，向 `127.0.0.1:<port>` 发 HTTP 请求。
3. `ULCSubsystem` 路由请求，`ULCTxService` 统一执行：预览、提交、撤销、检查点、捕获、检查。
4. 正式提交在一个事务内写入实体表与 SQLite，并推进世界版本；预览不进入实体索引。
5. 回执经 HTTP 返回，Python 侧把 `applied` / `scheduled` / `rejected` 转成 Agent 能读的结构。
6. 演出层根据已提交结果生成时间轴，通过呈现组件播放，结束后归还玩家控制。

## 协议要点

- **健康检查是能力协商**：`GET /health` 返回 `protocol` 与能力位（`semantic_core`、`fast_build`、`short_presentation`、`upward_growth`、`incremental_preview`）。客户端据此拒绝误连旧运行时，而不是"看起来能用"。
- **幂等**：写请求必须带 `req_id`；同一 `req_id` 重放返回同一回执。
- **失败也是 200**：`status` 为 `rejected` / `failed`，原因写入 `warnings`，让 Agent 能继续推理。
- **预览与提交分离**：`/v1/preview` 只产生预览件，`/v1/commit` 才推进世界版本；中途打断只需丢弃预览。
- **单一事实源**：`docs/protocol.md` 先于实现更新；`lc.help` 的内容从 `docs/lc_help/*.md` 读取，不硬编码。

完整的 HTTP 契约见 [protocol.md](protocol.md)。

## 实体层组件

| 组件 | 职责 |
|---|---|
| `ULCSubsystem` | 可 Tick 的世界子系统；只绑定 `127.0.0.1`，拥有健康检查、GET/POST 路由与语义端点，世界销毁时解绑 |
| `ULCTxService` | 事务中心：提交、预览、撤销、检查点、捕获、检查、动作、环境与语义查询 |
| `FLCStore` | SQLite 持久化：实体、回执、世界版本、环境、语义状态、检查点、空间候选（RTree） |
| `FLCSemanticWorld` | 已提交身份与运行时观测的读模型；发布、观测、查询、增量 |
| `ULCIdentityComponent` | 作者声明的持久 ID、显示名、角色、别名、区域、ISM 实例级身份、状态字典 |
| `ULCWireframeComponent` | 自建 `PrimitiveComponent`/`SceneProxy`；支持向上裁切盒，Shipping 可用，不用 `DrawDebug*` |
| `ULCPresentation` | 飞行、生长组、镜头阶段、贴花、字幕、音乐与提交/演出状态机 |
| `ULCEnvironment` | 太阳、天光、天空大气、高度雾、体积云与后处理的动态参数 |
| `LCSurface` | 18 个表面预设、补丁合并、ISM 批量创建与延迟贴花 |
| `LCSelector` | 确定性选择器解析；Actor 名/指针/索引永不跨层暴露 |

## 稳定性设计

- 实体 ID 稳定：设计路径派生为 `<scene>-<area:02d>-<module:02d>-<part:02d>`；Agent 创建的实体使用 kebab-case 语义名；用户口语名只写别名。
- 决定论：相同设计文档必然编译出相同 ID；数组展开保留 `members` 键；重命名不改变身份。
- 单位只在边界换算一次：设计层统一米 + Z-up，UE API 统一厘米。
- 破坏性动词自动检查点；撤销以事务为单位；回执是唯一"完成"证据。
- 单批实体上限 16,384；生成分帧，每帧应用预算 2 ms。

## 取舍与已知缺口

- 语义核心为了可查询性维护了独立的身份读模型，代价是提交路径的全量重排；10k 实体下仍可工作，但尚未索引化到可 GA 的程度。
- 预览与提交分离增加了状态机复杂度，但换来了可中断与严格核对。
- 演出层与实体层解耦，允许同一份设计被不同的呈现实现消费；代价是时间轴契约需要单独测试。
