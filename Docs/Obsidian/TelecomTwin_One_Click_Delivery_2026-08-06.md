# TelecomTwin 一键交付演示改造（2026-08-06）

## 目标

让接收方在没有 Codex、没有外部 MCP Server、不了解 UE 操作的情况下，只双击一个文件，就能正确看到香港中环 100 人持续行走的演示，而不是只看到 `100 people` 计数或空镜头。

## 接收方操作

双击项目根目录的 `启动TelecomTwin演示.bat`，等待 UE 画面显示绿色：

```text
DEMO READY | 100 PEOPLE
```

结束演示时按 `Esc`。完整交付说明见 [[../TelecomTwin一键演示说明]]。

## 自动化链路

1. 自动发现 UE 5.7；如果本机使用自定义目录，可读取 `TELECOMTWIN_UNREAL_ROOT`。
2. 没有 UE 时只启动一个 TelecomTwin；已有一个 UE 时先验证项目再复用；多个 UE 时明确失败。
3. 使用项目随附的 UnrealMCP 本地接口自动进入 Play，不依赖 Codex 或外部 MCP 进程。
4. 先验证配置、生成、准入均为 100，至少 95 人移动，且卡死、离地、非法道路位置和严重重叠均为 0。
5. 自动定位人群密集区域；镜头定位后必须显示 100 人，并且至少一个近景骨骼人物已经加载，才能宣布就绪。
6. 在 UE 前台严格检查 P95 小于 33 ms。UE 失焦导致的约 333 ms 固定节流会单独标记，不修改或伪造帧时。
7. 将完整结果保存到 `Saved/InvestorDeliveryDemo/one_click_demo_status.json`；失败时弹出错误框。

## 实现文件

- `启动TelecomTwin演示.bat`：交付给接收方的唯一入口。
- `Scripts/OpenMassCrowd/start_investor_delivery_demo.ps1`：UE 查找、单实例保护、缓存路径和前台窗口控制。
- `Scripts/OpenMassCrowd/orchestrate_investor_delivery_demo.py`：Play、100 人、镜头、地面安全、LOD 和性能就绪门槛。
- `Docs/TelecomTwin一键演示说明.md`：接收方说明与故障处理。

## 2026-08-06 验收

### 冷启动

- 从零个 UE 实例双击成功，自动启动 `D:\astrea\UE_5.7`。
- 人群配置 / 生成 / 准入 / 显示：100 / 100 / 100 / 100。
- 移动 100，卡住 0。
- 首次加载建立 CitySample 人物纹理、骨骼网格和 VAT 派生缓存；CPU 持续工作，完成后自动进入就绪状态。

### 已启动项目复用

- 再次双击后数秒内完成就绪检查。
- 人群配置 / 生成 / 准入 / 显示：100 / 100 / 100 / 100。
- 移动 99，卡住 0；unsupported / invalid / overlap：0 / 0 / 0。
- 高精度骨骼人物 4、低精度骨骼人物 16、远景 VAT 80；活动路径 33 条。
- UE 日志出现 `DEMO READY | 100 PEOPLE | KEEP UE FOREGROUND`。

### 性能解释

- 已有前台同步基线：P95 20.087 ms，满足 100 人演示的 P95 < 33 ms 门槛。
- Codex 测试工具抢占窗口焦点时，UE 编辑器会按自身规则降到约 3 FPS，因此状态文件如实记录约 333 ms，并设置 `background_throttle_detected=true`。接收方正常双击后保持 UE 前台即可避免这一编辑器行为。

## 相关画面证据

![[../Evidence/OpenMassCrowd/08_lod_low_30.png]]

![[../Evidence/OpenMassCrowd/09_lod_high_30.png]]

这些既有截图用于说明人物 LOD 的近远切换；本次新增验收的权威数据是状态 JSON 和 UE 日志中的 READY 标记。
