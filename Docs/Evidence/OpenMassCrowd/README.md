# OpenMassCrowd 最终证据索引

> 验证日期：2026-07-18（香港时间）
> 范围：TelecomTwin `/Game/Maps/shanghai` 的 30 人 bounded demo

## 三份最终机器可读报告

| 文件 | 结论 | 关键事实 |
|---|---|---|
| [`open_mass_city_sample_runtime_latest.json`](open_mass_city_sample_runtime_latest.json) | PASS | 30 可见、30 移动、移动中位数 489.485 cm、30 种外观、根节点最大误差 0.001 cm |
| [`open_mass_city_navigation_runtime_latest.json`](open_mass_city_navigation_runtime_latest.json) | PASS | 30 移动、0 卡住、累计 443 行程、0 重规划、9 节点、22 条有向 lane |
| [`open_mass_lod_transition_runtime_latest.json`](open_mass_lod_transition_runtime_latest.json) | PASS | Low 30 → High 30 → Low 30，同一组 30 个稳定 Mass seed |

导航报告还记录：30 个身份各做 3 次 exact-XY Cesium 地面检查并全部命中；11 次运行
时地面投影失败全部通过回到 lane 中线恢复，last-valid 回滚 0、不可恢复 0、当前不受
支撑的可视人物 0。人际间距没有小于 20 cm 的 severe overlap，但存在 1 个小于
60 cm 的 body-overlap 样本，最小中心距离 37.928 cm。这是 Mass avoidance 的验证，
不是硬物理碰撞约束。

SHA-256：

```text
0EF74D5589526890C0122900B96809B0439FE1441900408D9D5AB5EABFD4E4B6  open_mass_city_sample_runtime_latest.json
D7FA6F6445C05DC29473E2F25D3F05835874381A7E9C5A38E69A16FDA807A3F6  open_mass_city_navigation_runtime_latest.json
3DD7ED9915AAFE7053714775479BFC7CC2793A20F8E863C00A95F8BA5CFBF06F  open_mass_lod_transition_runtime_latest.json
```

## 最终 LOD 截图

- [`08_lod_low_30.png`](08_lod_low_30.png)：远相机阶段，30 个 Low skeletal 表示。
- [`09_lod_high_30.png`](09_lod_high_30.png)：近相机阶段，30 个 High 表示。
- [`10_lod_low_30_return.png`](10_lod_low_30_return.png)：相机再次拉远，返回 30 个 Low
  表示。三张图对应 LOD JSON 中的完整 Low→High→Low 序列。

High 与 Low 是 `AOpenMassCrowdCitySampleActor` 和
`AOpenMassCrowdCitySampleLowResActor` 两个不同表示类；Low 是 skeletal low-res，
不是 VAT/ISM。LOD 切换前后使用相同 seed 重放外观，不会随机换人。

## 辅助运行画面

- `04_city_sample_30_wide.png`：30 人整体运行画面。
- `05_city_sample_29_variants_close.png`：较早运行批次的 29 种外观近景；最终 JSON
  已严格验证 30 种，不应把文件名中的 29 当作最终数量。
- `06_city_sample_cesium_foot_grounding.png`：Cesium 路面脚部近景。
- `07_city_sample_restart_after_6s.png`：早一轮干净重启后继续运行的画面。

这些截图帮助肉眼检查，但最终数量、贴地、移动、导航和 LOD 结论以三份 JSON 为准。

## 证据所对应的实现

- 运行时路网是 3×3、9 节点；逻辑上最多 24 条有向 lane，本轮经过碰撞裁边后为
  22 条。系统只接受 16–24 条、偶数且保持 9 节点连通的安全子集。
- 每条候选 lane 使用不超过 10 cm 的 exact-XY 三纵轨和横向地面采样；不做邻域
  高度回退，也不复用其他 XY 的 Z。
- 身体空间使用约 30 cm 半径胶囊，执行 World sweep 和 direct Cesium component
  sweep；地面判断遵循 global nearest raw first-blocker policy。
- 30 个 Mass Entity 独立选择 A→B 并使用 `FZoneGraphAStar`，同时启用 Mass
  steering/avoidance；不是让所有人沿同一预设闭环运行。
- 路口转向仍由运行时 exact guard、中线恢复和 last-valid 回滚保护，因此证据不应被
  扩大为数学连续地形证明。

## 运行与回滚

从工程根目录启动：

```powershell
pwsh -ExecutionPolicy Bypass -File .\Scripts\OpenMassCrowd\launch_telecomtwin_citysample.ps1
```

本轮严格 first-blocker 冷启动约 44 秒，建议预留 40–50 秒。City Sample Crowds 是
Fab 的 UE-Only 内容，其他机器必须
通过自己的 Epic/Fab 授权取得并挂载资源。

纯 Git 回滚点：

```powershell
git fetch origin --tags
git switch -c rollback/pre-city-navigation checkpoint/pre-city-navigation-2026-07-18
```

## 历史证据

`01_mass_crowd_running.jpg`、`02_mass_crowd_after_6s.jpg` 和
`03_restart_smoke.jpg` 是 2026-07-15 的 BattleWizard 临时视觉阶段，只保留开发
历史，不能作为当前 City Sample Crowds 最终结果。

`HongKongStreetCrowd-runtime.png` 与 `MassNavMesh-UE57-runtime.png` 是更早的原型
调研证据，也不代表本次最终实现。
