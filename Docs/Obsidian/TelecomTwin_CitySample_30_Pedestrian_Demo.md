---
title: TelecomTwin City Sample 30 人群集 Demo 实施记录
date: 2026-07-18
tags:
  - TelecomTwin
  - UnrealEngine
  - MassEntity
  - CitySampleCrowds
  - Cesium
status: verified
---

# TelecomTwin City Sample 30 人群集 Demo 实施记录

## 目标与边界

目标是在原有香港数字孪生场景里加入 30 个真正移动的城市行人：使用 UE 5.7
Mass 负责群集移动和避让，使用 Epic 官方 City Sample Crowds 负责人物外观；人物
必须落在真实 Cesium 道路上，项目重启后仍可直接运行，并且不能破坏既有通信射线。

本轮只做一个可演示、可回滚的局部街道人群，不扩展为全香港人行路网，也不继续
修改已经完成的电信射线四项问题。

## 最终状态

最终重启运行报告：`overall_passed = true`，诊断错误数为 0。

| 要求 | 证据结果 |
|---|---:|
| 真实 Mass 人口 | 30 |
| City Sample 可见人物 | 30 |
| 官方 Blueprint 子人物 | 30 |
| 外观组合 | 30 种 |
| 走路动画播放 | 30 / 30 |
| 6.022 秒内移动超过 60 cm | 30 / 30 |
| 移动距离中位数 | 627.887 cm |
| Cesium 命中 | 30 / 30 |
| Mass 根节点最大贴地误差 | 2.135 cm |
| 脚部贴地检查 | 30 / 30 |
| 信源保留 | 30 / 30 |
| 射线保留 | 1920 / 1920 |
| 射线 Mesh 已赋值且可见 | 1920 / 1920 |
| 四色射线 | 每色 480 |

完整报告：[[../Evidence/OpenMassCrowd/open_mass_city_sample_runtime_latest.json]]

## 截图

### 30 人整体

![[../Evidence/OpenMassCrowd/04_city_sample_30_wide.png]]

### 外观随机化近景

![[../Evidence/OpenMassCrowd/05_city_sample_29_variants_close.png]]

### Cesium 脚部贴地

![[../Evidence/OpenMassCrowd/06_city_sample_cesium_foot_grounding.png]]

### 重启后继续移动

![[../Evidence/OpenMassCrowd/07_city_sample_restart_after_6s.png]]

广角图与重启后 6 秒图中的队形和位置不同；运行报告进一步以相同 30 个代理的
变换差值证明每个人都移动，不依赖肉眼判断。

## 问题一：为什么以前只是“放了一些人”

早期临时实现的视觉 Actor 与 Mass Entity 没有持续同步，可能出现后台 Mass 在走、
画面里人物停着；人物外观还是 BattleWizard，占位效果无法代表成熟城市人群。

解决方法：

1. 删除关卡里旧的 BattleWizard External Actor，只保留一个
   `HK_OpenMass_Crowd_Spawner`。
2. 新增 `AOpenMassCrowdCitySampleActor`，内部通过 `ChildActorComponent` 创建官方
   `BP_CrowdCharacter`。
3. Mass 仍是唯一移动所有者；每帧在 `TG_PostUpdateWork` 读取
   `FTransformFragment`，把最终变换同步到一对一的 City Sample 代理。

结果：运行报告同时看到 30 个 Mass Entity、30 个活跃代理和 30 个官方 Blueprint
子人物，而且 30 人都实际移动。

## 问题二：为什么 30 个人会长得一样

直接设置 `ChildActorClass` 时，官方 Construction Script 会先用默认选项构造，
事后再调用随机函数已经来不及，结果看起来像复制同一个人。

解决方法：销毁默认子 Actor，再使用带自定义回调的 `CreateChildActor` 延迟创建；
在 Construction Script 之前调用官方零参数 `SetRandomOptions`，让身体、衣服、头发
和配饰按官方兼容规则组合。走路动画的起始时间和播放速率也做小范围随机化。

结果：最终严格复验的 30 人得到 30 种可见外观组合；近景截图所在运行批次为
29 种。

## 问题三：怎么证明不是悬浮或假地面

运行时只接受类名为 `CesiumGltfPrimitiveComponent` 的可见、可查询碰撞组件，
同时检查坡度与高度范围。精确 XY 如果落在摄影测量三角面或瓦片小缝隙，会在
18 cm 脚掌范围内尝试九点探测；命中的 Z 仍必须来自真实 Cesium 三角面，并写回
原实体 XY，不使用隐藏平面或手工固定高度。

可视人物碰撞全部关闭，因此人物本身不会被下一次地面射线误认为道路。最终验证
为 Cesium 命中 30/30、最大 Mass 根节点误差 2.135 cm、脚部检查 30/30。

