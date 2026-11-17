# 表面方案（2.0）

接口正在按开发路线实现；以运行时回执为准。

```text
lc.read surfaces
lc.edit surface apply group:court --preset stone-limestone
lc.edit surface preview tag:wall --preset stone-slate --roughness 0.8
lc.edit surface apply pillar-* --tint 0.8,0.7,0.6 --metallic 0.2 --emissive 0
lc.edit design cancel
lc.edit design commit --expected-version 4
lc.edit undo --last
lc.edit apply create path-mark --template decal/path --at 6m,0,2cm --size 10cm,2m,2m --rotation -90,0,0
```

有限参数为`--tint`、`--roughness`、`--metallic`、`--emissive`、`--normal-strength`、`--wetness`。预览不保存，提交前必须确认基准版本。按ID、`group:`、`tag:`选择；空选择或未知参数拒绝。

贴花模板为`decal/stain`污渍、`decal/marker`墙面标记、`decal/path`路径箭头；尺寸顺序是局部投射深度、宽、高。贴花无碰撞。地面朝向用`-90,0,0`；墙面根据墙法线调整朝向。可移动、删除、撤销、检查点和重启恢复。
