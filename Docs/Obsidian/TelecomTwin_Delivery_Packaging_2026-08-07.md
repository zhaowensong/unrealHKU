# TelecomTwin 百度网盘双包交付记录（2026-08-07）

## 交付目录

```text
D:\TelecomTwin_Delivery_100People_2026-08-07\发送给对方
```

该目录中的文件可以一起上传到百度网盘。接收方把两个 7z 解压到同一个新目录，然后双击 `TelecomTwin/启动TelecomTwin演示.bat`。

## 为什么不能直接压缩原工程

原工程中的 `Content/CitySampleCrowd` 是 Windows Junction，指向：

```text
D:\CitySampleCrowds_Staging\Content\CitySampleCrowd
```

如果直接压缩，接收方只会得到失效的 D 盘链接，看不到人物。本次暂存时使用真实目录复制了 1,434 个 City Sample Crowd 文件，共约 6.06 GB；全量解压后再次确认不存在真实文件系统链接。

项目原先还依赖 UE 安装目录中的 Cesium for Unreal 与 VaRest。本次把两者作为项目本地插件放入 `TelecomTwin/Plugins`，并保留 Binaries、Source、Content、Config、Shaders 和许可文件；插件自己的 `Intermediate` 未重复打包。

## 双包结构

### 01 项目与内容

```text
01_TelecomTwin_Project_and_Content.7z
```

- 原始内容：10,300,975,118 字节
- 压缩后：6,677,343,052 字节
- 文件：12,638
- SHA-256：`6D99FB6EA7E1DA79967562B19D71A2DF79555BAE729128DFFA7D15D88141CA07`
- 包含工程、真实 City Sample Crowd、Cesium/VaRest、Cesium 城市缓存、一键启动入口和交付文档。

### 02 编译中间文件

```text
02_TelecomTwin_Intermediate.7z
```

- 原始内容：5,651,532,617 字节
- 压缩后：762,922,342 字节
- 文件：244
- SHA-256：`567BA5C7F41B0CBBEC3103AD895BFBBD09596DC31B57879839C913ECA0497E77`
- 解压路径固定为 `TelecomTwin/Intermediate`。

两个压缩包都通过 `7z t` 完整数据测试。

## 全量解压验证

验证目录：

```text
D:\TelecomTwin_Delivery_100People_2026-08-07\verification_extract\TelecomTwin
```

两个最终压缩包被重新解压到空目录后，检查结果：

- 总文件数：12,882
- 解压后总大小：14.857 GB
- City Sample Crowd 文件：1,434
- Intermediate 文件：244
- 真实 ReparsePoint/Junction：0
- 一键启动、地图、人物内容、Cesium、VaRest、OpenMassCrowd、UnrealMCP 和编译文件均存在。

第一次生成第二包时，完整解压检查发现它把 `Intermediate` 放在了交付根目录，而不是 `TelecomTwin` 内。该错误包已经移出“发送给对方”目录；最终第二包重建后再次执行 7-Zip 测试和全量解压，路径已修正。不要发送暂存目录中的旧包。

## 从解压副本实际启动

不是使用原工程，而是在全新解压副本中双击一键入口。验证结果：

| 指标 | 结果 |
| --- | ---: |
| 配置 / 生成 / 准入 / 显示 | 100 / 100 / 100 / 100 |
| 移动人数 | 100 |
| 卡住人数 | 0 |
| unsupported / invalid / overlap | 0 / 0 / 0 |
| 高 / 低骨骼人物 | 5 / 24 |
| VAT 人物 | 71 |
| 活动路径 | 31 |
| P50 / P95 | 19.100 / 32.054 ms |
| 性能门槛 | 通过，`performance_verified=true` |

机器可读结果随交付目录保存为 `verification_runtime_status.json`。验证完成后已正常结束 PIE 并关闭 UE。

## 接收方操作

1. 下载“发送给对方”目录里的全部文件。
2. 校验 `SHA256SUMS.txt`。
3. 把两个 7z 解压到同一个新目录。
4. 安装 UE 5.7。
5. 双击 `TelecomTwin/启动TelecomTwin演示.bat`。
6. 等待绿色 `DEMO READY | 100 PEOPLE`。

本交付包含 Epic UE-Only 内容以及 Cesium/Google Tileset 相关数据，只通过私密百度网盘渠道发送，不公开分发。
