# 灯光与氛围（2.0）

先读取状态或预设，再修改。参数错误会拒绝整次操作。

```text
lc.read environment
lc.read environment-presets
lc.edit environment preview --preset golden-hour
lc.edit environment preview --time 18 --fog-density 0.025 --cloud-coverage 0.7
lc.edit environment preview --time 6 --rate 0.1
lc.edit environment cancel
lc.edit environment apply --preset night
lc.edit environment apply --sun-intensity 2 --sun-temperature 4500 --exposure 1
lc.edit environment apply --fog-color 0.5,0.6,0.7 --fog-height 2m
lc.edit environment commit --expected-version 4
lc.edit environment save --name courtyard-night
lc.edit undo --last
lc.edit checkpoint save lighting-approved
```

`preview`不保存，`apply`直接提交，`commit`将当前预览提交。提交预览的版本来自`lc.read environment`的`preview_version`；不能猜测版本。`save`只保存正式状态，拒绝重复名字。预览期间必须先提交或取消再编辑实体。时间流逝只在预览中运行，取消恢复原状态。

太阳角度以度、色温以K、曝光以EV表示；`--fog-height`必须带m/cm。颜色为0..1线性RGB。其余参数：`--sun-azimuth`、`--sun-elevation`、`--sky-intensity`、`--atmosphere-density`、`--fog-falloff`、`--contrast`、`--saturation`、`--temperature`。帮助中的`sun_intensity`采用UE方向光原生强度，不声明为照度计实测值。
