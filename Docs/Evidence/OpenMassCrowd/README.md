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

## 2026-07-27：Central 300 人四项体验升级

- [完整实施与验收记录](../../Obsidian/TelecomTwin_Central_300_Ground_Navigation_Profile_UI_2026-07-27.md)
- [`central_crowd_experience_runtime_latest.json`](central_crowd_experience_runtime_latest.json)：Ground-Only、长距离往返、远景 VAT 动画、点击档案和电信场景联合 PASS。
- [`CentralCrowdExperience/ground_only_asset_latest.json`](CentralCrowdExperience/ground_only_asset_latest.json)：562 条 Ground-Only lane、49 个分量、6 个生成区、300 目标人口。
- [`CentralCrowdExperience/excluded_files_latest.json`](CentralCrowdExperience/excluded_files_latest.json)：7 个用户原有文件的大小与 SHA-256 全部保持不变。
- [`11_central_profile_glass_ui.png`](11_central_profile_glass_ui.png)：右侧半透明人物档案的最终运行截图。

联合报告同时证明原电信场景仍为 30 个信源、1920 条射线，绿、黄、橙、红各 480 条。

## 2026-07-30：Central 100 人与远距步态优化

- [完整实施与验收记录](../../Obsidian/TelecomTwin_Central_100_Far_Gait_Refinement_2026-07-30.md)
- [`CentralCrowdExperience/12_central_100_far_gait_a.png`](CentralCrowdExperience/12_central_100_far_gait_a.png) 与 [`13_central_100_far_gait_b.png`](CentralCrowdExperience/13_central_100_far_gait_b.png)：约 82 米物理距离、35° 长焦下的成对步态截图。
- [`CentralCrowdExperience/far_gait_pair_latest.json`](CentralCrowdExperience/far_gait_pair_latest.json)：同一 `HK-C-058` 的 VAT 帧 13.344 → 50.065，固定播放速率 1.35，`passed=true`。
- [`central_crowd_experience_runtime_latest.json`](central_crowd_experience_runtime_latest.json)：100 人全部 admitted / simulated / represented / moving，0 stuck、0 unsupported、0 severe overlap；30 信源、1920 射线、四色各 480 条仍通过。
- Ground-Only 路网保持 562 条 lane 与 49 个连通分量，六区人口配额改为 17、17、17、17、16、16。
