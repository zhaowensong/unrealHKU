---
title: TelecomTwin City Sample 30 人寻路 Demo 实施记录
date: 2026-07-18
tags:
  - TelecomTwin
  - UnrealEngine
  - MassEntity
  - ZoneGraph
  - CitySampleCrowds
  - Cesium
status: verified
---

# TelecomTwin City Sample 30 人寻路 Demo 实施记录

## 目标与边界

目标是在原有香港数字孪生场景中做出 30 个能够真正行走、选择路线并在局部会车时
进行避让的城市行人：

- UE 5.7 Mass Entity 负责导航、移动、steering 和 avoidance；
- Epic City Sample Crowds 负责成熟人物外观与走路动画；
- 路线和每一帧可见位置必须受到真实 Cesium 碰撞约束；
- 人物可根据相机距离切换 High/Low 表示，但切换后仍是同一个人；
- 工程冷重启后能再次运行，并保持既有通信信源与四色射线。

本轮交付是香港场景内的 **bounded 30 人 Demo**，不是全香港 NavMesh，不做全城
人行道路自动提取，也不重新修改已经完成的电信射线四项问题。

## 最终结果

三份最终报告均为 `overall_passed = true`：

- [[../Evidence/OpenMassCrowd/open_mass_city_sample_runtime_latest.json]]
- [[../Evidence/OpenMassCrowd/open_mass_city_navigation_runtime_latest.json]]
- [[../Evidence/OpenMassCrowd/open_mass_lod_transition_runtime_latest.json]]

### 人物与原项目保留

| 指标 | 最终值 |
|---|---:|
| Mass Entity / 活跃可视代理 | 30 / 30 |
| 可见 City Sample 人物 | 30 / 30 |
| 官方 `BP_CrowdCharacter` 子人物 | 30 / 30 |
| 播放走路动画 | 30 / 30 |
| 6.075 秒内移动超过 60 cm | 30 / 30 |
| 移动距离中位数 | 489.485 cm |
| 可见外观组合 | 30 |
| Cesium exact-XY 命中 | 30 / 30 |
| Mass 根节点相对目标的最大误差 | 0.001 cm |
| 原有信源 | 30 / 30 |
| 原有可见射线 | 1920 / 1920 |
| Green / Yellow / Orange / Red | 每色 480 |

### 寻路、避让与贴地

| 指标 | 最终值 |
|---|---:|
| 运行时网络 | 3×3、9 节点、22 条有向 lane |
| 移动人物 | 30 / 30 |
| 可能卡住 | 0 |
| 报告结束累计完成 A→B 行程 | 443 |
| 路线重规划 | 0 |
| 有意义转向人物 | 29 |
| 覆盖方向桶 | 8 / 8 |
| 地面验证 | 30 个身份 × 3 次，90/90 命中 Cesium |
| 地面投影失败 | 71 |
| 回到 lane 中线恢复 | 71 |
| last-valid 回滚 | 0 |
| 不可恢复 | 0 |
| 当前不受支撑的可视人物 | 0 |
| severe overlap（中心距离 <20 cm） | 0 个样本 |
| body overlap（中心距离 <60 cm） | 1 个样本 |
| 最小中心距离 | 37.928 cm |

1 个 body-overlap 样本必须诚实保留：当前结果说明 Mass avoidance 正常参与局部运动，
并且验证期内没有 severe overlap；它不是硬物理人物碰撞，也不能宣称绝对不会穿插。

### LOD 往返

自动化验证实际执行并通过：

```text
Low 30 → High 30 → Low 30
```

三个阶段都保留完全相同的 30 个 Mass seed。High 和 Low 是不同表示类，不是“同一
Actor 类换参数”：

- High：`AOpenMassCrowdCitySampleActor`；
- Low：`AOpenMassCrowdCitySampleLowResActor`，使用 skeletal low-res 策略；
- Low 不是 VAT/ISM。官方包中没有可直接接入的 VAT/AnimToTexture 资产。

## 最终截图

### 远相机：Low 30

![[../Evidence/OpenMassCrowd/08_lod_low_30.png]]

### 近相机：High 30

![[../Evidence/OpenMassCrowd/09_lod_high_30.png]]

### 再次拉远：返回 Low 30

![[../Evidence/OpenMassCrowd/10_lod_low_30_return.png]]

三张截图对应 LOD JSON 的三次观测。最终数量和身份连续性由 JSON 中的
`observed_sequence` 与 `mass_seeds` 验证，不只依赖肉眼判断。

