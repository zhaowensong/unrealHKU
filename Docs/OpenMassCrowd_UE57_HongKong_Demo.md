# TelecomTwin 香港 30 人群集 Demo（UE 5.7）

这是当前 Demo 的简明入口。完整实现、复现步骤、验证数字、限制和回滚方法统一维护在：

- [City Sample Crowds 集成与验证](CitySampleCrowds_UE57_Integration.md)
- [机器可读证据与截图索引](Evidence/OpenMassCrowd/README.md)
- [Obsidian 完整实施记录](Obsidian/TelecomTwin_CitySample_30_Pedestrian_Demo.md)

当前冷启动结果为 30 个 Mass Entity、30 个 City Sample 可视人物、9 个 ZoneGraph 节点
和 22 条碰撞认证有向 lane。每个人独立使用官方 `FZoneGraphAStar` 选择路线；地面认证
使用 exact-XY Cesium first-blocker 查询、10 cm 级多轨支撑采样和 30 cm 半径胶囊 sweep。
运行时若 avoidance 把实体带出已认证走廊，会先回收到当前 lane 中线；仍失败时回滚
last-valid Transform 与 lane 状态。未经认证的位置不会显示。

启动：

```powershell
pwsh -ExecutionPolicy Bypass -File .\Scripts\OpenMassCrowd\launch_telecomtwin_citysample.ps1
```

等香港 Cesium 瓦片可见后按 `Alt+P`。本轮严格 first-blocker 冷启动约 44 秒，建议
预留 40–50 秒完成碰撞路网认证；
成功日志包含：

```text
OPEN_MASS_CROWD_ASTAR_READY nodes=9 lanes=22
OPEN_MASS_CROWD_READY requested=30 spawned=30
```

行人只在 PIE 中由 Mass 动态创建，因此编辑状态下看不到人是预期行为。当前是香港局部
bounded demo，不是全城自动人行路网；Mass avoidance 也不是严格刚体碰撞。

City Sample Crowds 属于 UE-Only Content，协作者必须通过自己的 Epic/Fab 权限取得素材，
并挂载到 Git 忽略的 `/Game/CitySampleCrowd`。仓库只保存本项目代码、脚本、文档和证据。
