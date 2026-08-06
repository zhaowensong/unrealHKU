# TelecomTwin 一键演示说明

## 接收方唯一操作

1. 将完整项目解压到本机 SSD；不要直接在压缩包、网盘虚拟盘或只读目录中运行。
2. 安装与项目一致的 Unreal Engine 5.7，并确保 City Sample Crowds 内容已随交付项目存在。
3. 双击项目根目录的 `启动TelecomTwin演示.bat`。
4. 启动窗口会在拉起后台就绪检查后自动关闭，UE 保持前台，避免因失去前台而降到 3 FPS。
5. 等待 UE 中出现人群。画面显示绿色 `DEMO READY | 100 PEOPLE` 后才代表验收完成；机器状态同时写入 `Saved/InvestorDeliveryDemo/one_click_demo_status.json`。
6. 结束演示时在 UE 中按 `Esc`。

不需要 Codex，不需要手动启动外部 MCP Server，不需要按 `Alt+P`，也不需要运行任何 setup 或镜头脚本。

## 一键脚本实际完成的工作

- 从参数、环境变量、UE 注册表和常见路径中查找 UE 5.7。
- 若没有 UE 实例，使用固定 8 核、单并发资产/纹理编译启动 `shanghai`。缓存优先使用项目当前所在盘根目录下动态生成的 `TelecomTwinDemoCache`；盘符不是写死的，位置不可写时才回退到当前用户 `%LOCALAPPDATA%`，同时避免过长解压路径触发 UE 的 DDC 限制。
- 若已有一个 UE 实例，只在确认它就是当前 TelecomTwin 项目后复用；检测到多个实例则失败，防止重复 UE 抢占内存和显存。
- 通过项目自带的 UnrealMCP 本地接口自动进入 Play。
- 在就绪等待期间关闭 UE 的后台 3 FPS 节流，避免进度窗口处于前台时让演示误判为卡顿。
- 先等待配置、生成和准入达到 100 人且至少 95 人移动；即使默认远镜头让显示数暂时为 0，也会立即进入镜头定位，不形成循环等待。
- 强制要求 stuck、unsupported、invalid position 和 severe overlap 均为 0。
- 自动把镜头移动到最密集的地面人群，并等待至少一个近景骨骼人物出现。
- 镜头定位后再强制要求显示数达到 100，避免“100 people 计数存在但画面没有人物”。
- UE 在前台时要求稳定 P95 帧时低于 33 ms；若完整采样或少量失焦帧落在约 333 ms，脚本会将它标记为 UE 后台 3 FPS 节流而不是硬件性能失败，并提示保持 UE 前台。状态文件分别记录 `performance_verified` 与 `background_throttle_detected`，不会伪造 P95 数字。
- 双击模式使用 UE 自带的无窗口 Python 在后台检查，避免命令窗口占据前台；失败时会弹出 Windows 错误框。
- 将最后状态写入 `Saved/InvestorDeliveryDemo/one_click_demo_status.json`。

## 推荐硬件

- Windows 11
- 8 核以上 CPU
- 32 GB 内存
- RTX 3060/4060 或更高，至少 8 GB 显存
- NVMe SSD
- 推荐 1920×1080 演示

首次运行会建立本地 DDC/Zen 缓存，通常比后续运行慢。演示前建议至少完整启动一次作为预热。

第一次冷启动时，Windows 可能短暂显示 UE“未响应”。如果任务管理器中 UE 仍在使用 CPU，且动态缓存目录下的 `User/Saved/Logs/TelecomTwin.log` 持续出现“正在构建纹理/骨骼网格体”，说明它正在建立缓存，不要结束进程或重复双击。后续启动会复用这些缓存。

## UE 安装在自定义位置

如果脚本不能自动发现 UE 5.7，设置用户环境变量后重新双击：

```text
TELECOMTWIN_UNREAL_ROOT=<UE 5.7 安装目录>
```

也可以直接从 PowerShell 调用：

```powershell
pwsh -ExecutionPolicy Bypass -File `
  .\Scripts\OpenMassCrowd\start_investor_delivery_demo.ps1 `
  -UnrealRoot "<UE 5.7 安装目录>"
```

## 失败时如何处理

- “多个 Unreal Editor 实例”：关闭所有 UE 后重新双击。
- “检测到的是其他 UE 项目”：关闭该 UE 项目后重新双击。
- “City Sample Crowds is not mounted”：交付包缺少受 Epic UE-Only 许可约束的 City Sample Crowds 内容，必须由接收方自己的 Epic 授权内容补齐。
- “等待稳定演示画面超时”：不要启动第二个 UE；保留窗口错误并查看 `Saved/InvestorDeliveryDemo/one_click_demo_status.json`。
- 画面显示 100 people 但看不到人物：一键脚本不应在此状态宣布成功；它会继续自动定位镜头并等待近景角色。如果手动移动了镜头，重新双击脚本可复用当前 TelecomTwin 实例并再次定位。

## 演示就绪硬指标

一键脚本只有同时满足以下条件才返回成功：

| 指标 | 要求 |
| --- | ---: |
| 配置 / 生成 / 准入 / 显示 | 100 / 100 / 100 / 100 |
| 移动人数 | 至少 95 |
| 卡住人数 | 0 |
| unsupported / invalid / overlap | 0 / 0 / 0 |
| 近景骨骼人物 | 至少 1 |
| P95 帧时 | UE 前台时小于 33 ms；约 333 ms 后台节流必须显式标记并将 UE 前置 |

## 本机回归结果（2026-08-06）

- 关闭全部 UE 后从根目录双击：通过环境变量、引擎注册信息或标准安装目录动态发现 UE 5.7，启动唯一 UE 实例，自动进入 Play 并完成首次派生缓存构建。
- 冷启动最终状态：配置 / 生成 / 准入 / 显示均为 100，移动 100，卡住 0。
- 复用同一 UE 再次双击：配置 / 生成 / 准入 / 显示均为 100，移动 99，卡住 0，unsupported / invalid / overlap 均为 0。
- 复用画面 LOD：4 个高精度骨骼人物、16 个低精度骨骼人物、80 个远景 VAT 人物，共 100 人；活动路径 33 条。
- 前台同步性能基线：P95 20.087 ms（通过 33 ms 门槛）。Codex 自动化窗口抢占焦点时会记录约 333 ms 并明确标记 `background_throttle_detected=true`，该数字不是伪装成正常性能的结果。
