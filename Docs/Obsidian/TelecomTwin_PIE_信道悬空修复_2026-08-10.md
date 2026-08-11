# TelecomTwin 人物模式信道悬空修复（2026-08-10）

> [!warning] 此次验收已被 2026-08-11 的双层信道修复取代
> 本文只证明 1950 个正确对象在编辑器态与 PIE 中变换一致，遗漏了 World Partition 后加载的旧 `SIG_RaySegment_* / SIG_Node_* / SIG_Source_Main` 层。最终结论见 [[TelecomTwin_PIE_双层信道修复_2026-08-11]]。

## 结论

人物移动的 PIE 版本现在与没有人物的编辑器版本共用同一套信道几何来源。运行人物时不会再生成一套抬高的临时射线，也不会重新估算屋顶点。

最终重启验收结果：30 个信源、1920 个四色信道几何全部进入 PIE；逐项比较编辑器态与 PIE 原始 Actor 的位置、旋转、缩放，三类最大误差均为 0。运行时显示为 9 个 HISM 批次、1950 个实例，颜色仍为 Green / Yellow / Orange / Red 各 480 个，信源 30 个。HISM 写入后再读回的浮点旋转误差仅为 `0.000003°`，远低于 `0.01°` 验收容差。

## 原因

之前的人物演示模式做了两件互相叠加的事情：

1. 进入 PIE 后隐藏已经保存在 `shanghai` 关卡里的 `SIG_Source_*` 和 `SIG_Ray_*`，共 1950 个对象。
2. 每 0.1 秒调用 `DrawInvestorDemoVisuals()` 画临时 Debug 线。其中屋顶节点被人为放到屋面上方 760 cm，信标放到屋面上方 3600 cm，人物关联线目标也在屋面上方 780 cm。

所以人物运行时看到的是另一套悬空演示层；按 Esc 结束 PIE 后，临时 Debug 线消失，关卡中原来的信道恢复，因此看起来又正常了。

## 修改方式

### 1. 删除悬空临时绘制

`DrawInvestorDemoVisuals()`、刷新计时器和全部抬高的 Debug 节点代码已经从 C++ 中删除。人物模式不再画屋顶假节点、竖直信标、人物到屋顶的临时关联线。

### 2. 原信道成为唯一几何真源

PIE 初始化时读取每一个原始 `SIG_Source_*` / `SIG_Ray_*` 静态网格组件已经保存的世界变换，直接复制其：

- Static Mesh
- Material
- World Location
- World Rotation
- World Scale

没有调用 Cesium 重新投点，也没有用人物系统重算射线路径，因此人物版与无人物版不会产生两套落点。

### 3. 仅优化绘制方式，不改变几何

若同时显示 1950 个独立 StaticMeshActor 和 100 人，帧时会明显升高。现在按“网格 + 材质”分成 9 个 HISM 批次：

- 8 组四色 Cylinder / Sphere 信道几何，每组 240 个实例
- 1 组 Source Sphere，30 个实例

原 Actor 在 PIE 中只为避免重复绘制而隐藏；批次实例使用原组件的精确世界变换。结束 PIE 时批次销毁，原 Actor 的隐藏状态逐项恢复。

### 4. 启动器增加强制验收

一键启动从 5 步扩展为 6 步。宣布 `DEMO READY` 前会同时检查：

- 编辑器态与 PIE 都有 30 个信源、1920 个信道几何
- 标签没有缺失、重复或额外对象
- 1950 个原 Actor 的 Location / Rotation / Scale 与编辑器态一致
- 9 个运行时批次可见，总实例 1950
- 四种颜色各 480，信源实例 30
- C++ 读取回来的批次变换误差在容差内
- `runtime_overlay_enabled == false`

任何一项失败，一键启动不会报告演示就绪。

## 重启后验收

| 检查 | 结果 |
|---|---:|
| C++ 完整编译 | 通过 |
| 一键冷启动并进入 PIE | 通过 |
| 人口 | 100 / 100 / 100（生成 / 准入 / 显示） |
| 验收时移动 / 卡住 | 99 / 0 |
| 原始信号对象 | 30 信源 + 1920 信道 = 1950 |
| HISM 批次 / 可见实例 | 9 / 1950 |
| Green / Yellow / Orange / Red | 480 / 480 / 480 / 480 |
| 位置最大误差 | 0.0 cm |
| 旋转最大误差 | 0.0° |
| 缩放最大误差 | 0.0 |
| HISM 写入后旋转读回误差 | 0.000003° |
| PIE 缺少 / 多余 / 变换不一致 | 0 / 0 / 0 |
| 悬空 Debug 覆盖层 | 已删除，运行时为 false |

机器验收摘要：[[../Evidence/InvestorDelivery/07_signal_alignment_acceptance_2026-08-10.json]]

运行总览：

![[../Evidence/InvestorDelivery/05_people_and_persisted_signal_overview_2026-08-10.png]]

屋顶区域细节：

![[../Evidence/InvestorDelivery/06_persisted_rooftop_landing_detail_2026-08-10.png]]

两张截图旁边各有同名 JSON，记录 PIE 世界、相机、30 + 1920 对象和 9 / 1950 批次结果。几何是否一致以逐项变换比对 JSON 为准，截图用于人工观察。

## 涉及文件

- `Plugins/OpenMassCrowd/Source/OpenMassCrowd/Private/OpenMassCrowdSpawner.cpp`
- `Plugins/OpenMassCrowd/Source/OpenMassCrowd/Public/OpenMassCrowdSpawner.h`
- `Scripts/OpenMassCrowd/orchestrate_investor_delivery_demo.py`
- `Scripts/OpenMassCrowd/verify_investor_delivery_demo_runtime.py`
- `Scripts/OpenMassCrowd/capture_investor_signal_alignment_evidence.py`

## 回滚

- 修复前基线：`baaee009`
- 修复回档标签：`checkpoint/people-rooftop-signal-alignment-2026-08-10`

若回滚到修复前，人物模式会重新出现旧的抬高 Debug 信道；正常交付应使用本回档标签或之后的版本。
