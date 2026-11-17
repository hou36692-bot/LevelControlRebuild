# 评审与验证（2.0）

```text
lc.read sightline --from 0,0,1.5m --to 10m,0,1.5m
lc.edit review performance --frames 120
lc.capture courtyard-a.png --center 12m,0,2m --radius 18m --height 8m
lc.capture courtyard-b.png --center 12m,0,2m --radius 18m --height 8m
lc.capture compare --a <第一份截图req_id> --b <第二份截图req_id> --name courtyard
lc.edit review cancel
```

视线查询`ok`只表示查询完成，遮挡必须看`blocked`；命中还返回稳定ID、距离和坐标。失败不能回答“无遮挡”。几何预览期间先取消或提交，避免把临时画面当正式碰撞。

性能采集和4机位截图返回scheduled时仍在处理，读取原req_id等待终态。性能报告保留原始样本，不删除尖峰。FPS为游戏帧率；RHI DrawCall和进程物理内存均注明来源。性能结论必须在冻结硬件与分辨率的参考场景上复测。

A/B要求两组相机定义相同，且均完成。输出4张并排图和可追溯JSON，不能比较不同机位后声称是同一视角。
