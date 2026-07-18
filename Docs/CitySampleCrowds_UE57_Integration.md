# City Sample Crowds 接入记录（UE 5.7）

> 最终验证日期：2026-07-18（香港时间）
> 分支：`experiment/citysample-crowds-ue57`

## 结论

TelecomTwin 已在原有 `/Game/Maps/shanghai` 香港/Cesium 场景中接入 Epic 官方
City Sample Crowds 人物。运行时生成 30 个 Mass Entity，每个 Entity 一对一驱动
一个 City Sample 可视人物；最终重启验证为 30 人可见、30 人播放走路动画、30 人
在 6.022 秒内均移动超过 60 cm，30 种可见外观。

这次接入没有重新生成通信射线。验证器逐项确认原有 30 个信源、1920 条射线几何
以及绿/黄/橙/红各 480 条仍然存在。

## 官方资源审计

官方条目：<https://www.fab.com/listings/903037e9-e1ac-4f41-96e8-1683c6fa7ad4>

| 项目 | 本机实测结果 |
|---|---:|
| 本地来源 | `D:\CitySampleCrowds_Staging\Content\CitySampleCrowd` |
| 工程挂载点 | `Content\CitySampleCrowd`（目录联接，不进入 Git） |
| 文件数 | 1434 |
| 总大小 | 6,505,136,712 bytes / 6.058 GiB |
| Asset Registry 资产数 | 1434 |
| Skeletal Mesh | 135 |
| Animation Blueprint | 4 |
| `BP_CrowdCharacter` | 1，UE 5.7.4 编译通过 |
| 验证引擎 | `5.7.4-51494982+++UE5+Release-5.7` |
| VAT/AnimToTexture 资产 | 0 |

Fab 的本地内容没有暴露一个可可靠读取的发布版本号，因此没有虚构“精确包版本”。
这里记录的是可复现的本地文件集合、大小、引擎版本和蓝图编译结果。City Sample
Crowds 属于 UE-Only Content：只能用于基于 Unreal Engine 的产品，官方原始资源
不提交到公开 Git 仓库，也不作为本项目自有素材重新分发。

## 实际架构

```text
ZoneGraph / Mass movement / avoidance
                 │
                 ▼
       Mass FTransformFragment
                 │  每帧在 TG_PostUpdateWork 同步
                 ▼
AOpenMassCrowdCitySampleActor（轻量代理）
                 │  ChildActorComponent
                 ▼
Epic BP_CrowdCharacter + MTN_N_Walk_F_VarB
```

- Mass Entity 是唯一移动和导航所有者，City Sample Blueprint 不参与导航。
- 创建 Child Actor 时先调用官方 `SetRandomOptions`，再执行 Construction Script，
  因此本次 30 人得到 30 种可见组合，不再是同一个人物复制 30 次。
- `SkeletalMeshComponent0` 循环播放官方走路动画，并为每人随机动画起点和小范围
  播放速率，避免完全同步踏步。
- City Sample 人物的全部 Primitive Collision 被关闭，防止人物碰撞干扰 Cesium
  路面检测或既有通信射线。
- Mass 变换在 `TG_PostUpdateWork` 同步到人物代理，避免“Mass 在走、画面里人物
  不动”的两套位置。

## Cesium 贴地处理

每个候选位置必须命中可见、可查询、坡度合格且高度合理的
`CesiumGltfPrimitiveComponent`。精确 XY 没命中时，会在人物脚掌范围内增加最多
18 cm 的九点探测，以跨过摄影测量三角面或瓦片之间的细小缝隙；接受的高度仍然
只能来自真实 Cesium 碰撞三角面，并把结果写回原始实体 XY，不会吸附到旁边建筑
或代理平面。

最终 30 人全部命中 Cesium；Mass 根节点相对目标 2 cm 地面偏移的最大误差为
2.135 cm，可见脚部检查 30/30 通过，中位脚部偏移为 -1.405 cm。

## 为什么挂载整个官方目录

对 `BP_CrowdCharacter` 和 `CrowdCharacterDataAsset` 的硬依赖闭包只有 79 个包，
但加入软依赖后的闭包达到 1316 个包、5.572 GiB；随机外观正是通过这些软引用
选择身体、衣服、头发和材质。只复制硬依赖会让人物缺少随机组合。Demo 因而使用
一个 Git 忽略的目录联接挂载完整 6.058 GiB 官方包，而不是复制进仓库。

首次配置：

```powershell
pwsh -ExecutionPolicy Bypass -File .\Scripts\OpenMassCrowd\link_city_sample_crowds.ps1 `
  -Source "D:\CitySampleCrowds_Staging\Content\CitySampleCrowd"
```

## 启动和重启

City Sample 包含大量 4K/8K 纹理。为了避免首次编译时内存峰值以及系统盘 Zen
缓存空间不足，推荐使用仓库内启动器：

```powershell
pwsh -ExecutionPolicy Bypass -File .\Scripts\OpenMassCrowd\launch_telecomtwin_citysample.ps1
```

启动器把 UserDir、DDC、Zen 缓存放到 D 盘，并把纹理/资产编译并发限制为 1。
关卡内已经保存唯一的 `HK_OpenMass_Crowd_Spawner`；打开后直接点击“运行”即可，
不需要重跑布置脚本。最终验证是在关闭编辑器、重新启动后完成，停止 PIE 时日志为
正常的 `BeginTearingDown`，没有 Fatal、assert、OOM 或 HTTP 507。

## 可验证证据

- [最终运行 JSON](Evidence/OpenMassCrowd/open_mass_city_sample_runtime_latest.json)
- [Obsidian 实施记录](Obsidian/TelecomTwin_CitySample_30_Pedestrian_Demo.md)
- [30 人广角画面](Evidence/OpenMassCrowd/04_city_sample_30_wide.png)
- [29 种外观近景](Evidence/OpenMassCrowd/05_city_sample_29_variants_close.png)
- [Cesium 脚部贴地近景](Evidence/OpenMassCrowd/06_city_sample_cesium_foot_grounding.png)
- [重启并运行 6 秒后的画面](Evidence/OpenMassCrowd/07_city_sample_restart_after_6s.png)

JSON 的 SHA-256：
`9AAD2B0C6D5F67B4B0C2D048D59CD291F9DF61FFBA7F1FD2C7C53650B407373D`。

## 当前限制

- 这是 30 人局部街道 Demo，不是全香港自动行人路网；路线是项目内的有界
  ZoneGraph 环线。
- 移动与避让 Trait 已启用，30 个 Mass Entity 各自更新；本轮没有做最小人际距离
  的定量碰撞统计，因此不把截图中的会车写成严格的避碰性能证明。
- 当前高、低清表示都使用同一个 City Sample Actor 类；官方包审计未发现可直接
  使用的 VAT 资产，因此尚未实现数百/数千人规模所需的 ISM/VAT 远景切换。
- 当前验证的是 30 人 Demo 的稳定性，不是整机 FPS/显存基准测试。
- 协作者必须通过自己的 Epic/Fab 权限取得官方资源，再运行挂载脚本；仅克隆 Git
  仓库不会包含这 6.058 GiB UE-Only 素材。

## 回滚

接入官方资源前的远程回滚点：

```text
checkpoint/citysample-pre-assets-2026-07-15
0ad264252223654da99ac5400548d2524df3f4f7
```

回滚点不会包含 City Sample 目录联接或 D 盘缓存。
