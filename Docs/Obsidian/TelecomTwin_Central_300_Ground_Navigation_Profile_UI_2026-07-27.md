---
title: TelecomTwin 中环 300 人地面寻路、远景动画与人物档案
date: 2026-07-27
updated: 2026-07-27
project: TelecomTwin
status: verified
tags:
  - TelecomTwin
  - 中环
  - 300人群集
  - GroundOnly
  - VAT
  - 人物档案
branch: feature/central-crowd-experience
rollback_tag: checkpoint/central-crowd-experience-baseline-2026-07-27
---

# TelecomTwin 中环 300 人地面寻路、远景动画与人物档案

> [!success] 最终结论
> 用户提出的四项体验问题已经同时解决并通过当前 UE 5.7 PIE 运行验证：300 人全部进入运行和表现层，300 人都在移动，0 卡住、0 unsupported、0 严重重叠；远处 886.145 m 的人物仍在推进走路动画帧；点击人物可打开半透明档案；原有 30 个信源和 1920 条四色射线保持不变。

## 1. 这次只解决什么

本轮没有重做一个完整城市级人群产品，也没有引入已经停止维护的 Miarmy 二进制。范围严格对应四个可观察问题：

1. 人只走地面认证路线，不进入天桥、高架、楼梯或隧道来源；
2. 不再只在很短的一段路上机械折返，改为在真实连通分量内规划较长去程，再沿真实反向 lane 回程；
3. 远景人物继续播放步行动画，不能像棋子一样平移；
4. 点击人物后显示姓名、职业、常用软件和实时行程信息，界面采用右侧半透明玻璃风格。

## 2. 问题一：只在地面走

### 原因

Cesium 的“能碰撞”只能说明射线碰到了一个表面，不能说明这个表面语义上是地面。天桥和高架同样有碰撞，如果只向下投射，人物仍可能被放到天桥上。

### 解决方式

新增一个 fail-closed 的 `Ground-Only` 路网派生阶段。它从已经认证的中环路网读取来源信息，然后明确排除：

- `bridge=yes` 或 bridge/footbridge 来源；
- viaduct、高架、非零 `layer`；
- steps；
- tunnel；
- 人工复核拒绝的来源。

过滤结果写入独立缓存和 UE DataAsset。运行时生成、寻路和遥测只接受 `ground_only_eligible=true` 的 lane；旧版数据、哈希不一致或字段缺失都会拒绝启动，而不是回退到任意碰撞面。

### 数据结果

| 指标 | 结果 |
|---|---:|
| 原认证有向 lane | 818 |
| 排除有向 lane | 256 |
| Ground-Only 有向 lane | 562 |
| Ground-Only 总方向长度 | 2593.110 m |
| 真实连通分量 | 49 |
| 生成区域 | 6 |
| 目标人口 | 300 |
| 运行中 elevated-source lane | 0 |
| 运行中 unsupported 人物 | 0 |

缓存和策略都带 SHA-256，防止来源改变后继续误用旧结果：

- Policy：`6502a12cbec4bd33c631e1135e097d9cd70e444f0f25af0111658c124060ff44`
- Parent certified network：`d923fdc3305f4fc9d966477bf4c8dc783dbcc8eefe9f7df9ff2ccc9f753df3d5`
- Ground-Only canonical cache：`c886efc71ab829c06c85e3a5f77d954d03aa986d426f7ad784b30b97b5f8fe59`

![[../Evidence/OpenMassCrowd/Central300/central_300_final_grounded.png]]

> [!note] 边界
> 这里的“地面”是来源语义和认证样本共同约束的 Demo 路网，不代表已经自动识别了全香港每一块人行道。它解决的是本轮明确要求的“不要上天桥”，没有假装完成全城道路语义。

## 3. 问题二：走得更远并能往返

### 原因

旧候选版为了稳定，人物主要在一对 lane 上短距离循环。它能动，但观感像局部摆动，也没有真正利用已经认证的道路连通关系。

### 解决方式

现在每个人的路线由确定性、分量感知的图搜索生成：

1. 从人物当前 Ground-Only lane 出发；
2. 只在同一个真实连通分量内搜索；
3. 优先选择约 60–150 m 的去程目的地；
4. 若真实小分量达不到目标距离，选择该分量内最长可达路线，不伪造连接；
5. 保存完整去程 lane 序列；
6. 到达后使用每条 lane 的真实 reverse lane，按去程的精确逆序返回；
7. 回到起点后再规划下一次较长行程。

路线评分仍保留占用、最近访问历史、路口和冲突成本，避免 300 人总挑同一个终点。当前取样人物 `HK-C-138` 的往返计划为 106.921 m；长时间运行中已完成 374 个路径 leg，300 人都保持移动、0 卡住。

> [!important] 真实性选择
> 小连通分量不会为了“看起来更远”瞬移到另一条街，也不会在 Cesium 表面上画一条假的直线。真实图有多长，人物就最多走多长；这是比强制每个人都达到固定距离更诚实的处理。

## 4. 问题三：远处也有走路姿势

### 原因

300 个远景人物如果全部使用完整骨骼 Actor，成本很高；如果只移动实例位置而不更新动画帧，就会出现明显的“平移”。

### 解决方式

远景统一使用 City Sample 人物的 AnimToTexture / VAT 表现：

