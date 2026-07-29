# TelecomTwin Central 100 人与远距步态优化

> 日期：2026-07-30（香港时间）
> 地图：`/Game/Maps/shanghai`
> OpenSpec：`enhance-central-crowd-experience` 第 7 组任务

## 本次目标

在不改变已经完成的地面寻路、人物档案 UI 和通信射线功能的前提下：

1. 将中环运行人口从 300 人收敛到准确的 100 人；
2. 保持所有人物只使用经过认证的 Ground-Only 路线；
3. 让远处 VAT 人物仍能看到手脚步态变化，不能只是整个人形平移；
4. 再次联合验证 30 个信源和四色射线没有被人群改造破坏。

## 实现

### 1. 精确 100 人

- 运行目标、DataAsset、验证器和身份档案统一为 `HK-C-001` 至 `HK-C-100`。
- 六个生成区配额为 `17、17、17、17、16、16`，总数严格等于 100。
- 旧地图中保存的 300 人档位在运行时被安全上限收敛为 100，因此不需要修改用户原有地图 Actor。
- Ground-Only 路网本身没有重做：仍为 562 条 lane、49 个连通分量、6 个生成区。

### 2. 远处人物不是平移

- 远距表示继续使用 City Sample VAT/ISM，不为 100 人生成独立 Actor。
- 98 帧走路片段使用固定 `1.35x` 播放速率。原先随速度逐帧改变播放速率会因为 VAT 使用绝对世界时间而产生相位跳变；固定速率保证步态相位连续。
- 每个人保留独立 `TimeOffset`，所以不会所有人同手同脚。
- 证据接口可按稳定人物编号连续追踪同一个 VAT 实例，并返回位置、真实移动速度、播放范围、当前帧和物理相机距离。
- 远距参考机位与人物的实际距离约 82 米；使用 35° 长焦视场只是为了让 1600×900 截图能看清手脚，未缩短物理距离。

## 远距成对截图

第一帧：

![[Docs/Evidence/OpenMassCrowd/CentralCrowdExperience/12_central_100_far_gait_a.png]]

第二帧（游戏世界时间真实推进 0.907 秒后）：

![[Docs/Evidence/OpenMassCrowd/CentralCrowdExperience/13_central_100_far_gait_b.png]]

机器报告：

- 同一人物：`HK-C-058`（stable index 57）；
- 相机距离：82.301 m → 82.510 m；
- 真实速度：119.999 cm/s → 65.605 cm/s；
- VAT 帧：13.344 → 50.065；
- 播放速率：两帧均为 1.35，说明帧变化来自连续播放而不是速率跳变；
- 结果：`passed=true`。

报告文件：[[Docs/Evidence/OpenMassCrowd/CentralCrowdExperience/far_gait_pair_latest.json]]

## 联合运行验收

`central_crowd_experience_runtime_latest.json` 最终为 `passed=true`：

| 项目 | 结果 |
|---|---:|
| target / admitted / simulated / represented | 100 / 100 / 100 / 100 |
| moving / expected moving / stuck | 100 / 100 / 0 |
| unsupported / invalid / admission violation | 0 / 0 / 0 |
| severe overlap pairs / agents | 0 / 0 |
| 完成路径段 | 48 → 52 |
| 档案 UI | 半透明面板可见，姓名、职业、软件、区域与路线字段完整 |
| 通信信源 | 30 |
| 射线几何 | 1920 |
| 绿 / 黄 / 橙 / 红 | 480 / 480 / 480 / 480 |

运行报告：[[Docs/Evidence/OpenMassCrowd/central_crowd_experience_runtime_latest.json]]

## 复现

```powershell
pwsh -ExecutionPolicy Bypass -File .\Scripts\OpenMassCrowd\launch_telecomtwin_citysample.ps1
```

编辑器打开后按 `Alt+P`，等待约 10 秒，再执行：

```powershell
python .\Scripts\OpenMassCrowd\run_unreal_python_via_mcp.py `
  --file .\Scripts\OpenMassCrowd\set_central_runtime_lod_camera.py `
  --script-arg=--tier --script-arg=far

python .\Scripts\OpenMassCrowd\capture_central_far_gait_pair.py
python .\Scripts\OpenMassCrowd\verify_central_crowd_experience_runtime.py
```

## 回滚

本次优化前的远程提交是 `2b55adc2`。要查看此前的 300 人版本：

```powershell
git switch --detach 2b55adc2
```

最初的四项体验升级基线仍保留为：

```text
checkpoint/central-crowd-experience-baseline-2026-07-27
```

## 边界

- 这是中环局部数字孪生 demo，不是全香港人行道路自动生成系统。
- 远景人物使用 VAT，轮廓和动作已连续变化，但材质精度受现有 City Sample VAT 与 Cesium 摄影测量画质限制。
- 35° 长焦是观察手段；机器报告中的 82 米是实际世界坐标距离。
- 本次没有修改用户原有 7 个脏文件；哈希审计仍全部通过。