## 实现一：独立 A→B 寻路

当前实现不让所有人沿预先布置的单一闭环运动。生成器在运行时建立 3×3、9 节点的
局部 ZoneGraph：

1. 逻辑网格最多有 12 条无向边、24 条有向 lane；
2. 每条边先经过 Cesium 地面与身体空间认证；
3. 障碍边会尝试保持端点和端点切线的平滑 dogleg；
4. 所有候选都失败才成对裁掉该无向边；
5. 只有 9 节点仍连通、且有向 lane 为 16–24 的偶数时才接受安全子图；
6. 本次最终网络为 22 条有向 lane；
7. 30 个 Mass Entity 分别选择起点/目的节点，调用官方 `FZoneGraphAStar` 求 A→B
   路线，到达后再选择下一目的地。

因此现在不是 30 人绕同一条圈。本轮报告结束时累计记录 443 次完成行程、8 个方向桶全部覆盖、
29 人出现有意义转向，且 30 人都沿 X/Y 两个轴产生位移。

## 实现二：只使用真实 exact-XY Cesium 支撑

当前地面查询不做邻域高度补点。规则是：

1. 每一个采样位置只接受 **同一 XY** 的碰撞高度，不复用其他 XY 的 Z；
2. 先比较全局 raw hit，取最近的第一阻挡物，再做可行走性判断；
3. 如果第一阻挡物是建筑立面、陡面或其他不合格表面，就拒绝该点，不能穿过它去
   接受后面的道路；
4. 有效地面必须是可见、可查询、坡度合格的
   `CesiumGltfPrimitiveComponent`；
5. 不使用隐藏代理平面或固定高度。

这套 global raw first-blocker policy 解决了“射线穿过建筑后仍把后方路面当落点”的
假贴地问题。

## 实现三：不是只检查一条中线

仅在 lane 中线打点不能证明一个有宽度的人可以通过。现在每条候选边包含：

- 中线、左边界、右边界三条纵向轨迹；
- 每条纵向轨迹按不超过 10 cm 的步距做 exact-XY 地面连续性检查；
- 中线到左右两侧再按不超过 10 cm 做横向采样；
- 约 30 cm 半径、约 164 cm 高度的行人胶囊；
- 普通 World capsule sweep 与 direct Cesium component capsule sweep 两层身体空间
  检查。

候选 lane 必须全部通过这些离散检测才可进入 ZoneGraph。这里的“通过”表示在既定
采样分辨率和胶囊尺寸下通过，不应写成每一毫米数学连续或对所有未来瓦片状态的证明。

## 实现四：运行时回中线与 last-valid 保护

建网时通过检测，不代表 steering 在路口一定严格贴着 lane 几何中心。人物转弯、避让
和会车时可能横向偏移，因此每次把 Mass 位置交给可视代理前还会执行运行时 exact
Cesium guard：

1. 先检查当前候选位置的 exact-XY 地面；
2. 失败时尝试投影回当前 lane 中线；
3. 中线仍失败时回滚到该 Entity 保存的 last-valid 变换；
4. 只有得到真实支撑的位置才显示人物。

最终报告中的 71 次失败全部在第 2 步恢复，所以中线恢复 71、last-valid 回滚 0、
不可恢复 0、悬空可视人物 0。last-valid 不是装饰字段；它是更差地形情况下的最后
保护，只是本轮没有触发。

## 实现五：Mass avoidance 的角色

Mass Entity 是唯一运动所有者，`MassMovement`、steering 与 avoidance 生成速度和
局部让行。City Sample 可视人物的 Primitive Collision 全部关闭，避免角色胶囊、
衣服或头发反过来污染 Cesium trace。

因此系统不是 Character-to-Character 的硬碰撞仿真。最终 37 个采样帧出现 1 个
body-overlap 样本、最小中心距离 37.928 cm，但没有小于 20 cm 的 severe overlap。
这与 Demo 的“群集寻路和局部避让”目标相符，同时也清楚界定了能力边界。

## 实现六：稳定外观与真实 High/Low 切换

每个 Mass 身份有一个稳定 seed。第一次构建官方 `BP_CrowdCharacter` 时，seed 决定
身体、衣服、头发、配饰、动画起点和播放速率；结果被缓存。High/Low 表示回收或
重建后重放同一份配置，不再次用全局随机数抽人。

