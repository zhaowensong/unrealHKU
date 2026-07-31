---
title: TelecomTwin 中环 50 人持续行走修复与 500 人容量估算
date: 2026-08-01
project: TelecomTwin
status: 50 人已通过持续 PIE 与完整交付回归
tags:
  - UnrealEngine
  - OpenMassCrowd
  - Central
  - Performance
  - DigitalTwin
---

# TelecomTwin 中环 50 人持续行走修复与 500 人容量估算

## 1. 问题现象与根因

50 人全部生成后，少数人物会在短车道或车道端点走着走着停止。问题并不是人物丢失，也不是 Cesium 地面失效，而是三个导航状态没有始终保持一致：

1. UE Mass 的短路径动作能够重新激活，但人物的认证 Transform 没有真实位移；旧逻辑却可能把它视作已经恢复。
2. 补救逻辑曾只推进 ZoneGraph 的车道距离，下一帧 PathFollow 又用旧的 \`ShortPath.ProgressDistance\` 覆盖它，因此人物反复前进一步又退回原点。
3. 车道入口预约原本不区分 7 条横向展示带，不同带、实际相距 45–90 cm 的人物仍可能互相等待；在短边循环中形成端点队列。

日志还确认了异常的同向车道进度回退，例如约 \`49 cm -> 40 cm -> 49 cm\`。正常向前行走不应在同一有向车道上倒放。

## 2. 最终解决方式

- 只有认证 Transform 的真实位移才能清零静止计时；重新激活短路径本身不算恢复。
- 清除认证 Transform 约束后遗留的 \`bSteeringFallingBehind\`，避免 Mass PathFollow 因视觉横向约束错误停止。
- 当短路径需要小幅恢复时，同时推进 \`LaneLocation.DistanceAlongLane\` 和 \`ShortPath.ProgressDistance\`，避免下一帧状态回滚。
- 在同一有向车道上执行单调进度约束：短路径刷新若重建出更旧的位置，就恢复到上一帧认证进度，并同步短路径游标。
- 入口预约和恢复前向间距均按展示带区分；同带保持 55 cm，异带仍受 20 cm 真实防穿模约束。
- 已经小于 20 cm 的历史重叠只允许严格增加距离的分离动作，防止“每一步分离都被回滚”的吸收状态。
- Cesium 直播碰撞偶发流式查询失败时，投资演示继续信任已经完成精确 XY 认证的缓存点，并保留实时查询作为监测，不让一个瞬时 miss 冻结整格人物。
- 若人物超过 5 秒仍无认证位移，则把当前位置映射到同一物理道路的反向认证车道；正反位置误差必须不超过 50 cm。该处理相当于原地掉头，不传送到别处，然后重新发出短路径。

## 3. 验收结果

最终版本完成两层验证：

| 验证 | 结果 |
| --- | --- |
| 60 秒持续验证 | 30 次采样，\`maximum_stuck=0\`，最长卡住采样为 0，最大静止 4.032 s |
| 完整交付回归 | 12 项全部通过，50 人运动，0 unsupported，0 overlap |
| 地面与位置 | invalid 0，unsupported 0，严重重叠 0 |
| 帧时 | P50 18.451 ms，P95 21.383 ms，最大 48.723 ms |
| 长时间恢复 | 后续回归中原地掉头恢复触发 2 次，触发后 \`stuck=0\` |

证据：

- \`Docs/Evidence/InvestorDelivery/investor_long_run_liveness_latest.json\`
- \`Docs/Evidence/InvestorDelivery/investor_delivery_runtime_latest.json\`
- \`Scripts/OpenMassCrowd/verify_investor_long_run_liveness.py\`
- \`Scripts/OpenMassCrowd/verify_investor_delivery_demo_runtime.py\`

演示画面：

![[03_spread_ground_paths_50.png]]

仓库截图：\`Docs/Evidence/InvestorDelivery/03_spread_ground_paths_50.png\`

## 4. 当前机器的 50 人基线

| 项目 | 实测 |
| --- | --- |
| CPU | AMD Ryzen 9 7945HX，16 核 32 线程 |
| GPU | RTX 4060 Laptop，8 GB |
| 系统内存 | 31.7 GB |
| UE Working Set / Private | 4.91 / 7.80 GB |
| GPU 显存占用 / 利用率 | 4.55 GB / 38% |
| 角色预算 | 高精 6、低成本骨骼 24，其余 VAT |

## 5. 500 人压力估算

这是容量估算，不是已经完成的 500 人实测。当前交付代码和认证数据明确把活跃人数限制在 100，投资模式字段也限制到 100；因此现在不能只把数字改成 500 就宣称支持。

若扩展到 500 人，角色预算保持 6 个高精、24 个低成本骨骼，约 470 人使用 VAT。渲染不会简单增加 10 倍，但 CPU 算法会成为主要风险：

- 50 人无序人物对为 1,225 对；500 人为 124,750 对，人物对检查增加约 101.8 倍。
- 当前认证碰撞解析含逐帧全人物对扫描；500 人下必须改为空间哈希/网格邻域，否则主线程会首先成为瓶颈。
- Cesium 守卫每 0.05 秒只处理 4 人。完整轮询 50 人约需 0.625 秒，500 人约需 6.25 秒；应将预算提升到 8–16，或根据可见性和风险分层。
- 一次性 admission、候选筛选和证据扫描也需要分批，并把 100 人认证配额扩展到 500。

在不改当前 O(n²) 结构的情况下，保守估计为 P50 35–70 ms、P95 60–120 ms，即约 14–29 FPS，并可能出现更大的周期性卡顿。GPU 预计仍主要受 Cesium 城市场景控制，8 GB 显存可能升到约 5–6 GB；额外 Mass/VAT 数据的内存增量相对小，32 GB 可以运行，但编辑器开发余量有限。

完成空间分桶、分块遥测、8–16 人地面守卫批次和 500 人认证 admission 后，当前 16 核 CPU + 8 GB GPU 的合理目标是 P50 22–35 ms、约 28–45 FPS。若要把 500 人作为稳定 60 FPS 演示目标，建议按“高频 16 核桌面 CPU、12–16 GB 显存、64 GB 内存”准备硬件，并在目标分辨率和相机路径上做真实 500 人 benchmark。

## 6. 结论

- 50 人“走着走着停住”的问题已完成可复现修复，并通过持续验证和完整交付回归。
- 当前机器足够支持现有 50 人演示。
- 500 人不是单纯的硬件升级问题；先解除 100 人产品上限并把逐帧人物对检查改成空间邻域，才值得进行真实硬件压力测试。
