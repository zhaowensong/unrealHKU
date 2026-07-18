# Epic City Sample Crowds 来源审计

审计日期：2026-07-18（香港时间）

| 字段 | 实测值 |
|---|---|
| 官方 Fab 条目 | `https://www.fab.com/listings/903037e9-e1ac-4f41-96e8-1683c6fa7ad4` |
| 安装包名称 | `CitySampleCrowd` |
| 精确安装版本 | `5.7.0-45212673+++UE5+Dev-Marketplace-Windows` |
| Manifest ID | `EyuRxJDzJkyDTfX8-uC8NQ` |
| Fab Listing ID | `903037e9-e1ac-4f41-96e8-1683c6fa7ad4` |
| 安装目录 | `D:\CitySampleCrowds_Staging` |
| 工程挂载目录 | `Content\CitySampleCrowd` |
| 文件数 | `1434` |
| 安装字节数 | `6,505,136,712` |
| 验证引擎 | `5.7.4-51494982+++UE5+Release-5.7` |
| 安装结果 | `ProcessSuccess: TRUE` |

## 本机原始证据

来源日志：`D:\TelecomTwinUser\Saved\Logs\BuildPatchInstallerLib.log`

日志 SHA-256：
`747EC9FCC7168E2BA23E15FD99566250FABFF24514B7A4F8A79F36943B4AA4F3`

关键行：

- 第 23 行：安装包名称、精确版本和 Manifest ID。
- 第 25 行：安装目录。
- 第 26 行：Fab Listing ID 对应的 staging 目录。
- 第 39、1493、1521 行：1434 个文件。
- 第 1515、1518 行：读取及写入 6,505,136,712 bytes。
- 第 1534 行：`ProcessSuccess: TRUE`。

## 许可与仓库边界

City Sample Crowds 是 Epic 提供的 UE-Only Content，只用于 Unreal Engine 产品。
原始 `.uasset` 不进入公开 Git；协作者需要以自己的 Epic/Fab 权限取得内容，然后用
`Scripts/OpenMassCrowd/link_city_sample_crowds.ps1` 建立本地目录联接。项目源码仅
保存软引用、接入代码、挂载脚本和审计记录。