## 问题四：怎么保证原通信 Demo 没被破坏

人群代码与脚本隔离在 `Plugins/OpenMassCrowd` 和 `Scripts/OpenMassCrowd`。布置
脚本只按明确的人群类/标签处理旧生成器，不匹配 `SIG_*` 通信 Actor。最终验证器
使用严格标签正则重新计数：

- `SIG_Source_00...29_Direct_Roof`：30 个；
- `SIG_Ray_*_(Segment|RoofHit)_*_(Green|Yellow|Orange|Red)`：1920 个，且
  1920 个都实际挂有可见 Static Mesh；
- Green、Yellow、Orange、Red：各 480 个。

## 资源如何接入

本机官方资源位置：
`D:\CitySampleCrowds_Staging\Content\CitySampleCrowd`。

审计结果为 1434 文件、6,505,136,712 bytes（6.058 GiB）、135 个 Skeletal Mesh、
4 个 Animation Blueprint；`BP_CrowdCharacter` 已在 UE 5.7.4 编译并实例化通过。

官方随机人物依赖大量软引用。硬依赖只有 79 个包，但硬+软依赖闭包为 1316 个包、
5.572 GiB，所以本 Demo 挂载完整官方目录，避免随机人物缺身体、衣服或材质：

```powershell
pwsh -ExecutionPolicy Bypass -File .\Scripts\OpenMassCrowd\link_city_sample_crowds.ps1 `
  -Source "D:\CitySampleCrowds_Staging\Content\CitySampleCrowd"
```

挂载点 `Content\CitySampleCrowd` 已进入 `.gitignore`。City Sample Crowds 是
UE-Only Content；协作者必须通过自己的 Epic/Fab 权限取得，不从公开 Git 分发。

## 稳定启动

早期 City Sample 首次编译曾遇到内存/分页文件压力，以及系统盘 Zen 缓存写满导致
HTTP 507。稳定启动脚本把 UserDir、DDC 和 Zen 放到 D 盘，并把纹理/资产编译并发
限制为 1：

```powershell
pwsh -ExecutionPolicy Bypass -File .\Scripts\OpenMassCrowd\launch_telecomtwin_citysample.ps1
```

最终验证从干净重启到工程可用约 20 秒；停播日志为 `BeginTearingDown`，当前运行
没有 Fatal、assert、OOM 或 HTTP 507。正常演示不需要 MCP 或 Python，打开后直接
点击“运行”。

## 为什么打开工程后看不到人

人物是 `BeginPlay` 时动态创建的 Mass 表示，不是编辑器关卡里常驻的 30 个
Character。绿色“运行”按钮表示 PIE 尚未运行，此时道路为空是正常的；停止 PIE
后人物也会被正常销毁。

正确操作：

1. 用安全启动脚本打开工程；
2. 等待香港/Cesium 画面出现；
3. 按 `Alt+P`；
4. 首次加载等待 8–15 秒；
5. 在输出日志确认 `OPEN_MASS_CROWD_READY requested=30 spawned=30`。

日志已显示 `spawned=30` 但仍看不到时，应检查相机：路线中心约为
`(-97000, 222400, 395)`，范围约 `X ±720 / Y ±250`。不要重新运行布置脚本，
关卡中唯一生成器已经保存。

## 验证脚本

运行时自动验证：

```powershell
python .\Scripts\OpenMassCrowd\run_unreal_python_via_mcp.py `
  --file .\Scripts\OpenMassCrowd\verify_open_mass_city_sample_runtime.py
```

验证器等待 30 个官方人物全部建立，再记录 6 秒前后位置、Cesium 命中、脚部偏移、
碰撞开关、动画状态、外观签名、唯一生成器和完整射线计数。它不会创建或重建关卡。

## 已知限制（必须保留）

1. 当前是局部有界路线，不是全城自动道路网络。
2. 30 人使用 Actor 可视表示。官方包审计没有 VAT/AnimToTexture 资产，当前高、
   低清模板还是同一个类，尚未实现数百/数千人需要的 ISM/VAT 远景 LOD。
3. 避让 Trait 已启用，但没有输出“最小人际距离”统计，不能把它表述为定量避碰
   基准。
4. 本轮确认稳定启动和 30 人运行，没有宣称固定 FPS、显存上限或大规模性能。
5. Git 不包含官方 6.058 GiB 素材；换电脑后必须先取得并挂载官方包。

## 回滚

接入 City Sample 官方素材前的回滚点：

```text
checkpoint/citysample-pre-assets-2026-07-15
0ad264252223654da99ac5400548d2524df3f4f7
```

当前关卡和代码位于 `experiment/citysample-crowds-ue57` 分支。