最终 City 报告验证 30 种可见外观；LOD 报告又验证 Low→High→Low 三阶段的 seed
集合完全相同。这同时解决了“30 人长得一样”和“LOD 切换后突然换脸”两个问题。

## 为什么不使用全城 NavMesh

香港 Cesium 摄影测量模型由流式瓦片和复杂三角面组成。直接把整个城市当常规静态
NavMesh 构建对象，不适合作为这个 30 人 Demo 的最低风险实现。本轮选择局部
ZoneGraph + exact Cesium collision certification：它能在用户可见区域演示真实 A→B
寻路，并且每条边、每个运行时位置都有明确的碰撞依据。

这不代表已经获得全城步道语义。若下一阶段需要跨街区、红绿灯、人行道语义或动态
封路，应另行接入道路数据并扩展图网络和重规划，而不是把这个 9 节点 Demo 外推为
全香港导航系统。

## 资源接入

本机官方资源：`D:\CitySampleCrowds_Staging\Content\CitySampleCrowd`。

审计结果为 1434 文件、6,505,136,712 bytes（6.058 GiB）、135 个 Skeletal Mesh、
4 个 Animation Blueprint；`BP_CrowdCharacter` 已在 UE 5.7.4 编译并实例化通过。

首次挂载：

```powershell
pwsh -ExecutionPolicy Bypass -File .\Scripts\OpenMassCrowd\link_city_sample_crowds.ps1 `
  -Source "D:\CitySampleCrowds_Staging\Content\CitySampleCrowd"
```

挂载点 `Content\CitySampleCrowd` 已进入 `.gitignore`。Fab City Sample Crowds 属于
UE-Only Content；每位协作者必须通过自己的 Epic/Fab 授权取得，Git 不分发这
6.058 GiB 官方素材。

## 启动与重启

从 TelecomTwin 工程根目录运行：

```powershell
pwsh -ExecutionPolicy Bypass -File .\Scripts\OpenMassCrowd\launch_telecomtwin_citysample.ps1
```

启动器把 UserDir、DDC 和 Zen 缓存放到 D 盘，并限制资产编译并发。最终冷启动通常
本轮严格 first-blocker 冷启动约 44 秒，建议预留 40–50 秒。香港画面出现后按
`Alt+P`，再等待日志：

```text
OPEN_MASS_CROWD_READY requested=30 spawned=30
```

人物在 `BeginPlay` 时动态创建；编辑器未运行或停止 PIE 后看不到 30 个 Character
是正常生命周期，不要重新执行布置脚本。

## 验证命令

在 PIE 已运行、MCP Server 已启动时执行：

```powershell
python .\Scripts\OpenMassCrowd\run_unreal_python_via_mcp.py `
  --file .\Scripts\OpenMassCrowd\verify_open_mass_city_sample_runtime.py

python .\Scripts\OpenMassCrowd\run_unreal_python_via_mcp.py `
  --file .\Scripts\OpenMassCrowd\verify_open_mass_city_navigation_runtime.py
```

验证脚本只读运行时对象并写证据 JSON，不重建关卡。LOD 报告来自专门的相机距离
往返验证，并对应 `08`、`09`、`10` 三张截图。

## 已知限制（必须保留）

1. 当前是 9 节点局部 bounded demo，不是全香港 NavMesh 或全城人行道路网络。
2. 10 cm 级采样和胶囊 sweep 是离散认证，不是数学连续证明。
3. 路口 steering 转角依赖运行时 exact guard、中线恢复和 last-valid 回滚。
4. `route_replans = 0` 是本次稳定结果；还没有实现动态障碍或道路封闭后的全局重规划。
5. Mass avoidance 不是硬物理碰撞；报告有 1 个 body-overlap 样本，不能声称绝对
   无碰撞。
6. Low 是 skeletal low-res，不是 VAT/ISM；30 人结果不能直接外推到千人规模。
7. 编辑器帧率只是验证器的性能代理，不是发布版硬件基准。
8. 协作者必须自行取得 Fab UE-Only 素材授权。

## 纯 Git 回滚

本轮导航与碰撞改造开始前已经创建并推送 tag：

```text
checkpoint/pre-city-navigation-2026-07-18
```

需要比较或恢复时，不覆盖当前工作区，创建独立回滚分支：

```powershell
git fetch origin --tags
git switch -c rollback/pre-city-navigation checkpoint/pre-city-navigation-2026-07-18
```

该 tag 不包含 City Sample 官方资源、目录联接、DDC、Zen 或本机 UserDir。
