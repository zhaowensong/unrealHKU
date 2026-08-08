# TelecomTwin 百度网盘便携双包交付记录（2026-08-07）

## 最终交付目录

```text
D:\TelecomTwin_Delivery_100People_2026-08-07\发送给对方
```

只上传这个目录。旁边名字含“不要发送”的目录是暂存、错误包和全量解压验证副本。

## 第一性问题：原工程为什么不能直接压缩

### City Sample 是本机 Junction

原工程的 `Content/CitySampleCrowd` 指向发送方 D 盘。直接压缩会让接收方得到失效链接、看不到人物。本次将 1,434 个 City Sample Crowd 文件复制为压缩包中的真实文件；最终解压检查确认 Junction/ReparsePoint 为 0。

### Intermediate 不可移植

初版沿用旧交付方式制作了 `Intermediate` 包。二进制扫描明确发现其中包含发送方的 `C:\Users\15958...`、`D:\astrea...`、编译响应文件、UHT 路径和 Makefile 缓存。它在发送方本机能运行，不代表能在接收方机器安全复用。

最终交付彻底取消 `Intermediate` 包。UE 会在接收方电脑按需生成自己的 `Intermediate`，避免本机绝对编译路径污染。

### UE 与缓存不能绑定发送方盘符

一键启动链已删除固定 UE 路径和固定 D 盘缓存：

- UE 5.7 通过 `TELECOMTWIN_UNREAL_ROOT`、当前运行实例、Unreal 注册信息或系统标准安装目录动态发现。
- 缓存优先使用“解压项目当前所在盘”的根目录 `TelecomTwinDemoCache`；盘符由项目位置动态决定。
- 如果该盘根目录不可写，才回退到当前 Windows 用户 `%LOCALAPPDATA%`。
- 可通过 `TELECOMTWIN_DEMO_CACHE_ROOT` 显式覆盖，但接收方正常演示不需要设置。

曾尝试将缓存放在项目自身 `Saved/DemoCache`。在故意构造的深层验证目录中，UE 弹出 DDC 路径 119 字符过长提示。最终短路径策略解决了这个问题，并且不固定 C/D 盘。

## 最终双包

### 01 项目核心

```text
01_TelecomTwin_Project.7z
```

- 原始内容：1,982,123,655 字节
- 压缩后：945,454,299 字节
- 文件：8,011
- SHA-256：`1C5341EDC05DDF3DD52AEBFCF587206B62506334BF665F66705D5A0056A1BA37`
- 包含工程、地图、已编译项目插件、一键入口、射线系统、文档和 Cesium 城市请求缓存。
- 不包含 `Intermediate`、City Sample Crowd、Cesium 项目插件或 VaRest；后三项由第二包合并。

### 02 可移植依赖

```text
02_TelecomTwin_Portable_Dependencies.7z
```

- 原始内容：8,318,857,948 字节
- 压缩后：5,731,238,795 字节
- 文件：4,627
- SHA-256：`AB8D3733EE2A1FECD78E1CAA09BE64A9DDE3FC846A59D76F1630ECDB6E1AF1F5`
- 包含真实 City Sample Crowd、项目本地 Cesium for Unreal 和项目本地 VaRest。

两个压缩包都通过 `7z t` 完整数据测试。

## 最终全量解压验证

两个最终包重新解压到一个路径很深、包含中文的全新目录，以主动覆盖常见路径问题。结果：

| 检查 | 结果 |
| --- | ---: |
| 总文件数 | 12,638 |
| 总字节数 | 10,300,981,603 |
| City Sample Crowd 文件 | 1,434 |
| `Intermediate` 是否存在 | 否 |
| Junction/ReparsePoint | 0 |
| 一键启动链发送方固定路径 | 0 |
| Cesium/VaRest 项目插件 | 存在 |

## 从最终解压副本实际启动

没有传入 `-UnrealRoot` 或缓存路径，直接双击最终解压副本的 `启动TelecomTwin演示.bat`：

- UE 5.7 由注册信息动态发现。
- 深层工程路径没有被用作 DDC 路径。
- 缓存根据项目所在盘动态选择为短路径。
- 没有再出现路径长度弹窗。
- 项目进入 Play 并达到 `ready=true`。
- 配置 / 生成 / 准入 / 显示：100 / 100 / 100 / 100。
- 移动 99，卡住 0，unsupported / invalid / overlap 为 0 / 0 / 0。
- 高/低骨骼人物 6 / 10，VAT 84，活动路径 26。
- Codex 检查窗口抢占焦点时记录 `background_throttle_detected=true`；这是 UE 编辑器失焦 3 FPS 行为。此前同一渲染配置在前台解压副本测试的 P95 为 32.054 ms，通过 33 ms 门槛。

机器可读结果随最终发送目录保存为 `verification_portable_runtime_status.json`。验证完成后已正常结束 PIE 并关闭 UE。

## 接收方操作

1. 下载“发送给对方”目录中的全部文件。
2. 校验 `SHA256SUMS.txt`。
3. 将两个 7z 解压到同一个新的空目录。
4. 安装 UE 5.7。
5. 双击 `TelecomTwin/启动TelecomTwin演示.bat`。
6. 等待绿色 `DEMO READY | 100 PEOPLE`。

本交付包含 Epic UE-Only 内容以及 Cesium/Google Tileset 相关数据，只通过私密百度网盘渠道发送，不公开分发。

## 2026-08-08 接收方 PowerShell 5.1 兼容修复

接收方首次运行时出现中文乱码以及 `ParserError`、`Unexpected token`。根因不是 UE 或工程资源，而是接收方没有 PowerShell 7，批处理自动回退到 Windows PowerShell 5.1；后者会把没有 BOM 的 UTF-8 脚本按本地 ANSI 编码读取，乱码进一步破坏了中文字符串两侧的引号。

修复内容：

- `start_investor_delivery_demo.ps1` 改为带 BOM 的 UTF-8。
- `launch_telecomtwin_citysample.ps1` 改为带 BOM 的 UTF-8。
- 保留批处理自动回退逻辑，接收方无需安装 PowerShell 7。
- 两个脚本分别使用 Windows PowerShell 5.1 与 PowerShell 7 的语法解析器验证，四项结果均为通过。
- 重新生成 `01_TelecomTwin_Project.7z` 并更新 SHA-256；第二个依赖压缩包内容不变。