- 六组 City Sample 模块化人物外观；
- 每个身份保存确定性的起始相位、播放速度和帧范围；
- Mass 位置负责真实移动，实例自定义数据负责推进 Walk 帧；
- 相机很远时仍提交动画时间，不因距离把人物冻结成静态姿势；
- 不为每个人创建额外代理 Actor，因此 300 人仍适合 Demo 实时运行。

实时自动验证锁定当前最远可检查人物 `HK-C-132`：相机距离 886.145 m，2.0 秒内帧值从 20.466797 推进到 85.893555，帧增量 65.426758，`animation_active=true`。这直接证明远景不是静态模型随位置平移。

## 5. 问题四：点击人物显示半透明档案

### 人物资料

300 个稳定身份使用 `HK-C-001` 到 `HK-C-300`。姓名由 30 个姓与 10 个名字确定性组合，保证本 Demo 中 300 个姓名唯一；职业和常用软件也由稳定索引生成，重启后同一个 ID 不会随机换资料。

档案包含：

- ID 与姓名；
- 职业；
- 常用软件；
- 所属生成区域；
- 当前是去程还是回程；
- 当前计划往返距离。

### 点击实现

人物远景是 ISM/VAT，不是 300 个独立 Actor，所以不能依赖普通 Actor 点击事件。当前实现直接读取权威 Mass Transform，把每个人投影到屏幕空间，在鼠标点击附近选择最近的可见候选。这样同一个稳定身份在 VAT 表现下仍可选择，也不需要为 300 人额外生成碰撞代理。

选中后会在人物脚下绘制青色跟随标记，并在屏幕右侧打开 Slate 档案面板。面板是深色半透明底、青色描边与分区卡片，背景城市仍然可见；右上角关闭按钮已实际点击验证。

![[../Evidence/OpenMassCrowd/11_central_profile_glass_ui.png]]

图中实例：`HK-C-138 / 谢文轩 / 游戏开发者 / QGIS`，当前处于回程，往返 106.9 m。

### 使用方法

1. 打开 `/Game/Maps/shanghai` 并进入 PIE；
2. 等待 300 人生成完成；
3. 用鼠标左键点击一个人物；
4. 查看右侧半透明档案；
5. 点击档案右上角 `×` 关闭。

## 6. 同时验收结果

最终实时报告不是分别跑四套互不相干的场景，而是在同一个正在运行的中环 PIE 中同时检查：

| 检查 | 结果 |
|---|---|
| Ground-Only 300 人 | PASS：300 admitted / simulated / represented |
| 移动与往返 | PASS：300 moving，0 stuck，374 completed legs |
| 地面与间距 | PASS：0 unsupported，0 invalid，0 severe overlap |
| 远景 VAT 动画 | PASS：886.145 m，2 秒推进 65.426758 帧 |
| 点击资料卡 | PASS：字段完整，glass panel visible |
| 原电信场景回归 | PASS：30 信源，1920 射线，四色各 480 |

机器报告：

- [[../Evidence/OpenMassCrowd/central_crowd_experience_runtime_latest.json|四项体验与电信场景联合运行报告]]
- [[../Evidence/OpenMassCrowd/CentralCrowdExperience/ground_only_asset_latest.json|UE Ground-Only DataAsset 报告]]
- [[../Evidence/OpenMassCrowd/CentralCrowdExperience/excluded_files_latest.json|用户原有文件未变化报告]]

## 7. 主要实现位置

| 内容 | 路径 |
|---|---|
| Ground-Only 策略 | `Scripts/OpenMassCrowd/CentralNetwork/central_ground_only_policy.json` |
| Ground-Only 派生与验证 | `derive_central_ground_only_network.py`、`verify_central_ground_only_network.py` |
| Ground-Only 缓存 | `Scripts/OpenMassCrowd/CentralNetwork/Data/central_network_ground_only.json` |
| UE DataAsset | `Content/OpenMassCrowd/Central/DA_CentralNetwork_Certified.uasset` |
| 路线、VAT、点击与 UI | `Plugins/OpenMassCrowd/Source/OpenMassCrowd/Private/OpenMassCrowdSpawner.cpp` |
| 联合运行验证 | `Scripts/OpenMassCrowd/verify_central_crowd_experience_runtime.py` |

## 8. 测试、保护和回滚

已完成：

- UE 5.7 插件完整构建通过；
- Ground-Only 3 个 Python 单元测试通过；
- Ground-Only 缓存、DataAsset 和运行时联合验证通过；
- 7 个原有用户脏文件逐个比较大小与 SHA-256，全部未被本轮覆盖；
- OpenSpec strict validation 通过；
- 分支只提交本轮意图内文件。

本轮开始前已经建立并推送不可变回滚点：

```powershell
git fetch origin --tags
git switch -c rollback/central-crowd-experience checkpoint/central-crowd-experience-baseline-2026-07-27
```

当前实施分支：`feature/central-crowd-experience`。若只想查看最终版本，拉取该远程分支即可。

## 9. 仍然没有假装完成的事情

- 没有宣称这是一套覆盖全香港的人行道数字孪生；
- 没有使用 Miarmy 旧版 UE 5.2 DLL；
- 没有把 Cesium 任意碰撞面当作地面语义；
- 没有通过瞬移或跨连通分量直线来伪造长距离寻路；
- 没有把 300 个远景人物替换成 300 个高成本独立 Actor；
- 没有修改本轮开始前记录的 7 个用户文件。

这版定位是一个可以演示、可重启、可验证、可回滚的中环 300 人数字孪生 Demo。
