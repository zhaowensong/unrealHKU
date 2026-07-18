# City Sample Crowds 接入记录（UE 5.7）

> 最终验证日期：2026-07-18（香港时间）
> 分支：`experiment/citysample-crowds-ue57`

## 结论

TelecomTwin 已在 `/Game/Maps/shanghai` 香港 Cesium 场景中完成一个可重启、可回滚的
30 人局部行人 Demo。Mass Entity 负责导航、移动与局部避让；Epic City Sample
Crowds 负责人物外观和走路动画。它不是把 30 个 Character 摆进场景，也不使用
预设的单一闭环路线：运行时建立 3×3、9 节点的局部 ZoneGraph，30 个 Entity 各自选择
A→B 目标并调用官方 `FZoneGraphAStar` 求路线。

三份最终自动验证均通过：

| 验证 | 最终结果 |
|---|---:|
| City Sample 运行验证 | 30 可见、30 移动、30 种外观；6.189 s 移动中位数 489.485 cm |
| 根节点贴地 | 30/30 命中 Cesium；最大目标偏差 0.001 cm |
| 导航验证 | 30 移动、0 可能卡住、报告结束累计 443 次完成行程、0 次重规划 |
| 运行时路网 | 9 节点、22 条有向 lane |
| 运行时地面恢复 | 71 次投影失败全部回到 lane 中线；0 回滚、0 不可恢复、0 悬空可视人物 |
| LOD 往返 | Low 30 → High 30 → Low 30；三阶段保持同一组 30 个 seed |

既有电信内容未被重建或替换。最终 City 验证仍计到 30 个信源、1920 条可见射线，
绿/黄/橙/红各 480 条。

## 官方资源审计

官方条目：<https://www.fab.com/listings/903037e9-e1ac-4f41-96e8-1683c6fa7ad4>

| 项目 | 本机实测结果 |
|---|---:|
| Fab 安装版本 | `5.7.0-45212673+++UE5+Dev-Marketplace-Windows` |
| 安装 Manifest ID | `EyuRxJDzJkyDTfX8-uC8NQ` |
| Fab Listing ID | `903037e9-e1ac-4f41-96e8-1683c6fa7ad4` |
| 本地来源 | `D:\CitySampleCrowds_Staging\Content\CitySampleCrowd` |
| 工程挂载点 | `Content\CitySampleCrowd`（目录联接，不进入 Git） |
| 文件数 | 1434 |
| 总大小 | 6,505,136,712 bytes / 6.058 GiB |
| Skeletal Mesh | 135 |
| Animation Blueprint | 4 |
| `BP_CrowdCharacter` | UE 5.7.4 编译并实例化通过 |
| VAT/AnimToTexture 资产 | 0 |

精确来源字段和安装日志哈希见
[City Sample 来源审计](Evidence/OpenMassCrowd/city_sample_source_audit.md)。City Sample
Crowds 是 UE-Only Content；仓库不重新分发官方素材。协作者必须使用自己的
Epic/Fab 授权取得资源。

## 导航与表示架构

```text
3×3 runtime ZoneGraph（9 nodes，最终 22 directed lanes）
                  │
                  ▼
      每个 Mass Entity 独立 A→B + FZoneGraphAStar
                  │
                  ▼
          Mass steering / avoidance
                  │
                  ▼
     exact Cesium runtime guard + FTransformFragment
                  │
                  ▼
 High: AOpenMassCrowdCitySampleActor
 Low : AOpenMassCrowdCitySampleLowResActor
                  │
                  ▼
 Epic BP_CrowdCharacter + 官方走路动画
```

- 逻辑 3×3 网格最多产生 24 条有向 lane。建网时只保留通过地面与空间检测的边；
  若某边不能安全通过会被成对裁剪。只有 9 节点仍连通且有向 lane 数为 16–24 的
  偶数时才接受子图。本次冷启动最终接受 22 条。
