# TelecomTwin 人物 PIE 双层信道修复（2026-08-11）

## 结论

用户截图中的悬空线确实来自第二套旧信道层，不是视觉错觉。修复后人物 PIE 仍保留 30 个信源、1920 个四色屋顶信道几何和 9 个 HISM 批次，同时所有已加载旧模拟信道的可见数为 0。

冷启动验收结果：

| 项目 | 结果 |
|---|---:|
| 人物生成 / 准入 / 移动 / 显示 | 100 / 100 / 100 / 100 |
| 卡住人数 | 0 |
| 正确信源 / 信道几何 | 30 / 1920 |
| 正确信道批次 / 实例 | 9 / 1950 |
| Green / Yellow / Orange / Red | 480 / 480 / 480 / 480 |
| 冷启动镜头加载的旧层对象 | 2796 |
| 旧层可见对象 | 0 |
| 移动到旧信道总览镜头后加载的旧层对象 | 294 |
| 镜头移动后旧层可见对象 | 0 |
| 稳定帧时 P50 / P95 | 21.264 / 32.990 ms |

## 真正原因

地图里同时残留了两代信道对象：

1. 当前正确层：`SIG_Source_00_Direct_Roof` 与 `SIG_Ray_000_Segment_00_Green` 这类对象，共 1950 个。
2. 旧模拟层：`SIG_RaySegment_*`、`SIG_Node_Reflect_*`、`SIG_Node_Source`、`SIG_Source_Main` 等对象。

2026-08-10 的实现只匹配并批处理了第一类对象。旧对象位于 World Partition 单元中，会在 PIE 开始、相机移动或 Cesium/城市分区加载后才进入世界，因此初始化时的一次扫描看不到它们。旧层随后加载并保持可见，于是画面表现为正确屋顶信道和旧悬浮信道同时叠加。

上一轮验收也只使用严格正则统计 30 + 1920 个正确对象，完全忽略了其他 `SIG_*` 标签，所以错误地报告“没有额外对象”。

## 修复方式

### 1. 严格区分两代对象

正确层只允许：

```text
SIG_Source_*_Direct_Roof
SIG_Ray_*_Segment_*
SIG_Ray_*_RoofHit_*
```

旧层识别为：

```text
SIG_RaySegment_*
SIG_Node_*
SIG_Ray_HISM_*
除 *_Direct_Roof 以外的 SIG_Source_*
```

### 2. 同时关闭 Actor 和组件

对旧层对象同时执行 Actor 隐藏、PrimitiveComponent 可见性关闭和 Hidden In Game，避免只改 Actor 标志但组件仍参与绘制。

### 3. 持续处理 World Partition 晚加载

人物 PIE 初始化时先扫描一次，运行过程中每 0.25 秒重新扫描。相机移动导致新的分区单元加载时，旧对象最多在一个扫描周期内被关闭，不会持续形成第二层。

### 4. 修正验收盲区

一键启动和截图脚本现在会枚举全部已加载 Actor，而不是只统计预期的 1950 个对象。只要发现任意旧层 PrimitiveComponent 仍可见，`DEMO READY` 和证据截图都会失败。

## 截图

修复后屋顶信道近景：

![[../Evidence/InvestorDelivery/08_legacy_floating_signal_layer_removed_2026-08-11.png]]

对应机器证据：[[../Evidence/InvestorDelivery/08_legacy_floating_signal_layer_removed_2026-08-11.json]]

## 涉及文件

- `Plugins/OpenMassCrowd/Source/OpenMassCrowd/Private/OpenMassCrowdSpawner.cpp`
- `Plugins/OpenMassCrowd/Source/OpenMassCrowd/Public/OpenMassCrowdSpawner.h`
- `Scripts/OpenMassCrowd/orchestrate_investor_delivery_demo.py`
- `Scripts/OpenMassCrowd/verify_investor_delivery_demo_runtime.py`
- `Scripts/OpenMassCrowd/capture_investor_signal_alignment_evidence.py`

## 交付注意

接收方必须覆盖新编译的 `Plugins/OpenMassCrowd/Binaries/Win64/UnrealEditor-OpenMassCrowd.dll`。只覆盖 Python 或 C++ 源码不会改变已加载插件行为。此修复与第二版 PowerShell 5.1 兼容补丁无关，可以独立覆盖到第一版完整包上。
