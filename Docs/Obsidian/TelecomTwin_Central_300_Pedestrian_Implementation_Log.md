---
title: TelecomTwin 中环 300 人群集 Demo 实施与验证记录
date: 2026-07-18
updated: 2026-07-25
tags:
  - TelecomTwin
  - UnrealEngine
  - MassEntity
  - CitySampleCrowds
  - Cesium
  - Central
  - 300人群集
status: runtime-candidate-verified
baseline_commit: 8995709926658414e695f8d804bd5c4d9c43c465
working_branch: feature/central-300-crowd
runtime_candidate_commit: 6e01ab2f6d73b84f37f116dc440b94c9948052e5
runtime_candidate_tag: checkpoint/central-300-runtime-candidate-2026-07-25
remote_branch: origin/feature/central-300-crowd
---

# TelecomTwin 中环 300 人群集 Demo 实施与验证记录

> [!important] 2026-07-25 结论
> Central-300 运行候选版已在一次全新编辑器启动中完成 60 秒运行并取得可见证据：
> 300 simulated、300 represented、300 moving、0 stuck、0 severe overlap、0 unsupported，
> P95 27.02 ms。这里的 `runtime-candidate-verified` 只表示本页所述运行候选版得到证据；
> 它不把 OpenSpec 中仍未满足的“四连通街块/八路口”拓扑门、30→100→200 顺序晋级、
> 两次冷重启和信号回归伪装成已完成。

## 0. 2026-07-25 可运行候选版

### 0.1 第一性原理目标

人物不是“放进场景就算完成”。本轮把验收拆成五个可测事实：

1. **存在**：必须恰好生成 300 个 Mass Entity；
2. **可见**：300 个实体都必须进入 High / Low / VAT 表示层；
3. **贴地**：每个出生点及运行中的抽检点必须由当前 Cesium first-blocker 命中支持；
4. **在走**：最近两秒有真实认证位置位移，连续五秒不动则独立记为 stuck；
5. **不互穿且可运行**：中心距不得低于 20 cm，60 秒 P95 必须低于 33 ms。

### 0.2 最终采用的运行策略

- 当前认证图有 68 个真实连通分量且绝大多数是树。任意 A* 目的地会把人送进同一死胡同，
  在没有伪造 connector 的前提下必然形成对向排队。因此当前候选版采用
  `certified_edge_circulation`：每人只在一条认证有向 lane 与其真实 reverse lane 之间循环。
- 每个 edge circulation 最多两人；同一 lane pair 共享速度和折返点比例，保持出生时的
  认证间距，不再出现快人追上慢人的确定性追尾。
- 300 个槽位按六区配额 `[51, 51, 47, 51, 50, 50]` 分批事务式提交。未来槽位只是提案，
  已提交前缀才是占用；原槽位失效时只从同一 district 的认证候选中选 live-safe reserve。
- 每个候选在出生前执行实时 Cesium exact-XY first-blocker、坡度、高差、55 cm 出生净空与
  相向 lane 排除；全体提交完成前人物保持冻结，完成后一次性释放。
- UE Mass 的短路径在很短 lane 的折返点偶尔会留下动作交接空档。候选版在距最终折返点
  5 cm 时预排反向路径；若一个健康采样仍无位移，只重发**当前认证 edge 的同一路径动作**，
  不换街、不瞬移、不放宽碰撞或地面规则。
- `moving` 采用最近 2 秒内有认证位置位移的活动口径，避免把转身的一秒误报为停止；
  `stuck` 仍严格是连续 5 秒无认证位移，两者没有合并。

### 0.3 本轮实测

| 指标 | 结果 | 门槛 |
|---|---:|---:|
| Simulated | 300 | 300 |
| Represented | 300 | 300 |
| Moving（2 秒活动窗口） | 300 | ≥285 |
| Stuck（连续 5 秒） | 0 | <6 |
| Severe overlap pairs | 0 | 0 |
| Unsupported visuals | 0 | 0 |
| Invalid position observations | 0 | 0 |
| 当前最小中心距 | 20.022 cm | ≥20 cm |
| 60 秒 P50 | 23.32 ms | 记录项 |
| 60 秒 P95 | 27.02 ms | <33 ms |
| Frame window | 59.989 s | 约 60 s |

机器报告：[[../Evidence/OpenMassCrowd/Central300/central_300_final_grounded.json]]

街面画面：

![[../Evidence/OpenMassCrowd/Central300/central_300_final_grounded.png]]

> [!note] 画面边界
> 人物落点来自真实 Cesium 碰撞；近距离建筑破面和低清纹理来自当前香港摄影测量瓦片，
> 不是人为隐藏平面或固定 Z。截图能证明人物在真实路面和表示层中，但不能把低清瓦片
> 质量说成人群逻辑问题。

### 0.4 启动方式与回滚

地图仍保存为安全的 Central30 gate；Central300 是本次 session override，不会在关闭时
把临时 Spawner 状态写进 `shanghai`。启动后、PIE 前运行：

```powershell
python Scripts/OpenMassCrowd/run_unreal_python_via_mcp.py --file `
  Scripts/OpenMassCrowd/set_open_mass_crowd_session_mode.py --script-arg=central300