- 每个 Entity 独立选择起点和目的节点，A* 路线会经过不同路口和方向；不会沿同一条
  预设闭环循环。最终 30 人全部移动，覆盖 8 个方向桶，29 人产生有意义转向。
- `MassMovement`、steering 与 avoidance 负责期望速度和局部让行。可视人物的
  Primitive Collision 关闭，避免 City Sample 身体反过来污染 Cesium 查询。
- Mass 变换是唯一运动真值，可视代理在 `TG_PostUpdateWork` 同步，避免“后台 Entity
  在走而人物不动”。

## Cesium 碰撞认证与运行时贴地

这里使用离散、保守的碰撞认证，不声称对连续摄影测量网格做了数学证明。

1. **只接受原始 exact-XY 落点。** 每个采样点都在自己的 XY 上查询，不做邻域
   高度回退，不复用任何其他 XY 的 Z，也不把命中高度写回另一个 XY。
2. **先取全局原始第一阻挡物。** 查询先比较场景内的 raw hit，再判断最近命中是否
   是坡度合格的 `CesiumGltfPrimitiveComponent`。不会跳过近处立面、栏杆或陡面，
   再把它后面的路面当成有效落点。
3. **10 cm 级走廊采样。** 候选 lane 在中线、左右边界三条纵向轨迹上按不超过
   10 cm 的步距做 exact-XY 地面连续性检查，并以不超过 10 cm 的横向采样验证
   中线到两侧的支撑。三条轨迹与横截面都必须通过。
4. **30 cm 行人胶囊扫掠。** 以约 30 cm 半径的行人胶囊，同时做普通 World sweep
   和 direct Cesium component sweep，检查身体空间，而不只验证脚底一个点。
5. **障碍优先绕行或裁边。** 建网会尝试保持端点与切线的平滑 dogleg；候选绕行
   仍需经过同一套检测。全部失败才裁掉该无向边，再由 A* 使用连通安全子图。
6. **每帧路径仍受 exact guard。** steering 在路口转向时可能切过 lane 几何边界。
   若当前位置 exact-XY 投影失败，先回到 lane 中线；中线仍失败才回滚到该 Entity
   的 last-valid 位置，不把不可支撑位置提交给可视代理。

最终导航报告记录 71 次地面投影失败和 71 次中线恢复，last-valid 回滚为 0，
不可恢复为 0，当前不受支撑的可视人物为 0。30 个稳定身份各连续采样 3 次，所有
90 次均命中已加载 Cesium 碰撞；根节点相对 2 cm 目标偏移的最大误差为 0.001 cm。

## 人物外观和 High/Low 表示

High 与 Low 是两个不同的表示类，不是同一个 Actor 类的旧描述：

- `AOpenMassCrowdCitySampleActor` 是完整近景表示；
- `AOpenMassCrowdCitySampleLowResActor` 是远景 skeletal low-res 表示，会使用更低
  Skeletal LOD，并关闭或降低不必要的更新、毛发与阴影开销；它不是 VAT/ISM；
- 每个 Mass 身份使用稳定 seed 生成并缓存 City Sample 外观、动画起点和播放速率；
  High/Low Actor 被销毁和重建后重放同一配置，不会换人；
- LOD 验证强制完成 Low 30 → High 30 → Low 30，三阶段的 30 个 Mass seed 完全相同。

City 运行验证得到 30/30 官方 `BP_CrowdCharacter`、30/30 动画播放、30 种可见外观。

## 人际避让结果应该怎样解读

导航验证在 36 个采样帧中没有出现中心距离小于 20 cm 的 severe overlap；但确实
记录了 1 个低于 60 cm 的 body-overlap 样本，最小中心距离为 37.928 cm。因此本
Demo 证明的是 **Mass 局部避让已启用且没有 severe overlap**，不是硬物理碰撞或
“绝对不会穿插”的证明。可视人物自身碰撞关闭也是有意设计。

## 资源接入

官方随机人物依赖大量软引用，因此 Demo 挂载完整官方目录：

