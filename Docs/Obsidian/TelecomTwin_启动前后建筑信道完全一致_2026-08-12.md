# TelecomTwin 启动前后建筑信道完全一致

日期：2026-08-12

## 结论

人物演示现在不会再改变已经完成的建筑信道。编辑器未运行状态和人物 PIE 运行状态使用同一批持久化 Actor：30 个 `SIG_Source_*_Direct_Roof` 信源和 1920 个 `SIG_Ray_*` 四色反射对象，共 1950 个。

启动人物后不会隐藏、移动、复制、重建或实例化这 1950 个对象，也不会创建另一套建筑信道。人物系统只增加人物、人物资料 UI，以及独立的人物—基站关联线。

## 为什么以前启动后会变化

上一版为了降低 1950 个独立 StaticMeshActor 的绘制开销，在 PIE 开始时做了两件事：

1. 隐藏编辑器中的 1950 个原信道 Actor；
2. 按网格和材质把它们复制成 9 个运行时 HISM 批次。

批次使用的世界变换虽然与原对象相同，但渲染对象已经换了。HISM 的 LOD、裁剪、排序和发光表现可能与原 Actor 不同，所以启动人物会改变此前已经完成的信道工作。这个耦合没有必要。

## 本次修改

### 1. 人物系统不再拥有建筑信道

删除运行时 HISM 构建、原 Actor 隐藏和退出时恢复逻辑。人物初始化现在只做只读验证：

- 原对象总数必须为 1950；
- 30 个信源和 1920 个反射对象必须全部存在；
- 每个对象必须继续使用有效 StaticMeshComponent；
- 1950 个对象必须全部保持可见。

### 2. 旧悬浮层仍然独立清理

World Partition 晚加载的旧 `SIG_RaySegment_*`、`SIG_Node_*`、`SIG_Ray_HISM_*` 和旧 `SIG_Source_*` 仍每 0.25 秒扫描并关闭。它们不是当前 1950 个正确对象，因此清理旧层不会修改正确屋顶信道。

### 3. 人物关联线与建筑信道彻底分层

人物—基站的浅蓝/灰色关联线仍由 `DrawInvestorAssociationVisuals()` 单独绘制，屋顶端点来自实时 Cesium 屋顶校验点 `+4 cm`。它不创建绿色、黄色、橙色或红色建筑反射，也不移动原来的 30 个信源。

## 自动验收结果

| 检查项 | 结果 |
|---|---:|
| 编辑器 / PIE 原信道 Actor | 1950 / 1950 |
| 信源 / 反射对象 | 30 / 1920 |
| Green / Yellow / Orange / Red | 480 / 480 / 480 / 480 |
| PIE 运行时信道批次 | 0 |
| PIE 中仍可见的原对象 | 1950 |
| 缺失 / 额外 / 隐藏对象 | 0 / 0 / 0 |
| Actor 变换不一致 | 0 |
| StaticMeshComponent 不一致 | 0 |
| 最大位置 / 旋转 / 缩放差异 | 0 / 0 / 0 |
| 旧悬浮层可见对象 | 0 |
| 启动时修改可见性 | 否 |
| 启动时重建信道 | 否 |

运行验收同时达到 100 人生成和准入、0 卡死。Codex/UE 窗口失焦时 UE 被系统节流到约 3 FPS，因此这次后台采样不作为前台性能结论；它不影响上述逐对象信道一致性结果。

机器可读证据：[[../Evidence/InvestorDelivery/signal_startup_parity_2026-08-12.json]]。

## 同机位画面

启动前：

![[../Evidence/InvestorDelivery/15_signal_before_pie_window_2026-08-12.png]]

启动 100 人后：

![[../Evidence/InvestorDelivery/16_signal_during_pie_window_2026-08-12.png]]

两张图使用同一编辑器窗口与同一信道相机。Cesium 城市瓦片会因编辑器态与 PIE 的流送进度呈现不同清晰度，因此几何是否完全一致以逐对象 JSON 比对为最终依据。

## 涉及文件

- `Plugins/OpenMassCrowd/Source/OpenMassCrowd/Private/OpenMassCrowdSpawner.cpp`
- `Plugins/OpenMassCrowd/Source/OpenMassCrowd/Public/OpenMassCrowdSpawner.h`
- `Scripts/OpenMassCrowd/orchestrate_investor_delivery_demo.py`
- `Scripts/OpenMassCrowd/verify_investor_delivery_demo_runtime.py`
- `Scripts/OpenMassCrowd/capture_investor_signal_alignment_evidence.py`
- `Scripts/OpenMassCrowd/capture_signal_startup_parity_evidence.py`

## 当前二进制

`Plugins/OpenMassCrowd/Binaries/Win64/UnrealEditor-OpenMassCrowd.dll`

- 编译时间：`2026-08-12 07:19:26`
- 大小：`1,097,728` 字节
- SHA-256：`4407C36C622A03CB36178EA9B129019ADC8000D49E6A2BCB04F6561C538703D7`