```

然后按 `Alt+P`。回滚点仍是
`checkpoint/citysample-30-nav-complete-2026-07-18` → `8995709926658414e695f8d804bd5c4d9c43c465`；
本轮没有移动或重写该 tag。新的运行候选版另存为提交
`6e01ab2f6d73b84f37f116dc440b94c9948052e5` 和 annotated tag
`checkpoint/central-300-runtime-candidate-2026-07-25`，二者均已推送到
`origin/feature/central-300-crowd`，可独立回滚，不影响旧 30 人 checkpoint。

### 0.5 尚未完成、不得扩大表述的事项

- 当前是分布在六个 Central district 的真实 edge circulation demo，不是跨整座中环的
  任意街区寻路；OpenSpec 3.4 的主分量四街块/八路口条件仍未满足。
- 尚未依次补齐独立 Central30、100、200 的 60 秒 gate 报告。
- 尚未完成两次最终冷重启、local30 fallback 和 30 信源 / 1920 射线 / 四色各 480 的
  同版本回归。因此本页不把整个 OpenSpec change 标为完成。

## 1. 目标与不变边界

目标是在 TelecomTwin 的香港中环信号研究区域内，让 **300 个 Mass Entity** 分布在
六个真实城市步行 district 上，在各自认证连通分量内选择目的地、经过显式路口连接并沿
Cesium 地面行走。

本阶段必须同时满足：

- 不是把 300 人继续塞在原来的单个斑马线；
- 六个 district 各自内部连通；不宣称六区互相连通，也不为演示补造跨区 connector；
- XY 路线来自可审查的人行道、步行区和过街拓扑；
- Z、高度连续性、坡度和身体空间由真实 Cesium 碰撞认证；
- 不得用隐藏平面、固定 Z 或未命中建筑/道路表面的假贴地；
- 300 人仍由 Mass 负责移动，City Sample 只负责视觉表示；
- 原有 30 人局部 Demo 保留为可选择的回滚模式；
- 原有 30 个信源、1920 条射线和绿/黄/橙/红各 480 条不得改变。

明确不在本阶段内：全香港自动步道提取、车辆交通、红绿灯时序、室内导航，以及重新
修改已经完成的电信射线四项问题。

## 2. 回滚基线（已验证，允许复用）

稳定基线来自提交 `8995709926658414e695f8d804bd5c4d9c43c465`，对应
[[../../00-项目索引|香港 30 人群集 Demo 总索引]]。这一部分的截图和 JSON 只能证明
旧的 30 人 bounded demo，不能证明 Central-300。

| 基线项 | 已保存结论 |
|---|---:|
| Mass Entity / 可视人物 | 30 / 30 |
| 局部路网 | 9 节点、22 条有向 lane |
| 贴地 | 30/30，旧报告中无不受支撑可视人物 |
| LOD | Low 30 → High 30 → Low 30 |
| 信源 | 30 |
| 射线 | 1920 |
| Green / Yellow / Orange / Red | 每色 480 |

回滚点：`checkpoint/citysample-30-nav-complete-2026-07-18`。这是远程已确认的 annotated
tag（tag object `8e16ef3d...`），解引用到
`8995709926658414e695f8d804bd5c4d9c43c465`。回滚时应从 tag 新建分支，不得移动或
重写 tag。

### 30 人基线画面（不是 300 人证据）

![[../Evidence/OpenMassCrowd/04_city_sample_30_wide.png]]

![[../Evidence/OpenMassCrowd/06_city_sample_cesium_foot_grounding.png]]

## 3. 当前实施快照

下表只区分“已有实现/文件”和“已经得到 UE 运行证据”。代码或 OpenSpec 任务存在，
不等于运行验收通过。

| 模块 | 当前已有内容 | 当前证据等级 | 仍需验证 |
|---|---|---|---|
| 隔离与回滚 | Central/local 模式、独立分支、远程 annotated 30 人 tag | tag 解引用和远程存在已验证 | Central 模式冷启动后仍能切回 local |
| Central 源数据 | 六个网格单元、OSM 候选、手工审查入口、稳定 ID、schema/hash | host/静态检查 | 最终叠加图人工审查仍待完成 |
| 候选路网 | 229 个拆分要素、约 6052.533 m 候选源路径 | 候选数据，不是认证路网 | 不得写成“已认证 6 km” |
| Cesium 认证器 | coarse、exact-XY、三轨、坡度、连续性、胶囊、first-blocker、分层 OSM semantic recovery | 已产出 fail-closed final cache；generated connector 未进入运行拓扑 | 最终视觉审查及各人口 Gate 仍未完成 |
| 认证 cache | `6 cells / 476 nodes / 818 directed lanes / 68 components / 38250 ground samples / 46 portals`；认证有向 lane 共 `3.736215 km`，38 个 junction，目标人口 300 | cache verifier 12/12 机器检查通过；UE DataAsset 已导入 `/Game/OpenMassCrowd/Central/DA_CentralNetwork_Certified` | 机器通过不等于 Central Gate 通过；仍需人工画面和持续 PIE 证据 |
| 真实连通范围 | 六个 spawn district 各自完全位于一个真实认证连通分量内；全局 cache 仍有 68 个分量 | 数据资产/import audit 已记录 6 个 component-local district | 不伪造跨区连接；只有同一真实分量自然跨越 cell 边界时才允许 cross-cell 路由 |
| Cesium 流送 | `shanghai` 地图的 `CesiumCameraManager.AdditionalCameras` 已持久化六个 district camera，并得到真实 `CesiumGltfPrimitiveComponent` exact-XY 命中 | 地图外部 actor 与 live trace 证据 | 最终两次冷重启仍须证明无需修复脚本即可恢复 |
| 任意图运行时 | cache 驱动 ZoneGraph、BV/邻接和分量内 A*；不把 68 个真实分量改写成一张假图；4179 个认证轨迹冲突由 2913 个局部 interval resources 与 159 个 expanded-overlap clusters 管理，并以 route-chain 连续原子预约、最终同向 headway 和每帧全体 pair scan 约束 | UHT、Development Editor Win64 UBT、最终 DLL link 与独立离线覆盖审计 PASS；uncovered=0 | 代码/容量阻塞已解除；Gate30 仍须 schema-v4 60 秒完整会话证明 0 overlap、0 invalid position 和最大冲突等待 ≤2 s |
| 人口调度 | 30/100/200/300 gate、完整 300 槽位与稳定前缀；完整 Gate300 六区配额 `[51,51,47,51,50,50]`；Central 半径 27 cm、目标中心间距 55 cm；动态 admission 每批最多 25 人/0.1 秒并按当前 Mass Transform 重验净空 | 离线 300-slot 精确重放 PASS：全局最小中心距 `59.3277447 cm`、opposing slot pairs=0；UHT/UBT/DLL PASS | 每个 gate 的持续运行人数、移动、覆盖与完整会话零严重重叠报告 |
| City Sample VAT | 6 个 FTN/FTO/FTU/MTN/MTO/MTU modular assemblies；24 个真实部件；144 个精确生成资产 | UE 5.7 build + strict verifier 37/37 PASS；OpenSpec 5.1 完成 | 运行时三层 LOD、远景动画和身份连续仍待 5.2/5.3 验收 |
| 运行时地面保护 | 50 m component 网格、1 s 共享刷新、每 tick 最多 24 人错峰 guard；PrePhysics 中在 Movement 后、Visualization/ISM 前统一约束认证 transform；last-live 回滚及 cell unavailable/replan | UHT/C++/最终 DLL link PASS | Gate30 运行验证 query 数、回滚和 unsupported=0 |
| 300 人表示 LOD | 计划 24 High、72 actor-Low、其余 VAT/ISM；Central owner/index fragment 与 pre-visual transform processor 已编译链接 | 实现/构建证据，不是 LOD 运行验收 | 身份连续、外观多样、远景动画和表示数量 |
| 性能 | 目标为 300 人 60 秒、P95 < 33 ms | 未运行 | 100、200、300 分级报告 |
| 重启 | 新 DLL 编译后已做一次开发性编辑器重载，插件和 `shanghai` 地图成功加载 | 仅证明新二进制可加载 | 两次正式冷重启均须在最终 cache/300 人就绪后执行并自动恢复，无 setup repair |
| 信号回归 | 30 人 checkpoint 已有 30/1920/四色基线 | Central 未回归 | Central-300 同屏/同次运行的新 JSON 与截图 |
| 本机运行存储 | 项目 `Intermediate` junction 指向 `D:\TelecomTwinGenerated\Intermediate`；UserDir、DDC、Zen、TEMP/TMP 分别使用 `D:\TelecomTwinCitySampleUser`、`D:\TelecomTwinCache\DDC`、`D:\TelecomTwinCache\Zen`、`D:\TelecomTwinTemp` | 路径、junction 和启动参数已核对 | 这是 C 盘空间保护，不是任何 Gate 的通过证据 |

### 当前必须保留的阻塞说明

1. Final certified cache 已存在并通过机器 verifier，但视觉审查和 PIE 持续运行验收尚未
   完成。它包含 6 cells、476 nodes、818 条有向 lane、3.736215 km、38 junctions、38250
   ground samples、46 portals 和 68 个真实连通分量；这些统计不能写成“一张全连通图”。
2. 六个 spawn district 各自在一个真实认证分量内连通。断点没有用 generated connector
   补齐；跨区不可达是当前真实数据边界，不得为了画面伪造跨区路线。旧的 19 条 OSM 边 /
   253.608 m group-Steiner 缺口分析保留为为何放弃假连线的历史依据。
3. 原 manifest 的 `*_base` 曾被严格 verifier 证明是三顶点动画驱动代理（3 vertices /
   1 triangle / 1 UV），不是人体。该失败方案已经替换：新 schema v2 为每个外观烘焙
   Torso、Legs、Shoes、Head 四个官方部件，共 6 assemblies / 24 parts / 144 assets；
   37/37 strict checks PASS，所有部件均为真实几何、含 UV2、帧范围同步，且无 Mannequin
   或 `*_base` 输出依赖。OpenSpec 5.1 已完成；运行时 LOD 仍须通过 5.2/5.3。
4. Central 30 人 gate、100/200/300 gate、60 秒性能、两次冷重启和最终信号回归均未完成。
   04:28 生成的 Gate30 JSON 虽然在 15.169 秒采样窗内写出 `overall_passed=true`，但 PIE
   继续运行后于 04:29:01 在 lane 396 / 765 的共享节点记录到 `6.403 cm` severe overlap；
   因此该报告已作废，不能作为 Gate30 通过证据。
5. 首轮 Gate30 运行揭示同 lane 追尾和相距仅数厘米的近重复 lane 占用同一物理路面。
   04:23 的第二轮 DLL 加入了 lane/progress/物理 track 诊断；04:31 构建又明确把真正的
   ReverseLane pair 排除在同向近重复 lane 等价组之外，plugin DLL 编译成功。该修订尚未
   获得新的持续运行 Gate 证据。
6. City Sample VAT manifest 已升级为 modular assembly schema v2：每个 FTN/FTO/FTU/
   MTN/MTO/MTU 变体由 Torso、Legs、Shoes、Head 四个官方部件组成，所有部件共用同一
   动画相位和帧区间。UE 5.7 build 和 read-only strict verifier 已证明精确 144 个资产、
   24 个真实几何部件、UV2/材质/动画数据以及无 Mannequin/`*_base` 代理，37/37 PASS；
   这里只完成 OpenSpec 5.1，5.2/5.3 的运行时三层 LOD 和身份连续仍未验证。
7. `UOpenMassCrowdCertifiedTransformProcessor` 位于 `PrePhysics`，明确排在 Mass Movement
   之后、`UMassCrowdVisualizationProcessor` 与 `UMassUpdateISMProcessor` 之前。它让
   actor 与 VAT/ISM 在同一帧读取已经约束到完整 10 cm 认证 lane polyline 的 transform。
   离线 cache 只提供可视 lane transform；只有 live exact-XY Cesium 成功才更新
   last-live 回滚点，cell unavailable 或恢复中会还原到该点。Local30 的 owner fragment
   为空，因此不进入 Central 专用路径。此项只有构建证据，unsupported=0 仍须 Gate30。
8. Central admission 在建立 LastValid 前先做 live exact-XY 点探针；每批最多 25 人/0.1 秒，
   出生前按当前 Mass Transform 重验 ≥55 cm 净空，可换用后续安全槽位，整批不足时 defer；
   每批出生后立即执行 pair scan。任何失败仍不静默切换 local route。离线 300-slot 计划已
   通过容量检查，但动态 admission 仍须由各人口 Gate 证明。
9. 共享节点约束已扩展为全量 55 cm 局部 interval resources、expanded-overlap closure 与
   route-chain 连续原子预约；离线审计为 4179 strict pairs / 2913 resources / 159 clusters /
   uncovered=0，最终 UHT/UBT/DLL link 通过。这只解除代码和容量阻塞；在新的 schema-v4
   60 秒完整会话报告产生前，Gate30 仍保持“未验证”。

## 4. Central 验证门

只有上一门完整通过后才允许增加人数。

| Gate | 必须记录 | 状态 | 证据链接 |
|---|---|---|---|
| Central cache | 6 cells、6 个 component-local district、68 个真实分量、38 junctions、3.736215 km certified directional lanes、真实 Cesium 命中 | 未验证 | 机器 cache/import 报告已有；人工 Gate 证据仍为 TBD |
| Central 30 | 30 simulated/represented/moving、分量内真实路由、0 unsupported、持续观察无 severe overlap | 未验证 | 04:28 的 15 秒报告因后续 6.403 cm overlap 已作废 |
| Central 100 | 数量、移动、卡住、地面、覆盖、重规划、帧时间 | 未验证 | TBD |
| Central 200 | 同上，并比较 100 gate 的退化 | 未验证 | TBD |
| Central 300 | 60 秒；300 simulated/represented；≥95% moving；<2% stuck；0 unsupported；P95 <33 ms | 未验证 | TBD |
| LOD/VAT | 24 High、72 actor-Low、remainder VAT/ISM；身份稳定；远景有动画 | 未验证 | TBD |
| 回归 | local 30 fallback；30 sources；1920 rays；四色各 480 | 未验证 | TBD |
| 重启 | 两次关闭/重开后，无人工修复地恢复 Central、人物、地面和射线 | 未验证 | TBD |

## 5. 可复用截图盘点

以下文件已经在 vault 中，可用于说明旧基线或技术沿革；不得把它们重复标成
Central-300 最终截图。

| 已有截图 | 可复用用途 | 不能证明 |
|---|---|---|
| [[../Evidence/OpenMassCrowd/04_city_sample_30_wide.png]] | 30 人整体运行基线 | 中环 300 人、多街区 |
| [[../Evidence/OpenMassCrowd/05_city_sample_29_variants_close.png]] | City Sample 外观多样性辅助画面 | 最终 30/300 数量；文件本身只有 29 种批次 |
| [[../Evidence/OpenMassCrowd/06_city_sample_cesium_foot_grounding.png]] | 原局部路线脚部贴地近景 | Central 多路段认证 |
| [[../Evidence/OpenMassCrowd/07_city_sample_restart_after_6s.png]] | 旧 30 人干净重启基线 | Central 的两次重启 |
| [[../Evidence/OpenMassCrowd/08_lod_low_30.png]] | 旧 Low 30 阶段 | VAT/ISM 或 300 人预算 |
| [[../Evidence/OpenMassCrowd/09_lod_high_30.png]] | 旧 High 30 阶段 | 新 24 High 预算 |
| [[../Evidence/OpenMassCrowd/10_lod_low_30_return.png]] | 旧 LOD 往返及射线同屏 | 新三层 LOD 和 Central 回归 |
| [[../../../attachments/01-green-intensity.jpg]] 至 [[../../../attachments/07-restart-counts-30-sources.jpg]] | 已解决四项信道问题及 30 信源历史基线 | Central-300 版本没有破坏信号 |

下列截图只保留开发历史，不应进入 Central-300 验收证据：

- `01_mass_crowd_running.jpg`、`02_mass_crowd_after_6s.jpg`、`03_restart_smoke.jpg`：
  BattleWizard 临时视觉阶段；
- `HongKongStreetCrowd-runtime.png`：更早的香港人群原型；
- `MassNavMesh-UE57-runtime.png`：灰盒 NavMesh 调研场景，不是 TelecomTwin 中环。

> [!note] Vault 快照与当前工作树
> `04`–`07` 与项目副本目前相同；vault 中的 `08`–`10` 和三份 JSON 是提交
> `89957099` 的冻结证据，当前项目工作树里的同名文件已被后续试跑改写。Central
> 最终证据应使用新的 `central_*` 文件名，不能覆盖这份基线快照。

## 6. 缺失截图清单

建议将未来新证据保存到独立的 `Central300` 证据目录，并采用不会覆盖旧基线的名称。

- [ ] `central_01_network_overlay.png`：中环 450 m × 300 m 范围、六个 cell、接受/
  拒绝路线、crossing、portal 的可读叠加图。
- [ ] `central_02_cesium_certified_hits.png`：实际 Cesium primitive 命中、三轨和
  first-blocker 认证，而不是 dry-run 或平面替代物。
- [ ] `central_03_30_gate_wide.png`：Central 30 人 gate，人物分布在多个街段。
- [ ] `central_04_30_gate_street_grounding.png`：街面近景，脚部落点与真实路面相符。
- [ ] `central_05_district_routing_and_crossing.png`：人物在所属认证分量内经过真实 portal/
  路口；仅在同一认证分量自然跨越 cell 边界时记录 cross-cell，不宣称六区互相连通。
- [ ] `central_06_100_gate.png`：100 人分布与 telemetry。
- [ ] `central_07_200_gate.png`：200 人分布与 telemetry。
- [ ] `central_08_300_wide.png`：300 人中环广角，覆盖多个街区。
- [ ] `central_09_300_street.png`：300 gate 街面视角，显示行走、避让和贴地。
- [ ] `central_10_lod_high.png`：近景 High actor 预算与身份。
- [ ] `central_11_lod_low.png`：中景 actor-Low 预算与身份。
- [ ] `central_12_lod_vat.png`：远景 VAT/ISM 人物可见且动画实际播放。
- [ ] `central_13_telemetry_60s.png`：300 simulated/represented、moving/stuck/
  unsupported、P95 frame time 等最终统计。
- [ ] `central_14_signal_regression.png`：Central 人群与原有四色射线同屏，并配套
  30/1920/480×4 JSON。
- [ ] `central_15_restart_1.png`：第一次干净重启后的 Central 300 人与射线。
- [ ] `central_16_restart_2.png`：第二次干净重启后的相同结果。

截图不能代替机器报告。人数、移动比例、悬空数量、帧时间和射线数量必须同时由 JSON
验证；JSON 也不能代替路线叠加图和街面视觉检查。

## 7. 待生成的机器证据

- [ ] Central cache manifest、hash、每 cell 认证与拒绝统计；
- [ ] cache 覆盖、68 个真实分量及六个 component-local district verifier 记录；
- [ ] 30、100、200、300 四个 gate 的独立 JSON；
- [ ] 300 人 60 秒 frame-time 分布与 P95；
- [ ] High/Low/VAT 表示数、Mass seed 和外观稳定性报告；
- [ ] 每人 staggered ground guard、last-certified 回退、unsupported 统计；
- [ ] local 30 fallback 回归 JSON；
- [ ] 信号 30 sources / 1920 geometries / 480×4 colors 回归 JSON；
- [ ] 两次冷重启的启动日志、运行报告和时间戳；
- [ ] 最终 commit、远程分支和保留 tag 的记录。

## 8. 实施日志

后续每次推进只追加一行；没有机器或截图证据时，状态不得填写“通过”。

| 日期 | 变更 | 验证方法 | 结果 | 证据 |
|---|---|---|---|---|
| 2026-07-18 | 建立 Central-300 OpenSpec、分支和回滚隔离 | tag/branch/文件检查 | 已建立实施入口；非运行验收 | TBD |
| 2026-07-18 | 导入候选路网、schema、overlay、cache verifier | host self-test / schema test | 候选及工具可用；Central live cache 未验证 | TBD |
| 2026-07-18 | 编写 Cesium 认证、任意图、分区人口和 A* 相关实现 | 静态/host 检查 | UE live 结果待补 | TBD |
| 2026-07-18 | 生成 6 个 City Sample VAT 变体并定位软引用问题 | build report / verifier | verifier 尚未 PASS | TBD |
| 2026-07-19 | 修复 UnrealMCP 64 KiB 接收边界及大脚本 bootstrap，恢复长认证脚本稳定执行 | plugin full build / 大脚本 MCP 运行 | PASS；不再因 102 KiB certifier 崩溃 | 项目源码与 UE log |
| 2026-07-19 | 清除 OneDrive 恢复到 JSON 检查点上的只读位，并保留有限原子写重试 | 7 个 host tests / 1029→1444 live resume | PASS；检查点可恢复且仍 fail-closed | `test_cesium_certifier.py` |
| 2026-07-19 | 三轮真实 Cesium 认证 | live direct component trace / work audit | 1444 resolved；240 accepted；1786.68 m；54 components；尚未通过连通门 | `central_network_certification_working.json` |
| 2026-07-19 | 运行时 grounding P0：空间网格、错峰 guard、last-certified、cell replan、跨 cell lane fallback | UHT / C++ compile / full link | 构建 PASS；Gate30 live 待验 | `OpenMassCrowdSpawner.h/.cpp` |
| 2026-07-19 | 严格检查 VAT 实际几何 | UE build + read-only verifier | FAIL（诚实阻断）：每个旧输出仅 3 vertices / 1 triangle | `city_sample_vat_verify_latest.json` |
| 2026-07-19 | 对 54 个认证分量求六 cell 最小连接 cut | 收缩 accepted graph + 穷举 group-Steiner DP | 19 条 rejected OSM 边 / 253.608 m；确认必须支持天桥、楼梯和 layer 1/2，不能直线补线 | `central_network_certification_working.json` + 分析记录 |
| 2026-07-19 | 设计 City Sample modular VAT schema v2 | manifest/build/verifier host 检查（UE 严格验证进行中） | 6 variants × 4 parts；女 0..97 帧，男 0..98 帧；尚未完成运行验收 | `city_sample_vat_manifest.json` |
| 2026-07-19 | 在 UE 5.7 重烘焙 City Sample modular VAT，并清理旧三角代理 | source audit + UE build + strict read-only verifier | PASS：37/37；6 assemblies / 24 real parts / 144 exact assets；OpenSpec 5.1 完成 | [[../Evidence/OpenMassCrowd/Central300/city_sample_vat_build_latest.json]]、[[../Evidence/OpenMassCrowd/Central300/city_sample_vat_verify_latest.json]] |
| 2026-07-19 | 三轮 OSM semantic recovery 真实 Cesium 认证 | 539 + 13 + 3 个分层子段；完整链选择；每轮 final fail-closed | 1999 resolved / 414 accepted / 223 trusted；68 trusted components；six-cell=0；未生成 final cache，继续诊断真实断点 | `central_network_certification_working.json` |
| 2026-07-19 | 固化 annotated 30 人回滚点 | 本地 tag object、解引用 commit、远程 tag 检查 | PASS：`8e16ef3d...` → `89957099...`；tag 保持不可变 | Git refs |
| 2026-07-19 | 增加 Central pre-visual 认证 transform 处理器及 live/cache 状态分离 | UHT、C++ compile、最终 plugin DLL link；编辑器重启加载插件/地图 | 构建与加载 PASS；PIE Gate30/unsupported=0 尚未验证 | `OpenMassCrowdVisualization.*`、`OpenMassCrowdTrait.*`、`OpenMassCrowdSpawner.*`、UE log |
| 2026-07-19 | 增加 Central admission exact-XY 初始探针与有限重试 | 代码审计 + full link | 首次/批次失败销毁部分人口；1 s × 最多 60；只从 cache 重建；无 local fallback/全区 recertification；PIE 尚待验 | `OpenMassCrowdSpawner.cpp` |
| 2026-07-19 | semantic recovery round 4 安全恢复 | 3 个原 OSM ground-sidewalk candidates；±50 cm；26 strict subsegments / 8 directional chains；14/14 unit tests + cache-verifier self-test | 15 segment accepted / 11 rejected，但 8 条链全部不完整；trusted graph 仍 223/68、six-cell=0；generated connector contribution=0；fail-closed，无 final cache | `central_network_certification_working.json`、`audit_central_certification_work.py` |
| 2026-07-19 | 新 C++/plugin 二进制开发性重载 | full UHT/C++/DLL link 后关闭并重开编辑器；检查启动 log | `OpenMassCrowd`/`UnrealMCP` 已加载，`/Game/Maps/shanghai` 已打开；不计入最终两次冷重启门 | UE build output、`D:\TelecomTwinCitySampleUser\Saved\Logs\TelecomTwin.log` |
| 2026-07-19 | 生成并导入 fail-closed final Central cache | cache verifier + UE import audit | 机器证据记录 6 cells / 476 nodes / 818 lanes / 3.736215 km / 38 junctions / 38250 samples / 46 portals / 68 components / 6 component-local districts；不伪造跨区连接；不计为 Gate 通过 | `central_network_certified.json`、`central_network_certified.pending.verification.json`、`central_network_asset_import.audit.json` |
| 2026-07-19 | 将六个 district Cesium streaming cameras 持久化到 `shanghai` | `CesiumCameraManager.AdditionalCameras`、地图外部 actor、live exact-XY trace | 六个 camera 已保存并取得真实 `CesiumGltfPrimitiveComponent` 命中；最终两次冷重启仍待验 | `register_central_cesium_streaming_cameras.py`、地图 external actor、UE log |
| 2026-07-19 | 将构建与运行缓存迁移到 D 盘 | junction/目录/启动参数检查 | `Intermediate` → `D:\TelecomTwinGenerated\Intermediate`；UserDir/DDC/Zen/TEMP 分别位于 `D:\TelecomTwinCitySampleUser`、`D:\TelecomTwinCache\DDC`、`D:\TelecomTwinCache\Zen`、`D:\TelecomTwinTemp`；仅为空间保护 | `launch_telecomtwin_citysample.ps1`、文件系统 junction |
| 2026-07-19 | 首轮 Gate30 重叠诊断 | PIE overlap telemetry、entity lane/progress 日志 | 发现同 lane 速度追尾及近重复 lane 占用同一物理路面；Gate 未通过 | `D:\TelecomTwinCitySampleUser\Saved\Logs\TelecomTwin.log` |
| 2026-07-19 04:23 | 第二轮 overlap 诊断 DLL | full plugin build + PIE lane/progress/physical-track telemetry | 诊断构建完成，用于区分同 lane、近重复 lane 与共享端点；不是验收证据 | `OpenMassCrowdSpawner.*`、`OpenMassCrowdVisualization.*`、build/log |
| 2026-07-19 04:28–04:29 | Gate30 15 秒窗口及窗口后持续观察 | schema v3 verifier + 持续 PIE log | 15.169 秒报告写出 `overall_passed=true`；继续运行后 04:29:01 在 lane 396 / 765 共享节点出现 6.403 cm severe overlap，故该报告作废，Gate30 保持未验证 | `central_crowd_gate_runtime_gate30_20260718T202842Z.json`、UE log 中 `OPEN_MASS_CROWD_SEVERE_OVERLAP` |
| 2026-07-19 04:31 | 修正近重复物理 track 的 ReverseLane 判定 | full plugin DLL build | 明确排除真正的 ReverseLane pair；`UnrealEditor-OpenMassCrowd.dll` 04:31:07 编译成功；尚无新 Gate 运行结论 | `OpenMassCrowdSpawner.cpp`、plugin DLL |
| 2026-07-19 | 实现共享节点 55 cm 冲突区 | 进行中 | 针对不同 lane 在同一节点汇合的占用冲突；完成编译和持续 PIE 验证前不改变任何 Gate 状态 | `OpenMassCrowdVisualization.*`、后续 UE log |
| 2026-07-19 04:47 | 增加节点预约、直接同向邻接、相向方向锁、排空后换向和持久等待公平排序 | UHT / Development Editor Win64 UBT / 精确离线计数 | 构建 PASS；日志模型得到 520 对相向候选、314 条受影响 lane、64 个走廊，但尚未进入正式 Gate；后续独立审查发现其覆盖模型仍不完整 | `OpenMassCrowdSpawner.*`、`OpenMassCrowdVisualization.*` |
| 2026-07-19 04:54 | 修复最终速度 cap 之后的同向 headway 收敛 | 独立代码审查、UHT、UBT、DLL hash | PASS：最终 pass 不再假设 leader 本帧会兑现未来位移；DLL 730624 bytes，SHA-256 `04370BAC4C49B511A2D8E078520CC3191A502C3D3B15B9B4D26566091AF3F5FA`；仍不是运行验收 | `OpenMassCrowdVisualization.cpp` |
| 2026-07-19 04:56 | 将四级 Gate 与晋级契约统一提升为 60 秒 | host self-test / 旧 schema-v3 15 秒报告拒绝测试 | PASS：30/100/200/300 均要求 game、wall、required sampling 不少于 60 秒；旧 15.169 秒诊断报告不能再晋级 | `verify_central_crowd_gate_runtime.py`、`promote_central_crowd_gate.py` |
| 2026-07-19 04:57 | 正式 Gate 前的全量认证轨迹冲突审计 | 818 lanes / 38250 RightTrack samples；任意局部 sample-pair `<55 cm`；复刻当前 whole-track matcher | 诚实阻断：4179 对存在局部冲突，旧模型仅覆盖 1072 对，漏 3107 对；漏项含同向 shared/nonshared 744/496、相向 shared/nonshared 717/501、declared reverse 375、交叉 shared/nonshared 272/2；必须改为局部冲突区间/资源后才能跑 Gate | certified JSON、独立只读审计、`OpenMassCrowdSpawner.cpp` |
| 2026-07-19 04:58 | 审计 Central 分批 admission 的动态出生净空 | 25 人/0.1 秒 × 12 批状态机检查 | 诚实阻断：静态 300-slot 互距不能证明后批出生时槽位未被先批移动实体占用；要求每批出生前按当前 Mass Transform 做 `>=55 cm` 净空并延迟/换槽，或采用等价安全方案 | `AdmitNextCentralBatch`、`InitializeCentralEntity` |
| 2026-07-19 | 将碰撞验收提升为完整 PIE 会话硬约束（schema v4） | verifier/promotion self-test、独立审计 | PASS（工具级）：不再用 steady-window baseline 掩盖早期违规；current、lifetime peak、完整会话 observation、采样峰值均须为 0，当前和 lifetime 最小中心距均须 `>=54.5 cm`（仅 0.5 cm 数值容差）；C++ 每批出生后即时 pair scan 与局部冲突资源实现仍在进行，故 Gate 状态不变 | `verify_central_crowd_gate_runtime.py`、`promote_central_crowd_gate.py` |
| 2026-07-19 | 增加 Central300 最终截图证据套件 | `py_compile`、host `--self-test`、AST/CLI/diff 检查 | PASS（工具级）：可串行生成 wide/street/route-overlay/crossing/High/Low/VAT/telemetry/signal-regression 9 组 PNG+JSON；临时隐藏信号后逐 actor 验证恢复；截图明确不能替代 schema-v4 60 秒 Gate；尚未运行 UE，故未生成最终证据 | `capture_central_crowd_evidence.py` |
| 2026-07-19 | 55 cm 局部资源与 route-chain 预约的首次完整编译 | 强制 UHT / Development Editor Win64 UBT / DLL hash / 独立离线图审计 | 编译 PASS，但运行前容量 FAIL：2913 个局部资源与 expanded-interval 出生过滤后，`district-central-r0-c0` 虽有 1125 个剩余候选，现有 farthest/opposing 约束最多只能选 27/50；因此未启动 UE、Gate30 状态不变，正在调整安全候选与分批方向策略 | `OpenMassCrowdSpawner.*`、`OpenMassCrowdVisualization.*`、离线 certified JSON 审计 |
| 2026-07-19 | 将旧全局 Unreal Zen 生成缓存迁移到 D 盘并保留 junction | 绝对路径、junction target、字节数和盘符余量检查 | PASS：`C:\Users\15958\AppData\Local\UnrealEngine\Common\Zen` → `D:\TelecomTwinCache\LegacyGlobalZen`，1.979 GiB；迁移后 C 盘可用 6.540 GiB；仅为空间保护、可逆、不计功能验收 | 文件系统 junction |
| 2026-07-19 06:01 | 解除 55 cm 全量冲突覆盖与 300 槽位容量阻塞 | 强制 UHT / Development Editor Win64 UBT / 最终 DLL link；818 lanes / 38250 samples 全量离线冲突审计；300-slot 精确重放；独立代码审查 | 构建/静态 PASS：4179 个 strict conflict pairs → 2913 个 runtime interval resources（2732 strict + 181 guard），159 个 expanded-overlap clusters，uncovered=0；完整 Gate300 六区配额 `[51,51,47,51,50,50]`，候选池 `[6667,5801,547,909,3988,1612]`，全局最小中心距 `59.3277447 cm`，opposing slot pairs=0；动态 admission 每批最多 25 人/0.1 秒并重验当前 Mass Transform，300 人时每帧全量扫描 44850 对；Gate30 运行状态仍未验证 | `OpenMassCrowdSpawner.*`、`OpenMassCrowdVisualization.*`；`UnrealEditor-OpenMassCrowd.dll` 816640 bytes，SHA-256 `E2D0B6228FBD1CE7AB9FA2CB5C2A9528C78B22668AC3D472A52C95F755DB8413` |
| 2026-07-19 06:07 | schema-v4 Gate30 前置运行首次尝试 | PIE live exact-XY admission、60 次 fail-closed 重试、UE 日志 | FAIL（未启动 verifier、未生成新报告）：静态计划仍为 30/300 槽位且最小净空 59.328 cm，但 live admission 依次在 entity 2 / lane 284、entity 3 / lane 172、entity 22 / lane 256 发生 `CENTRAL_ADMISSION_GROUND_REJECT`；60/60 重试后自动中止，Gate30 保持未验证。当前正在核对扩展为 cell geographic pools 后六个 AdditionalCameras 是否仍只覆盖旧 seed component；不得以旧 schema-v3 报告替代 | `D:\TelecomTwinCitySampleUser\Saved\Logs\TelecomTwin.log`、`register_central_cesium_streaming_cameras.py` |
| 2026-07-19 06:15 | 将六个 Cesium streaming cameras 从旧 seed component 扩展到完整认证 spawn cell | MCP editor 脚本、38250 个 RightTrack sample 分区聚合、地图保存、tileset load progress | PASS（覆盖工具级）：六区 camera 分别覆盖 11174/11723/2773/1680/8839/2061 个认证 sample，总计 38250，输出 schema 2、`coverage_mode=complete_certified_spawn_cells`，tileset load progress=100%；这只证明流送覆盖配置，不能替代 Gate | `register_central_cesium_streaming_cameras.py`、`Content/Maps/shanghai.umap`、地图 external actor、UE log |
| 2026-07-19 06:18 | 消除信号可视化几何对行人地面探针的碰撞污染 | exact-name actor/component audit、首次修复、Save All、二次幂等复验 | PASS（信号回归前置）：精确识别 30 个 `SIG_Source_*` 与 1920 个 `SIG_Ray_*`；首次将 1950 个可视组件改为 `NoCollision`，四色几何各 480；保存后复验 `changed=0`、query/physics/overlap residual 全为 0。未改变信号数量、颜色或可见性，最终 signal screenshot/JSON 仍待 Gate300 后生成 | `enforce_signal_visual_no_collision.py`、地图 external actor、UE log |
| 2026-07-19 06:21 | 完整 cell camera 与信号 NoCollision 后再次运行 Gate30 前置 | PIE live exact-XY admission、确定性重复日志、PIE teardown | FAIL（未启动 verifier、未生成新报告）：entity 0 / lane 14、entity 2 / lane 284、entity 3 / lane 172、entity 22 / lane 256 仍被真实 first-blocker 探针拒绝；证明问题不是旧 camera 覆盖或信号射线碰撞。保持 fail-closed 和原 ground tolerance，改为同 district、同严格冲突约束、实时落地成功且动态净空 `>=55 cm` 的确定性候补出生点；Gate30 继续保持未验证 | `D:\TelecomTwinCitySampleUser\Saved\Logs\TelecomTwin.log`、`OpenMassCrowdSpawner.*` |
| 2026-07-19 06:54 | 实现同区确定性 reserve admission，并将六区流送相机统一为 110 m 正交俯视 | host verifier/self-test、UHT/UBT/full link、MCP camera readiness | 构建与覆盖 PASS：完整 300 槽位计划及 Gate 前缀保持稳定，配额 `[51,51,47,51,50,50]`；实时 exact-XY 候补仍须满足同 district、动态净空 `>=55 cm`，每次提交最多 25 人；六个 camera 均为 11000 cm、pitch -90°、FOV 90°、2048²，tileset 连续 12 tick 稳定且 component count=1481。DLL 846336 bytes，SHA-256 `601203...F1B`；尚不是 Gate 验收 | `OpenMassCrowdSpawner.*`、`verify_central_crowd_gate_runtime.py`、`register_central_cesium_streaming_cameras.py`、`D:\TelecomTwinTemp\TelecomTwin_build_20260719_065419.out.log` |
| 2026-07-19 06:58–07:02 | reserve 版本首次完整 Gate30 运行诊断 | live admission、逐帧 3D overlap、lane/progress、route wait/replan、ground guard | Admission PASS：25+5、30/30、六区各 5、432 probes / 382 rejects / 2 reserve replacements，出生时 overlap=0；运行 FAIL：entity 23/29 首次 52.327 cm，随后 entity 5/11/23/29 聚集在 lane249 终点，最小 0.287 cm，最终 8 stuck / 11 overlap pairs。ground unsupported=0、invalid=0，排除 Cesium 落地问题；Gate30 仍未通过 | `D:\TelecomTwinCitySampleUser\Saved\Logs\TelecomTwin.log` |
| 2026-07-19 07:22 | 精确定位 lane249 路口越界与永久等待根因并开始修复 | 项目/UE5.7 PathFollow 源码对照、认证 JSON 路径还原、独立只读审查 | 根因有三层：LaneLocation 10 cm 与 ShortPath 1 cm 距离域造成单帧穿线；corridor-held 行人仍抢 node owner 形成环等待；超时把 wait lane 的全部碰撞邻接误当关闭道路，封死真实出口。当前补丁改用 ShortPath 端点硬 cap、3D 净距+10.01 cm 量化保护、held 不再抢 incoming node、重规划仅禁真实 wait lane；尚待重新编译和 60 秒 Gate30，故状态仍为 FAIL/in-progress | `OpenMassCrowdVisualization.cpp`、UE `MassZoneGraphNavigationProcessors.cpp`、独立审查记录 |
| 2026-07-19 07:30 | 第一轮正式 60 秒 Gate30 verifier | schema-v4 60 秒完整会话 | FAIL：最低 moving 25/30、最高 stuck 3/30、最大冲突等待 55.993 s；人数和落地正常，但未满足移动/等待门 | verifier 输出、UE log |
| 2026-07-19 07:42–08:15 | 定位并修复 UE 5.7 ShortPath 缓存窗口与远端 lane progress 不一致 | cached lane/path point、认证 sample、运行轨迹、Development Editor build、长时间 PIE | 构建 PASS；改为认证几何分块，`short_path_chunks` 超过 9000，unsupported/invalid 为 0；但 moving 约 26、stuck 4、最大等待约 333 s，Gate30 仍 FAIL | `ClampCentralShortPathToCachedCoverage()`、UE log |
| 2026-07-19 08:13–08:37 | 缩窄冲突重规划 forbidden set、按 1.5 s bucket 节流，并处理低内存构建 | 代码审查、真实路口出口还原、`-NoUBA -MaxParallelActions=1` | forbidden set 不再把 55 cm 几何邻接误当道路关闭；首次 UBA 构建因约 67.1/69.2 GB 内存终止，关闭 UBA 后 37.58 s 构建 PASS；尚未解决物理预约死锁 | `OpenMassCrowdVisualization.cpp`、UBT 输出 |
| 2026-07-19 08:40 | 修复冲突重规划事务回滚和 stuck 观测口径 | 单线程 UBT / plugin link | 构建 PASS（20.38 s）：只有路径真正激活才提交 route；失败恢复旧 route/counter；stuck 只按认证位置位移计数。运行仍待重新验证 | `OpenMassCrowdSpawner.cpp`、UBT 输出 |
| 2026-07-19 08:48–08:51 | 最新 Gate30 长时间运行 | admission、逐秒 telemetry、3D center-distance、route replan | FAIL：30/30、六区各 5、VAT=30、unsupported=0、invalid=0、P95 约 19 ms；但最终 moving=18、stuck=12、最大等待约 194 s，entity 10/17 在真实认证汇入节点形成 12.865 cm 重叠；不得晋级 Gate100 | `D:\TelecomTwinCitySampleUser\Saved\Logs\TelecomTwin.log` |
| 2026-07-19 09:00 | 精确定位 12.865 cm 重叠的执行顺序根因 | lane 246/287 认证端点、Mass fragment/processor 顺序、速度日志对照 | 两条 Cesium 认证 lane 的端点本身相距 12.865 cm；预约只降低 `DesiredSpeed`，未截断已有 `DesiredVelocity`，被 hold 的实体继续滑入冲突区。修复目标为 Avoidance 后、Movement 前执行实际速度 cap；尚未完成构建/PIE | `OpenMassCrowdVisualization.cpp`、认证 JSON、UE log |
| 2026-07-19 09:05 | 复核 OpenSpec 碰撞与连通覆盖契约 | OpenSpec spec/tasks、认证器轨道生成公式、cache 全图审计 | 发现两项实现自行加严/错误聚合：规范严重重叠阈值是低于 20 cm，不是 55 cm；当前正反 RightTrack 理论间距约 20 cm，整段 55 cm 反向锁会必然单车道化。当前 3.736 km 又是 68 个断开分量之和，最大分量仅 342.814 m，street_block_count=1，六区在 6 个分量，不能满足 connected Central | OpenSpec、`central_network_certified.json`、只读审计 |
| 2026-07-19 09:10 | 将 cache/runtime verifier 改为 fail-closed 的真实 OpenSpec 契约 | host self-test、当前 cache verifier | 工具 PASS、当前 cache 诚实 FAIL 4 项：68→应为 1 个连通分量；1→至少 4 个 connected street blocks；最大连通分量 34281.414 cm→至少 300000 cm；六个 district component→应为 1。硬碰撞门改为 60 秒窗口无低于 20 cm；55 cm 仅保留出生/同向跟随质量诊断，不再伪装规范门 | `verify_central_network_cache.py`、`verify_central_crowd_gate_runtime.py`、`capture_central_crowd_evidence.py`、`Saved/Reports/central_network_cache_verification_latest.json` |
| 2026-07-19 09:18–09:25 | 修复 whole-opposing 分类把 20 cm 硬门错误复用于 55 cm 入场走廊的问题 | r0-c2 候选计数、300-slot 重放、单线程 `-NoUBA` UBT | 首次 PIE 因 r0-c2 `candidates=0/required=47` 在 admission 前 60 次重试失败；确认 reverse tracks 的 20.0036–21.5051 cm 间距被错误展开成 lane-long local guard。将 whole-corridor 几何容差恢复为 55 cm 后，完整计划恢复为 300 槽位、六区配额 `[51,51,47,51,50,50]`、最小计划净空 79.955 cm；构建 PASS | `OpenMassCrowdSpawner.cpp`、UE log |
| 2026-07-19 09:37–09:40 | velocity-cap 版本 Gate30 60 秒以上诊断 | 30/30 live admission、持续 telemetry、20 cm lifetime overlap、VAT、信号清单、instant evidence | 人数/落地/表示/性能/信号回归正常：30 simulated/represented、VAT=30、unsupported=0、invalid=0、P95 约 19 ms、严重重叠 current/peak/observations 全为 0、信号 30/1920/480×4；但 `moving=11/stuck=19`，最大等待超过 110 s，故 Gate30 明确 FAIL。截图写文件超时，JSON 仍保留，不能冒充最终截图 | [[../Evidence/OpenMassCrowd/Central300/central_crowd_evidence_diagnostic_gate30_post_velocity_clamp_latest.json]] |
| 2026-07-19 09:41–09:55 | 对 19 个 stuck entity 建立 wait-for 根因图 | entity/lane/route/component/hold/replan 日志逐项还原 | 证实为稳定资源死锁而非暂时拥堵：连续约 53 秒固定 11 moving/19 stuck；19 人恰好全部位于 7 个多人分量，11 个 singleton 分量恰好 11 人持续移动。`701→700`、`284→285`、`382→383` immediate reverse 队列被 55 cm whole-corridor 自己阻塞；node/corridor/local 非原子持锁进一步闭环；每 1.5 s replan 只换路线、不改变资源 | UE log、`OpenMassCrowdVisualization.cpp`、`OpenMassCrowdSpawner.cpp` |
| 2026-07-19 09:52–10:03 | 运行 transient Central network review overlay 并对照真实 Cesium 场景 | 629 个 source points first-blocker surface snap、颜色分类、top-down viewport、未保存关卡 | 审核脚本 PASS：818 certified lanes、37432 certified segments、54 crossings、25 manual-review segments、273 rejected-candidate segments、46 portals；surface snap 629/629、failure=0。该视图证明现有路线确实贴在流送 Cesium 表面，同时也不能消除 68-component 连通失败；没有自动接受 connector | [[../Evidence/OpenMassCrowd/Central300/central_network_topology_review_latest.jpg]]、[[../Evidence/OpenMassCrowd/Central300/central_network_review_overlay_latest.json]] |
| 2026-07-19 09:50 | C 盘写报告时触发 `Errno 28`，执行可逆空间保护 | 盘符余量、源/目标绝对路径与归档字节数 | 将本次项目产生的 39.5 MB crash bundle 和约 750 MB 临时调研 clone 移到 `D:\TelecomTwinCitySampleUser\Saved\Archived*`，未删除项目资产、源码或用户资料；重跑 overlay 报告成功。C 盘仍偏紧，后续构建继续使用 D 盘 UserDir/DDC/Zen/TEMP 与单线程 NoUBA | 文件系统检查、overlay 重跑输出 |
| 2026-07-23 23:11–23:14 | 编译并实际运行 runtime-only 冲突资源版本 | 6/6 UBT、同一 PIE 会话持续 telemetry、MCP PNG/JSON | 构建 PASS；运行 Gate FAIL。启动后曾达到 30 moving / 0 stuck；约 60 秒时为 21/9，继续运行并截图时最终为 0/30。完整会话 severe overlap 为 0、unsupported=0、信号仍为 30/1920/480×4；截图工具的 `capture_passed=true` 只表示 PNG 成功，不表示 Gate 通过 | [[../Evidence/OpenMassCrowd/Central300/central_crowd_evidence_gate30_liveness_fail_20260723.json]]、[[../Evidence/OpenMassCrowd/Central300/central_crowd_evidence_gate30_liveness_fail_20260723.png]] |
| 2026-07-23 23:14–23:30 | 对 Gate30 等待链只做三次有界仲裁修正并停止继续试错 | 每次单线程 NoUBA 全量构建、同一 PIE 持续 telemetry、最终 MCP PNG/JSON | 构建均 PASS；短窗口一度改善为 26/4、随后 30/0 和 27/3，最大等待由约 110 s 降至约 9.6 s，且触发 `conflict_wait_replans`；但长时间运行再次退化为 `moving=0/stuck=30`。完整会话 severe overlap current/peak/observations=0、unsupported=0、VAT=30；Gate30 仍 FAIL，未启动 Gate100 | [[../Evidence/OpenMassCrowd/Central300/central_crowd_evidence_gate30_stable_arbitration_fail_20260723.json]]、[[../Evidence/OpenMassCrowd/Central300/central_crowd_evidence_gate30_stable_arbitration_fail_20260723.png]] |

### 07:30–09:10 关键结论

> [!warning] 状态仍为 `in-progress-unverified`
> Gate30 没有通过，Gate100/200/300 均未启动。当前还存在两个必须按顺序解决的
> blocker：先完成一个真实连通、满足 3 km/4 blocks/8 junctions/六区可达的 Cesium
> 认证网络；再用速度预约补丁通过 60 秒 Gate30。任何 component-local 运行或旧 15 秒
> 报告都不能替代这两个门禁。

ShortPath 跳跃已通过 fail-closed 分块修正：UE 5.7 最多缓存 5 个 lane point，
Central 又保留约 10 cm 的密集认证采样，原实现会出现画面仍在缓存边界、lane progress
却已跳到远端的状态。现在超出窗口的最后一点重新绑定缓存边界，清除尚未真实到达的
next-lane/exit，并以 partial result 在后续帧继续前进。

最新 12.865 cm 重叠不是悬空或 Cesium 假落点。lane 246 与 lane 287 的 RightTrack
端点都来自真实 Cesium 碰撞认证，且在同一汇入节点只相隔 12.865 cm。违规来自 Mass
预约执行顺序：只写 `MoveTarget.DesiredSpeed=0` 不能抹掉本帧已有的
`DesiredMovement.DesiredVelocity`，所以被拒绝进入的实体仍然滑过预约边界。代码必须
在 Avoidance 后、Movement 前把实际期望速度按预约 cap 截断，完全 hold 时直接清零。

55 cm 与 20 cm 的职责已经分开：

- 55 cm：初始槽位和同向跟随的质量目标/诊断；
- 20 cm：OpenSpec 明确规定的跨车道、路口和完整 60 秒 steady-window 严重重叠硬门；
- 低于 20 cm 的 current pair、窗口内新增 observation 或无效位置任一出现，Gate 必须失败；
- 55 cm 全路段反向互锁已被判定不适用于当前约 20 cm 分离的 certified RightTrack。

连通覆盖 verifier 也已从“断开分量求和”改为“单一连通主图”。认证语义恢复已经穷尽
±200 cm 的现有 OSM 子段；不同分量的最近认证几何仍约 189.3 cm，不能靠改 node id 或
伪 portal 合并。若没有新的、人工审查过并经 Cesium 实时碰撞认证的语义/手工连接，
OpenSpec 3.4 必须保持失败。

### 09:18–10:03 最新诊断与审核证据

> [!failure] Gate30 仍未通过
> 后速度预约已经把低于 20 cm 的严重重叠从完整会话观测中清零，但这并不等于人群
> 可用。30 人中只有 11 人持续移动，19 人在 7 个多人分量中形成稳定 wait-for 闭环；
> `moving=11/stuck=19` 连续约 53 秒不变，hold 计数和最大等待持续线性增长。因此没有
> 运行 Gate100，也没有生成任何晋级标记。

本轮把两个此前混在一起的距离职责进一步分开：55 cm 继续用于出生槽位、动态 admission
和同向行走质量；20 cm 才是运行时严重重叠的硬门。当前修复正在保留 55 cm 入场容量的
同时，为含 `<20 cm` 几何样本的 whole-opposing pair 建立 runtime-only 局部资源，取消
非 strict pair 的整条走廊互斥，并统一 node/corridor/local 的持锁优先级。完成构建和新
60 秒 Gate30 前，本页仍保持 `in-progress-unverified`。

下面的截图是 transient、未保存关卡的真实 Cesium 路网局部俯视审核。绿色为 certified
lane，蓝/青为导入 pedestrian/sidewalk，黄色为 crossing，橙色为 manual-review，红色为
rejected candidate，洋红圆点为 portal。它可证明线条经过 first-blocker surface snap；
它不能证明六区已经连通，也不能替代最终 450 m × 300 m 总览图。

![[../Evidence/OpenMassCrowd/Central300/central_network_topology_review_latest.jpg]]

对应机器报告：
[[../Evidence/OpenMassCrowd/Central300/central_network_review_overlay_latest.json]]。

### 2026-07-23：停止重复运行，锁定剩余 Gate30 等待链

本轮首先核对 DLL 与源码时间，确认此前反复启动 UE 时加载的仍是
`2026-07-19 09:28` 旧 DLL，而死锁修复源码写于 `10:13–10:15`。关闭编辑器后使用
`-NoUBA -MaxParallelActions=1` 完整编译 6/6 成功，随后重新启动项目和 PIE；这是第一次
真正运行该修复，而不是再次运行旧二进制。

结果说明修复有效但不完整：30 人全部成功入场、六区各 5 人，启动阶段一度达到
`moving=30/stuck=0`；同一会话约 60 秒后稳定为 `moving=21/stuck=9`，最大局部资源等待
超过 57 秒，而 `conflict_wait_replans=0`。继续保留会话以写截图时最终达到
`moving=0/stuck=30`。与此同时，低于 20 cm 的 current/peak/observation 全部为 0，
`unsupported_visuals=0`，证明现在的唯一 P0 不是悬空或穿人，而是局部预约活性。

代码审计定位到确定条件：等待者只要已占有 node/local/corridor 任一资源，
`CurrentReplanBucket` 即使每 1.5 秒跨桶也会被 `!bHoldsAnotherResource` 拦截，因而日志中
出现“最大等待持续增长、重规划计数始终为 0”。当前最小修复已改为所有等待者均可在
跨桶时只排除实际争用的 next lane 并重新规划；当前物理 claim 在本 tick 仍保留，
最后的 20 cm 速度截断继续作为硬安全门。该改动尚待下一次编译和完整 60 秒运行，
因此本页状态仍为 `in-progress-unverified`，不会进入 Gate100。

![[../Evidence/OpenMassCrowd/Central300/central_crowd_evidence_gate30_liveness_fail_20260723.png]]

机器报告：
[[../Evidence/OpenMassCrowd/Central300/central_crowd_evidence_gate30_liveness_fail_20260723.json]]。

### 2026-07-23：最终有界尝试结论（停止继续改动）

为避免继续陷入重复运行，本轮把尝试严格限制为三次仲裁修正，每次都先完整编译再启动
同一个 Gate30 PIE 会话观察，不启动 Gate100/200/300：

1. 去掉“已持有其他资源就禁止重规划”的条件；运行约 60 秒为 `moving=26/stuck=4`，
   但等待者位于当前 lane 中部，重规划仍没有可靠解除等待链。
2. 将正在移动且实际占用冲突区的实体纳入本 tick 请求，并使用稳定全局实体优先级；
   约 43 秒为 `moving=23/stuck=7`，说明只清理移动者的历史授权仍会让上一帧失败者保留旧 claim。
3. 不再预授权任何历史局部 claim，所有实体每 tick 重新仲裁；非移动实体继续由最终 20 cm
   速度截断保护。短窗口曾达到 `moving=30/stuck=0`，约 60 秒为 `27/3`，
   `conflict_wait_replans` 已实际触发，最大等待约 9.6 秒；这证明等待链明显缓解，但没有
   形成可持续活性保证。

最终取证发生在同一会话继续运行后。机器报告为 `simulated=30`、`represented=30`、
`admitted=30`、`moving=0`、`stuck=30`、`completed_trips=1055`、`route_replans=2`、
`unsupported_visuals=0`、`VAT=30`。低于 20 cm 的 severe overlap current、lifetime peak、
observations 均为 0，最小完整会话观测中心距为 20.012 cm；信号回归仍为 30 个 source、
1920 条 ray、绿色/黄色/橙色/红色各 480。截图 JSON 中 `status=PASS` 和
`capture_passed=true` 只表示 PNG 写入与瞬时证据字段完整，报告同时明确
`acceptance_gate_evaluated=false`，不得解释为 Gate30 通过。

> [!failure] 当前正式结论
> Gate30 仍未通过：仲裁修正改善了短时活性，但长时间运行仍会全部停止。因此停止继续
> 改代码、停止 PIE、保留 UE 编辑器和证据；Gate100/200/300 不启动，OpenSpec 4.5 不勾选。
> 另外，Central cache 仍为 68 个真实认证分量、最大连通分量约 342.8 m、六区分属六个
> 分量，OpenSpec 3.4 的单一连通路网同样保持失败。没有经 Cesium 认证的新连接之前，
> 不能伪造连通性，也不能宣称 300 人目标完成。

![[../Evidence/OpenMassCrowd/Central300/central_crowd_evidence_gate30_stable_arbitration_fail_20260723.png]]

最终机器报告：
[[../Evidence/OpenMassCrowd/Central300/central_crowd_evidence_gate30_stable_arbitration_fail_20260723.json]]。

### 2026-07-24：有界拓扑恢复、三层 LOD 实机取证

本轮没有再无期限重复认证或重复启动。拓扑恢复先限定单个 OSM 语义候选、固定尝试次数，
并让 A* 只沿 Cesium 实时碰撞确认过的地面逐小段前进；每条新边仍完整经过 10 cm 采样、
中心/左右三轨、净空胶囊和 first-blocker 检查。工具新增从已认证前沿续跑、前沿回退、可配置
网格和有界停止条件，主机自测通过。结果只把工作清单从 68 个可信分量减少到 67 个；
发布用 `central_network_certified.json` 仍保持 68 个分量，因为最终化脚本在没有单一合格连通图时
按设计拒绝覆盖。OpenSpec 3.4 因而继续保持未完成，不能把局部安全边伪装成四街区连通路网。

人群运行时已取消旧的第二套 Central node/entrance 预约，只保留一个局部冲突仲裁权威，
冲突等待者也允许在禁止实际争用 lane 后重新规划。新 PIE 会话真实达到 30/30 入场、六区各
5 人、`unsupported_visuals=0`，但会话结束前为 `moving=21`、`stuck_gt5s=8`，完整会话仍记录
1 个低于 20 cm 的严重重叠峰值、35 次观测，最大冲突等待超过 216 秒。因此这些修复只算
诊断进展，Gate30/OpenSpec 4.5 仍明确失败；没有启动 100/200/300 人晋级。

三层表现集成已经在同一个真实 `/Game/Maps/shanghai` PIE 会话中逐层触发并保存机器报告：

- High 取证：`High=1, Low=0, VAT=29, total=30`，目标实体被报告为 High；
- Low 取证：`High=1, Low=1, VAT=28, total=30`，目标实体被报告为 Low；
- VAT 取证：`High=1, Low=1, VAT=28, total=30`，24 个模块化 ISM 组件对应 28 个唯一实体变换；
- 三份报告均为 `unsupported_visuals=0`，代码中的全局预算为 High 24、Low 72、VAT/ISM 余量，
  High/Low/VAT 只替换同一 Mass 实体的表现，不创建第二套移动代理。

因此 OpenSpec 5.2 已有代码与运行证据，可以完成；5.3 仍不完成。原因是本轮 PNG 虽证明对应
LOD 目标被引擎识别并成功写入，但近景人物在画面中太小，尚不足以肉眼证明每次切换中的稳定
身份、外观多样性和远景动画质量。`status=PASS` 只表示该 LOD 取证脚本自身通过，不代表
Gate30，更不能代表 300 人验收。

![[../Evidence/OpenMassCrowd/Central300/central_crowd_lod_high_runtime_20260724.png]]

High 机器报告：
[[../Evidence/OpenMassCrowd/Central300/central_crowd_lod_high_runtime_20260724.json]]。

![[../Evidence/OpenMassCrowd/Central300/central_crowd_lod_low_runtime_20260724.png]]

Low 机器报告：
[[../Evidence/OpenMassCrowd/Central300/central_crowd_lod_low_runtime_20260724.json]]。

![[../Evidence/OpenMassCrowd/Central300/central_crowd_lod_vat_runtime_20260724.png]]

VAT 机器报告：
[[../Evidence/OpenMassCrowd/Central300/central_crowd_lod_vat_runtime_20260724.json]]。

UE 5.7 只读 VAT 资产复核也重新通过：6 套官方 City Sample 外观、24 个可见模块、144 个
精确生成资产均存在，输出路径中没有 Mannequin 或 base proxy：
[[../Evidence/OpenMassCrowd/Central300/city_sample_vat_verify_latest.json]]。

中环实时视口补充图可看到真实 City Sample 人物位于 Cesium 步行面，但人物尺度仍不适合作为
5.3 的最终视觉证据：

![[../Evidence/OpenMassCrowd/Central300/central_crowd_lod_high_low_live_20260724.jpg]]

取证完成后已恢复运行时玩家相机并正常结束 PIE；所有 LOD 相机位移都只作用于 PIE 临时世界，
没有保存进 `shanghai.umap`。相关 Python 文件已通过 `py_compile`，LOD 取证和语义绕行工具的
主机自测均为 PASS；`openspec validate scale-central-crowd-to-300 --strict --no-interactive`
也已通过。

### 2026-07-24：补齐同一实体的 High → Low → VAT 转换证据

上一轮截图存在一个纯取证问题：HighResShot 写文件期间行人仍沿 Mass 路径移动，相机只在
请求截图前定位一次，所以 Low 人物可能跑到画面边缘。修复只让 PIE 玩家相机和编辑器视口
每 0.1 秒追随稳定证据实体；它不移动 Mass Entity、不改变路线、不生成第二套移动代理，
也不保存地图。修复后的三张原始 1600 × 900 PNG 中，目标始终位于画面中央。

三次独立捕获都由 C++ `GetCentralLODEvidenceSnapshot()` 读取稳定槽位 0，结果完全一致：

| 捕获 | Mass 身份 | 外观种子 | 外观 | 运行时表示 | 关键结果 |
|---|---|---:|---|---|---|
| High | `1:1` | 510772 | `CitySample_FTN` | `OpenMassCrowdCitySampleActor` | 正确 High、可见、无占位 |
| Low | `1:1` | 510772 | `CitySample_FTN` | `OpenMassCrowdCitySampleLowResActor` | 正确 Low、可见、无占位 |
| VAT | `1:1` | 510772 | `CitySample_FTN` | VAT/ISM | 帧推进 38.480469，非静态远景 |

VAT 取证还读取到 6 套官方 City Sample 外观、24 个 ISM 模块组件，24/24 组件均上传至少
4 个 per-instance custom floats；所有 mesh 路径均来自
`/OpenMassCrowd/CitySampleVAT/`，没有 Mannequin 或 base proxy。三份报告各自的
`visual_evidence_checks.all_required_checks_passed=true`，但也都明确写有
`acceptance_gate_evaluated=false`。据此只完成 OpenSpec 5.3，不修改 3.4、4.5 或 6.x。

![[../Evidence/OpenMassCrowd/Central300/central_crowd_lod_identity_high_20260724.png]]

High 原始报告：
[[../Evidence/OpenMassCrowd/Central300/central_crowd_lod_identity_high_20260724.json]]。

![[../Evidence/OpenMassCrowd/Central300/central_crowd_lod_identity_low_20260724.png]]

Low 原始报告：
[[../Evidence/OpenMassCrowd/Central300/central_crowd_lod_identity_low_20260724.json]]。

![[../Evidence/OpenMassCrowd/Central300/central_crowd_lod_identity_vat_20260724.png]]

VAT 原始报告：
[[../Evidence/OpenMassCrowd/Central300/central_crowd_lod_identity_vat_20260724.json]]。

跨三档汇总：
[[../Evidence/OpenMassCrowd/Central300/central_crowd_lod_identity_summary_20260724.json]]。

### 2026-07-24：Gate30 两次有界仲裁验证

完成 LOD 证据后没有继续重复旧 Gate30。源码与上一会话日志首先定位到一个确定矛盾：
`ProcessCentralConflictReplans()` 的设计注释要求 FIFO 等待年龄只在实体真正离开或更换资源后
清零，但实际获准分支每帧立即把它清零。这样刚获准的入口下一帧会掉到等待队尾，路口可能
不断换优先权却无人真正通过。最小修复为：实体仍请求同一资源期间保留等待年龄，现有 cleanup
在实体离开全部候选资源后再清零。

第一轮完整编译后的 PIE 诊断窗口达到引擎自身 `frame_window_s=60.00`：30 simulated、
30 represented、`moving=30`、`stuck_gt5s=0`、`unsupported=0`，最大冲突等待从上一版本约
280 秒降到 1.515 秒，P95 为 28.44 ms。这证明长期等待/活性回归已经被该最小修复解决。
但是该会话仍记录 1 个严重重叠峰值、12 次 `<20 cm` 观测，最小中心距 8.17 cm，
因此 Gate30 仍失败。

日志把唯一重叠定位到实体 10/17：两者同时到达同一三岔路口的 outgoing lane 247/286，
都处于 progress 0，中心距 13.308 cm。第二次有界尝试让 chunked ShortPath 尚未发布
`NextLaneHandle` 时，提前使用权威 `LanePath` 预约 next lane。它确实把严重重叠降为 0，
但 60 秒结果退化为 `moving=1`、`stuck_gt5s=7`、最大等待 31.071 秒、P95 53.31 ms。
这一尝试被立即撤销并重新完整编译，没有保留在最终源码中。

> [!failure] Gate30 当前结论
> 保留“等待年龄到真正离开资源后再清零”的活性修复；撤销过度保守的 next-lane 回退。
> 4.5 仍不勾选，因为保留版本虽然达到 30/30 持续移动，但 lifetime 严重重叠不是 0。
> 未启动 Gate100/200/300，也没有把第二次无重叠但几乎停摆的结果冒充成功。

两次有界试验机器摘要：
[[../Evidence/OpenMassCrowd/Central300/central_crowd_gate30_bounded_arbitration_trials_20260724.json]]。

### 2026-07-25：结束 Gate30 试错循环，回退所有退化方案

本轮只处理 Gate30 剩余的“真实寻路必须同时满足活性、20 cm 硬间距和性能”问题，没有启动
Gate100/200/300，也没有修改 30 信源、1920 条射线或四色各 480 的电信场景。每个尝试都限定
为一次编译和一次真实 PIE 运行；失败后立即取证并从源码撤销，避免继续在同一个假设上循环。

首先把严重重叠日志扩展为可定位的诊断：除实体编号和距离外，还记录双方 lane index、稳定
lane ID、lane progress、共享资源编号、资源区间和世界坐标。由此确认问题不只是“路口预约慢”：
认证缓存中存在两条未声明为直接 reverse lane、但几何上几乎反向重合的 OSM semantic-recovery
候选（运行时 lane 502/504）。离线复核得到两条 lane 的 center-track 最小间距 0 cm、
right-track 最小间距约 4.78 cm，而 left-track 最小间距约 20.324 cm。每条候选单独都有 Cesium
支撑证据，但“单 lane 真实落地”不等于“多 lane 组合后能容纳两个行人同时通过”；这是当前
缓存/拓扑层的真实缺陷，不能靠给行人加假偏移掩盖。

本轮依次否决了以下方案：

1. next-lane 预约第一次误用了 progress 字段，结果为 `moving=23/stuck=7`、最大等待
   204.88 秒，仍出现 1 对严重重叠，最小距离 14.55 cm；修正为权威
   `DistanceAlongLane` 后，60 秒仍出现 1 对重叠，最小距离 11.4875 cm，P95 约 86 ms。
   两个版本都已撤销。
2. post-movement rollback 在实体移动后若发现 `<20 cm` 就退回上一变换。它让 overlap peak
   变成 0、P95 为 24.91 ms，却在 60 秒内触发 10,030 次回滚，最终
   `moving=0/stuck=30`。这只是把碰撞改造成全员冻结，不是真实避让，已撤销。
3. certified track-side 尝试没有制造运行时 XY 偏移，而是让负向 semantic-recovery lane 使用
   缓存中已由 Cesium 认证的 left track，其余 lane 继续使用 right track。短时曾为
   `moving=30/stuck=0`，但完整会话仍累计 1 对严重重叠、5 次观测；60 秒 P95 约
   45.90 ms，继续取证时已退化到 `moving=2/stuck=28`。说明按 lane 名称统一选边不是可证明
   的冲突解法，且把局部冲突资源从约 29,775 增至 45,761，性能也退化。该改动已撤销。

下面两张图是失败会话的原始 1600 × 900 PIE 截图。取证脚本临时隐藏电信射线以便观察城市
和人群，完成后恢复；PNG 只提供可视上下文，具体通过/失败以同名 JSON 的运行时 getter 为准。
其中 JSON 的 `status=PASS` 只表示截图成功写入，文件同时明确
`acceptance_gate_evaluated=false`，不能解读为 Gate30 通过。

![[../Evidence/OpenMassCrowd/Central300/central_crowd_evidence_gate30_post_movement_rollback_fail_20260725.png]]

post-movement rollback 原始报告：
[[../Evidence/OpenMassCrowd/Central300/central_crowd_evidence_gate30_post_movement_rollback_fail_20260725.json]]。

![[../Evidence/OpenMassCrowd/Central300/central_crowd_evidence_gate30_certified_track_sides_fail_20260725.png]]

certified track-side 原始报告：
[[../Evidence/OpenMassCrowd/Central300/central_crowd_evidence_gate30_certified_track_sides_fail_20260725.json]]。

> [!failure] 2026-07-25 正式结论
> 所有退化方案均已从 C++、头文件和 Python 取证接口中删除；回退版重新完整编译为
> `Result: Succeeded`，运行时验证器 `--self-test` 为 PASS，OpenSpec strict validate 为 valid。
> 只保留此前已经证明能改善活性的“等待年龄到真正离开资源再清零”修复，以及不会改变行为的
> 详细碰撞诊断。OpenSpec 3.4、4.5 继续不勾选：发布缓存仍为 68 个真实认证分量、仅一个
> connected street block、最大连通分量约 342.8 m；而现有保留版本也没有同时满足 60 秒
> 活性、零 `<20 cm` 重叠、零 unsupported 和 P95 `<33 ms`。因此 100/200/300 晋级没有启动。

本轮机器化闭环摘要（包含全部被撤销方案、精确数值、lane ID 和正式阻塞项）：
[[../Evidence/OpenMassCrowd/Central300/central_crowd_gate30_diagnostic_closure_20260725.json]]。


## 9. 完成条件

只有以下条件全部满足，才能把本页状态从 `in-progress-unverified` 改为 `verified`：

- [ ] OpenSpec 3.4、4.5、6.1–6.4、7.1–7.3 全部有对应证据（4.4、5.1–5.3 已完成）；
- [ ] Central cache 的真实 Cesium 认证和人工叠加审查通过；
- [ ] 30 → 100 → 200 → 300 逐级通过，未跳级；
- [ ] 300 人满足 60 秒数量、移动、卡住、unsupported 和性能门槛；
- [ ] High/Low/VAT 三层视觉均来自官方 City Sample，且没有 Mannequin 占位；
- [ ] 原 local 30 人、30 信源、1920 射线和四色各 480 全部回归通过；
- [ ] 两次冷重启无需修复脚本；
- [ ] 新截图和 JSON 已写入独立 Central 证据目录，本页所有 `TBD` 已替换；
- [ ] 实现提交和远程分支可复现，两个既有回滚 tag 仍保持原指向。

## 10. 相关文档

- [[TelecomTwin_CitySample_30_Pedestrian_Demo|30 人完整实施记录]]
- [[../CitySampleCrowds_UE57_Integration|City Sample UE 5.7 集成说明]]
- [[../OpenMassCrowd_UE57_HongKong_Demo|OpenMassCrowd 香港 Demo 说明]]
- [[../Evidence/OpenMassCrowd/README|30 人证据索引]]
- [[../../../TelecomTwin 四项问题解决报告|电信射线四项问题报告]]
