# TelecomTwin 100 人香港中环演示：从这里开始

版本日期：2026-08-07  
适用引擎：Unreal Engine 5.7

## 一、解压两个压缩包

本次交付包含：

1. `01_TelecomTwin_Project_and_Content.7z`
2. `02_TelecomTwin_Intermediate.7z`

请将两个压缩包都解压到同一个新的空目录。第二个压缩包会把 `Intermediate` 放入第一个压缩包生成的 `TelecomTwin` 目录中。最终结构应为：

```text
TelecomTwin/
├─ Binaries/
├─ Config/
├─ Content/
│  └─ CitySampleCrowd/       # 已实体化，不是本机目录链接
├─ Intermediate/
├─ Plugins/
│  ├─ CesiumForUnreal/
│  ├─ VaRest/
│  ├─ UnrealMCP/
│  └─ OpenMassCrowd/
├─ Scripts/
├─ 启动TelecomTwin演示.bat
└─ TelecomTwin.uproject
```

不要覆盖到旧的同名工程目录。

## 二、一键启动

1. 安装 Unreal Engine 5.7。
2. 双击 `启动TelecomTwin演示.bat`。
3. 脚本会自动打开或复用正确的 TelecomTwin 项目、进入 Play、定位香港中环人群并检查 100 人状态。
4. 只有 UE 画面出现绿色 `DEMO READY | 100 PEOPLE` 才代表演示已经就绪。
5. 结束演示时按 `Esc`。

不需要 Codex，不需要手工启动外部 MCP Server，不需要按 `Alt+P`，也不需要运行生成脚本。

UE 安装在自定义位置且脚本无法发现时，请设置用户环境变量：

```text
TELECOMTWIN_UNREAL_ROOT=D:\你的路径\UE_5.7
```

## 三、第一次启动

第一次启动会建立本机纹理、骨骼网格和 VAT 派生缓存，可能比后续启动慢。Windows 暂时显示 UE“未响应”但任务管理器仍有明显 CPU 使用时，请继续等待，不要结束进程或重复双击。后续演示会复用缓存。

为了获得正确帧率，请在演示期间保持 UE 为前台窗口。UE 编辑器失去焦点时可能主动降到约 3 FPS，这不是人群算法卡住。

## 四、交付内容

- 香港中环 Cesium 城市场景和 30 个信号源四颜色射线路径。
- 100 个地面行人，包含分散路径、持续移动、近景骨骼动画和远景 VAT 动画。
- 点击人物信息面板及相关演示功能。
- City Sample Crowds 人物内容已经复制为真实文件，不依赖发送方的 D 盘目录链接。
- Cesium for Unreal 与 VaRest 已放入项目 `Plugins`，不要求接收方再单独寻找相同插件包。

## 五、完整性和许可提醒

上传、下载后请使用交付目录中的 `SHA256SUMS.txt` 比对两个压缩包的 SHA-256。

本交付包含 Epic UE-Only 的 City Sample Crowds 内容以及 Cesium/Google Tileset 相关数据，只适合在符合相应许可的 Unreal Engine 项目中使用。请通过百度网盘等私密渠道交付，不要公开发布或作为独立素材重新分发。

更详细的启动和故障说明见 `Docs/TelecomTwin一键演示说明.md`。
