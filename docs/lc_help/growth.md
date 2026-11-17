# 分阶段向上生长

普通建筑新建与独立扩建使用一个 AI 顺序调用 `lgg_plan`、`lgg_module`，不创建子 agent。

`lgg_plan(plan, request_id)` 发布粗规划。plan 包含 name、expected_version、anchor（可省略，自动放到玩家前方）、modules、environment（可选）。每个模块包含 id、label、size、at（局部米制中心）、yaw（可选）、depends_on（可选）、brief（设计意图）。模块位置也可用 relative_to、side（left/right/front/back/above/below）及 gap 表达，程序计算坐标；不得同时提供 at。公共边界在粗规划中确定。返回 build_id 和已确认的模块布局。

`lgg_module(build_id, module, request_id)` 交付一个模块。module 包含 id、nodes、definitions（可选），沿用 lgg_build 的局部组合/构造器。模块局部原点位于包围盒中心；构件必须位于模块边界内。重复排列可用 repeat:{count,span}，程序从跨度算间距；span 与 step 互斥。一次交付一个模块，校验成功后立即设计下一模块，不等待演出。

全部模块交齐后自动执行完整性检查，并将几何和环境作为一个事务提交。预览回执 applied 只表示本模块已显示；只有 construction_status=applied 才表示正式构建成功。scheduled 表示等待演出结束或提交租约释放。工具只返回简短状态，不回传展开后的全部实体。

每个模块使用一个沿世界 Z 轴上升的高度面，普通模块默认 3 秒。网格变换和 UV 不变；线框、实体、玻璃、贴花统一按高度显露。后续交稿不重播已完成部分。默认自动镜头、无音乐；移动或转动视角交还镜头，Esc 或 presentation skip 跳过演出但继续建造。presentation cancel 取消未提交作业。旧 lgg_build、lgg_card/draft/run 入口保留，演出形式同样为向上显露。

名称与 ID 使用英文 kebab-case，label 可用中文。石灰石表面预设为 stone-limestone；其他预设查 lc.read surfaces。每份交稿使用独立 request_id；同一 ID 仅可重放相同内容。拒绝的交稿不增加 revision，修正后继续原 revision。修改已成功发布的模块才增加 revision，并重新交付其依赖模块；旧版本结果不能覆盖新结果。世界版本冲突时拒绝提交，不自动覆盖用户场景。
