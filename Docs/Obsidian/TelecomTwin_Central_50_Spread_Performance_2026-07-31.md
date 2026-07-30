---
title: TelecomTwin 中环 50 人分散与性能优化
date: 2026-07-31
project: TelecomTwin
status: 已通过 PIE 验收
tags:
  - UnrealEngine
  - OpenMassCrowd
  - Central
  - DigitalTwin
---

# TelecomTwin 中环 50 人分散与性能优化

## 本轮目标

本轮只处理演示质量与运行性能：让中银大厦周边的 50 名行人不再挤在同一条轨迹上，让更远处的人仍呈现行走姿态，并在不破坏 Cesium 地面约束、寻路、人物资料 UI、基站关联和室内切换的前提下减少卡顿。

## 实现方式

### 1. 行人散开但仍沿真实道路行走

- 继续使用经过认证的 ZoneGraph 车道中心线作为寻路真值，导航输入不使用视觉偏移，避免短路径构建器把合法路线判成越界。
- 为稳定人物编号分配 7 个确定性的横向展示带，间距 45 cm，最大横向偏移为 ±135 cm。
- 再按稳定编号加入 5 个纵向错相位置，间距 60 cm，最大错相为 ±120 cm，避免不同路线在交汇处长期重合。
- 每个偏移点都先做 Cesium 精确 XY 首次命中测试；只有命中真实地表才展示偏移。未命中的人物立即回到已认证的道路中心点，不制造悬空位置。
- 投资演示模式采用“单人物失败关闭”，某个远端瓦片临时不可查询时只冻结该人物，不再冻结同一 150 m 单元内其他已经获得地面支持的人。
- 等待路线按照每帧 1 人轮转重试，避免 50 次 A* 同帧突发，也避免人物永久等待。

### 2. 更远距离仍显示走路动作

- 低成本骨骼行走层从 35 m 延长到 200 m。
- 高成本角色上限为 6，低成本骨骼角色上限为 24，其余人物继续使用带步态动画的 VAT 表示。
- 实测演示镜头下有 17 个低成本骨骼角色和 33 个 VAT 行人；远处 VAT 的动画帧、播放速率和移动速度均保持更新，因此不是静态模型平移。

### 3. 性能优化

- Cesium 地面守卫从每轮 24 人降为每轮 4 人，并继续轮转覆盖全部人物。
- O(n²) 的人物重叠遥测从每帧改为 1 Hz；实际避让与认证变换仍按 Mass 帧执行。
- 网络关联刷新调整为约 3.03 Hz。
- 两个屋顶基站完成验证后，屋顶复验降为每 2 秒一次。
- 调试线条以 10 Hz 短生命周期刷新，不再创建永久调试图元。
- LOD 数量有明确上限，超出预算的人物自动进入 VAT 层。

## 验收结果

2026-07-31 在新启动的 UE 5.7 编辑器和 PIE 世界中运行自动验收，结果为 `passed: true`：

| 指标 | 结果 |
| --- | ---: |
| 配置 / 生成 / 移动 / 表示人数 | 50 / 50 / 50 / 50 |
| 横向展示带 | 7 / 7 已占用 |
| Cesium 偏移支持 | 49 人，1 人安全回退中心线 |
| 活跃真实车道 | 34 条 |
| 最大单车道人数 | 4 人 |
| 严重重叠 | 0 对、0 人 |
| 地面异常 / 不支持位置 | 0 / 0 |
| 骨骼行走可见距离 | 200 m |
| P50 帧时间 | 17.824 ms |
| P95 帧时间 | 22.121 ms |
| 修改前 P50 基线 | 333.3336 ms |

P50 帧时间约降低 94.7%，约为原来的 1/18.7。验收同时确认两座真实屋顶基站、人物资料卡、室内断开/室外重连、远端 VAT 步态和原信号系统可恢复状态均未被破坏。

## 截图

下图为中环真实道路上的最终 PIE 画面。人物在同一路段形成横向分带和纵向错相，可以看到多名人物处于不同走路姿态；所有展示位置都通过 Cesium 地表查询或回退到认证中心线。

![[03_spread_ground_paths_50.png]]

仓库截图：`Docs/Evidence/InvestorDelivery/03_spread_ground_paths_50.png`

## 证据与代码

- 自动验收：`Docs/Evidence/InvestorDelivery/investor_delivery_runtime_latest.json`
- 运行时实现：`Plugins/OpenMassCrowd/Source/OpenMassCrowd/Private/OpenMassCrowdSpawner.cpp`
- 运行时声明：`Plugins/OpenMassCrowd/Source/OpenMassCrowd/Public/OpenMassCrowdSpawner.h`
- 验收脚本：`Scripts/OpenMassCrowd/verify_investor_delivery_demo_runtime.py`
- OpenSpec 变更：`openspec/changes/spread-central-crowd-performance`

## 回档方法

本轮完成后会在 `feature/investor-delivery-demo` 分支创建一个独立提交并推送。修改前的基线提交是 `2714449c`。如需撤销本轮而保留后续历史，可先用 `git log -1 --oneline` 找到本轮最新提交，再执行 `git revert <本轮提交哈希>`。
