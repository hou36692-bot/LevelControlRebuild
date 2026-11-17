# 道具、预制体与摆放（2.0）

```text
lc.read catalog
lc.read catalog --category furniture
lc.read catalog --search bench
lc.edit prop place prop/bench --id court-bench --at 6m,0,1m --snap
lc.edit prop preview prefab/rest-stop --id east-rest --at 10m,4m,0 --snap
lc.edit prop place prop/bollard --id bollards --at 6m,0,1m --count 100 --step 2m,0,0 --snap --seed 42 --random-yaw 20 --scale-min 0.9 --scale-max 1.1
lc.edit placement align group:benches --axis y --mode center
lc.edit placement array court-bench --prefix west-benches --count 5 --step 0,2m,0
lc.edit apply set east-rest-part-00 --template prop/stool
lc.edit design cancel
lc.edit design commit --expected-version 12
```

`--at`为单道具几何中心；预制体为组合根位置，各部件使用目录中的局部中心。`--snap`将底面贴到支撑面，无支撑时拒绝。`--grid 50cm`对XY网格吸附；阵列方向用`--step`，不改变原实体。`--count`指道具/预制体数量，展开后仍受16384实体上限限制。随机数由`--seed`确定，可复现。

`--group`指定组；默认预制体使用实例ID作为组。`--collision off`关闭碰撞；`--lod 0`自动，1..3指定道具LOD。整体变换可以选择`east-rest-*`；局部替换使用具名组件ID。替换默认保留位置、尺寸、标签和表面；需要恢复道具原始尺寸时显式传`--size`。

所有操作有回执，可撤销、检查点和重启恢复。只接受项目目录中的预烘焙资产，没有运行时GLB导入。
