---
title: TelecomTwin 远距人物步态持续显示
date: 2026-08-12
project: TelecomTwin
status: 100 人功能与性能验收通过
tags:
  - UnrealEngine
  - OpenMassCrowd
  - VAT
  - LOD
  - DigitalTwin
---

# TelecomTwin 远距人物步态持续显示

## 问题

用户观察到镜头拉远后，人物的走路动作像是消失，只剩整体平移。检查发现这不是 VAT 动画纹理 mip 导致的静止：运行中的 VAT 动画帧一直在推进，动画纹理也使用 `NoMipmaps + Nearest`。真正需要消除的是两处距离限制：此前低成本骨骼人物最多 24 个并只覆盖到 200 米，VAT 表示在相机距离达到 1 公里后进入 `Off`。

## 实施

- 高精骨骼预算保持 6；低成本骨骼预算从 24 提升到 48。
- 骨骼步态距离从 200 米扩大到 350 米。预算仍是硬上限，避免 100 个完整 Skeletal Actor 同时更新。
- VAT 可见动画范围从 1 公里扩大到 3 公里；超过 350 米的人仍由 City Sample 派生 VAT 播放步行动画，不切成静态占位物。
- VAT 播放速率保持固定 `1.35x`，每人继续使用独立 `TimeOffset`，避免 LOD 或速度变化造成动画相位跳变。
- 正式验收器会固定同一人物，把 PIE 相机放到 1.5 公里外，比较两个世界时间采样的 VAT 帧号，并核对实例 WPO 与裁剪距离。

配置审计位于 `Config/InvestorDeliveryDemo.json` 的 `pedestrian_lod`，运行时报告也直接输出 `skeletal_walk_distance_m=350` 与 `vat_visible_distance_m=3000`，避免只依赖配置文件自述。

## 验收结果

### 1. 1.5 公里固定人物

- 人物：`HK-C-084`，stable index 83；
- 实际距离：1504.948 米 → 1506.860 米；
- 表示方式：两帧均为 VAT；
- 动画帧：82.426 → 31.934，按 98 帧循环计算实际推进 47.508 帧；
- 播放速率：两帧均为 1.35；
- 第二帧实际移动速度：151.921 cm/s；
- 100 人仍全部有表示；
- 结果：`passed=true`。

机器报告：[[Docs/Evidence/InvestorDelivery/far_gait_runtime_1500m_2026-08-12.json]]

### 2. 隐藏距离阈值审计

运行时共检查 24 个 VAT 实例组件：

- `world_position_offset_disable_distance = 0`：不会因为距离停止 WPO 顶点动画；
- `instance_end_cull_distance = 1,000,000 cm`：实例裁剪点为 10 公里；
- `cached_max_draw_distance = 0`：没有额外的组件最大绘制距离；
- 所有检查均写入上面的 1.5 公里机器报告。

### 3. 100 人性能

前台 60 秒采样窗口排除 UE 编辑器失焦时固定约 3 FPS 的节流样本后，完整验收通过：

| 指标 | 实测 |
| --- | ---: |
| 生成 / 准入 / 显示 | 100 / 100 / 100 |
| 移动 / 卡住 | 100 / 0 |
| 不支持地面 / 无效位置 / 严重重叠 | 0 / 0 / 0 |
| 当前低成本骨骼 / VAT | 30 / 70 |
| P50 帧时 | 17.515 ms |
| P95 帧时 | 22.631 ms |
| 性能门槛 | P95 < 33 ms，通过 |

完整报告：[[Docs/Evidence/InvestorDelivery/investor_delivery_runtime_far_gait_2026-08-12.json]]

### 4. 画面证据

约 80 米远景中可见人物处在不同的手臂和腿部步态姿势：

![[Docs/Evidence/InvestorDelivery/distant_gait_80m_2026-08-12_a.png]]

高分辨率截图队列只成功落下第一帧；第二帧请求超时，因此没有把它伪装成成对截图证据。同一人物的连续动画以 1.5 公里逐帧机器报告为权威证据。

## 视觉边界

系统现在可以保证远距人物仍在播放动画且不会在 1 公里后消失，但不能突破屏幕像素的物理限制：人物在极远处只占几像素时，裸眼无法分辨手脚细节。350 米内优先使用骨骼表示改善可读性；更远距离依靠 VAT 保证动作连续，观察细节需使用长焦或靠近镜头。

## 复现

项目进入 100 人演示并稳定后：

```powershell
python .\Scripts\OpenMassCrowd\verify_investor_far_gait_runtime.py `
  --distance-m 1500 `
  --output .\Docs\Evidence\InvestorDelivery\far_gait_runtime_1500m_2026-08-12.json

python .\Scripts\OpenMassCrowd\verify_investor_delivery_demo_runtime.py `
  --output .\Docs\Evidence\InvestorDelivery\investor_delivery_runtime_far_gait_2026-08-12.json
```

两个脚本都只读取/移动 PIE 观察相机，不保存地图，也不修改 Mass 人物位置。
