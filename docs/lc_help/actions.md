# 有限时间动作

```text
act front-column spin --degrees 180 --duration 2
act front-column move --delta 1m,0m,0m --duration 2
act front-column scale --factor 1.2 --duration 2
act front-column oscillate --amplitude 0m,0m,0.2m --frequency 1 --duration 4
action pause
action resume
action stop
```

raise 保留原语法 `act front-column raise 1m`。其余动作立即返回 scheduled，最终回执为 applied 才代表最终状态已持久化。oscillate 在开始前自动保存检查点，结束时回到起点。取消恢复动作前状态；已提交动作使用 undo。动作最多 256 个实体，时长 0.1..30 s。动作中其他实体事务拒绝，控制命令始终可用。
