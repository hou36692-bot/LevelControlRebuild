# LC 工具 v2（W1–W4）

桌面主界面支持选择供应商、模型和思考强度。“API 设置”可配置 OpenAI、DeepSeek 或其他 Responses 兼容服务的根地址、API Key 和模型列表；这些设置只影响对话宿主，不改变本页工具语法。

- `lc.connect --port 18844`：连接本机游戏。
- `lc.help lc_read` / `lc_edit` / `lc_capture` / `lc_connect`：读取本目录帮助。
- `lc.read objects * --count`：结构化查询。
- `lc.edit apply create front-column --template blockout/column --size 1m,1m,5m --front 4m`：生成。
- `lc.edit select front-column`：青色选中框。
- `lc.edit undo --last`：撤销最近实体编辑。
- `lc.capture photo.png`：视口截图。
- `lgg_card init --scene courtyard`：读取总则并建立设计卡。
- `lgg_draft #N site|coarse|module ...`：发布草稿与预览；语法见 `lc.help lgg`。
- `lgg_run play` / `status` / `cancel`：异步施工、查询、取消。
- `lc.edit checkpoint save before` / `restore --checkpoint before`：检查点。

工具参数为单行字符串。只有 `applied` 表示修改完成；`rejected` / `failed` 要读 `warnings`。LGG play 的 scheduled 表示正在施工，正式提交与演出完成分别查询。`lc.help checks`、`lc.help actions` 提供 3C 和动作语法；桌面助手支持本地语音，play 可用 `--music architect-pulse`。物理破碎与联机不属于本次 MVP。

2.0 灯光：`lc.help environment`；读取 `lc.read environment`、`lc.read environment-presets`。

2.0表面与贴花：`lc.help surfaces`，预设目录`lc.read surfaces`。

2.0道具：`lc.help placement`；`lc.read catalog --search bench`。

2.0评审：`lc.help review`；视线、四机位、A/B及性能报告。
# 语义核心

`lc.help semantic`：结构化查询、实体身份、逻辑组合、视角锁定、实例覆盖与实时状态。
`lc.query` / `lc.changes` / `lc.annotate`：读取、增量同步与事务标注。
`lgg_knowledge`：按需加载配对设计案例；不训练模型权重。