```powershell
pwsh -ExecutionPolicy Bypass -File .\Scripts\OpenMassCrowd\link_city_sample_crowds.ps1 `
  -Source "D:\CitySampleCrowds_Staging\Content\CitySampleCrowd"
```

`Content\CitySampleCrowd` 已在 `.gitignore` 中。只克隆仓库不会带回 6.058 GiB
UE-Only 内容。

## 启动和冷重启

从工程根目录使用安全启动器：

```powershell
pwsh -ExecutionPolicy Bypass -File .\Scripts\OpenMassCrowd\launch_telecomtwin_citysample.ps1
```

它把 UserDir、DDC 与 Zen 缓存放到 D 盘，并限制首次纹理/资产编译并发。最终冷启动
本次严格 first-blocker 冷启动约 44 秒，建议预留 40–50 秒；看到香港场景后按
`Alt+P`，等待日志出现
`OPEN_MASS_CROWD_READY requested=30 spawned=30`。30 人是 PIE 的 `BeginPlay`
运行时对象，未运行或停止 PIE 时在编辑器视口看不到是正常的。

## 最终证据

- [City Sample 运行报告](Evidence/OpenMassCrowd/open_mass_city_sample_runtime_latest.json)
  — `overall_passed=true`，30 可见、30 移动、30 外观；
- [独立导航与贴地报告](Evidence/OpenMassCrowd/open_mass_city_navigation_runtime_latest.json)
  — `overall_passed=true`，9 节点、22 lane、累计 443 行程、0 卡住；
- [LOD 往返报告](Evidence/OpenMassCrowd/open_mass_lod_transition_runtime_latest.json)
  — `overall_passed=true`，Low 30 → High 30 → Low 30；
- [08：Low 30](Evidence/OpenMassCrowd/08_lod_low_30.png)；
- [09：High 30](Evidence/OpenMassCrowd/09_lod_high_30.png)；
- [10：返回 Low 30](Evidence/OpenMassCrowd/10_lod_low_30_return.png)。

三份 JSON 的 SHA-256：

```text
0EF74D5589526890C0122900B96809B0439FE1441900408D9D5AB5EABFD4E4B6  open_mass_city_sample_runtime_latest.json
D7FA6F6445C05DC29473E2F25D3F05835874381A7E9C5A38E69A16FDA807A3F6  open_mass_city_navigation_runtime_latest.json
3DD7ED9915AAFE7053714775479BFC7CC2793A20F8E863C00A95F8BA5CFBF06F  open_mass_lod_transition_runtime_latest.json
```

## 当前限制

- 这是香港场景中的 **bounded demo**，不是全香港 NavMesh 或全城自动人行道路提取。
- lane 直线/弯曲段经过离散的 10 cm 级认证；路口 steering 转角仍依赖运行时
  exact-XY guard、中线恢复和 last-valid 回滚。不要把它描述为数学连续认证。
- `route_replans=0` 说明本轮没有恢复性重规划；当前没有动态道路封闭后的全局重规划。
- Mass avoidance 不是硬物理碰撞。报告存在 1 个 body-overlap 样本，不能声称绝对
  无碰撞。
- Low 表示仍是 skeletal low-res，不是 VAT/ISM；本轮只验证 30 人，不外推到数百或
  数千人的性能。
- 编辑器性能数字只是帧时间代理，不是整机发布版 FPS/显存基准。
- 协作者必须用自己的 Epic/Fab 授权取得 UE-Only 官方素材。

## 纯 Git 回滚

开始本轮导航改造前的远程 tag：

```text
checkpoint/pre-city-navigation-2026-07-18
```

不覆盖当前工作区的安全回滚方式：

```powershell
git fetch origin --tags
git switch -c rollback/pre-city-navigation checkpoint/pre-city-navigation-2026-07-18
```

该 tag 只管理 Git 内代码和工程文件，不包含 City Sample 官方素材、目录联接或本机
缓存。
