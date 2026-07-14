# City Sample Crowds 接入记录（UE 5.7）

## 目标

在现有香港 TelecomTwin 场景中保留已经验证的 Cesium 地面投影、运行时
ZoneGraph、MassEntity 移动和局部避让，只把临时的 BattleWizard 视觉层升级为
Epic 官方 City Sample Crowds 人物，并使用 Mass Representation 与实例化 LOD。

当前工作分支：`experiment/citysample-crowds-ue57`。

## 官方资源事实

- 官方条目：<https://www.fab.com/listings/903037e9-e1ac-4f41-96e8-1683c6fa7ad4>
- 发布者：Epic Games；价格：免费；分发方式：Unreal Engine `asset_pack`。
- 官方支持版本：UE 5.0–5.3。Fab 没有声明 UE 5.7 原生兼容，因此接入必须经过
  前向升级和实际验证，不能把“能下载”写成“官方支持 UE 5.7”。
- 内容：12 个头部、10 个 Groom、6 种身体、10 个配饰、100 多个服装网格、
  60 种布料图案、角色组合 Data Asset、定制 Blueprint 和示例动画。
- 许可：Fab Standard License，且标记为 UE-Only Content；只能用于基于 Unreal
  Engine 的产品。原始资源不能作为独立资产重新分发。

官方兼容元数据：
<https://www.fab.com/i/listings/903037e9-e1ac-4f41-96e8-1683c6fa7ad4/asset-formats/unreal-engine>

## 当前状态

- 本机尚未下载 City Sample Crowds，VaultCache 中也没有该资源。
- TelecomTwin 当前 30 个 Mass Entity 的移动、ZoneGraph lane、避让和 Cesium 贴地
  已经工作。
- 第一阶段代码升级已经完成：删除逐实体 `AOpenMassCrowdVisualActor` 和
  BattleWizard 依赖，改为 UE 5.7 原生 MassCrowd ISM Representation，并通过
  AnimToTexture custom data 驱动行走动画。官方 City Sample 资源到位前暂用
  引擎 Mannequin VAT 资产验证管线，不把它冒充最终人物。
- 低内存 UBT/UHT/链接已通过；运行日志确认 30 个实体、2 条 lane、30 个 VAT
  实例和每实例 4 个 custom floats 均建立成功，无 Fatal 或 ensure。
- 已建立独立 Git 分支，原稳定版本仍在
  `experiment/open-mass-crowd-ue57`，回滚标签为
  `rollback/hk-street-crowd-30-2026-07-14`。

## 实施顺序

1. 把官方免费资源加入当前 Epic/Fab 账号库。
2. 优先尝试让 Launcher 以 UE 5.3 资产格式直接加入 UE 5.7 测试工程；如果 Fab
   阻止该路径，再创建 UE 5.3 中转工程并在 UE 5.7 中使用副本升级。
3. 验证 Blueprint、材质、Groom、骨骼和动画依赖。
4. 用 UE 5.7 的 `UMassCrowdVisualizationTrait` 与
   `UMassCrowdRepresentationSubsystem` 替换 30 个视觉 Actor。
5. 通过 AnimToTexture VAT 和 ISM custom data 保持所有行人持续行走动画；至少
   生成 6 种官方人物组合。
6. 删除 `VisualRootHeight = 90` 的假视觉抬高，用每种网格的局部变换校准脚底，
   Mass Transform 仍保持在 Cesium 碰撞点上方 2 cm。
7. 验证 30 人、对向避让、LOD、原通信射线、停止 PIE 和干净重启。

## Git 与资源分发

代码、配置、文档和本项目自有资产可以继续提交到 Git。City Sample Crowds 的
原始 `.uasset` 在提交到公开 GitHub 前必须单独审查许可和仓库可见性；默认做法是
记录 Fab 获取步骤，让协作者使用自己的 Epic 账号取得相同免费资源，而不是把
官方素材包当作本项目自有资产重新发布。

## 完成判据

- World Outliner 不再出现 30 个 `AOpenMassCrowdVisualActor`。
- 30 个 Mass Entity 由真正的 ISM/VAT 表示驱动，并至少有 6 种 City Sample 外观。
- 每个实体保留有效 ZoneGraph Lane Handle，能够持续移动并发生局部避让。
- 近景证据证明脚底落在 Cesium 路面，而不是视觉抬高或代理平面。
- 通信射线仍存在；项目关闭并重新打开后无需重跑布置脚本。
