# TelecomTwin 人物信息面板全英文（2026-08-16）

## 目标

把点击人物后左下角显示的实时人物档案改为全英文。该修改只影响人物资料数据和 Slate 面板文字，不修改建筑信道、人物寻路、基站判定或启动脚本。

## 修改内容

- 300 个稳定人物槽位使用英文姓名组合；当前 100 人演示生成 100 个不重复英文姓名。
- 20 种职业全部改为英文。
- 性别与年龄改为 `Female / Male` 和 `AGE n`。
- `Octopus 八达通` 改为 `Octopus`，其他应用和软件名称保持英文。
- 进入建筑、室内断联、离开建筑和室外街道状态全部改为英文。
- 面板标题、关闭按钮和六个字段标签全部改为英文：
  - `SELECTED PERSON / LIVE PROFILE`
  - `CLOSE`
  - `OCCUPATION`
  - `GENDER & AGE`
  - `LIVE APP`
  - `LOCATION`
  - `SERVING NODE`
  - `SIGNAL QUALITY`
- 运行证据增加 `ui_language: en` 与 `display_text_english_only: true`。

## 实现文件

- `Plugins/OpenMassCrowd/Source/OpenMassCrowd/Private/OpenMassCrowdSpawner.cpp`
- `Scripts/OpenMassCrowd/verify_investor_delivery_demo_runtime.py`
- `Scripts/OpenMassCrowd/verify_investor_profile_english_runtime.py`

## 验证结果

- UnrealBuildTool Development Editor Win64：通过。
- 新 DLL SHA-256：`C05C69906F674993CB2982EC8833D7FDFE1D07DF29D820844714B0915663E31C`。
- 实际 PIE：100 人生成并显示，人物资料卡可见。
- 全量检查：100/100 个人物资料字段均无中文字符。
- 唯一英文姓名：100/100。
- `OpenMassCrowdSpawner.cpp` 中文字符数量：0。
- 运行时违规条目：0。
- 建筑信道回归：30 个信源、1920 条信道、1950 个原对象全部可见；运行时批次 0，变换误差 0，旧悬浮层可见数 0。

## 证据

- 窗口截图：`../Evidence/InvestorDelivery/18_english_person_profile_window_2026-08-16.jpg`
- 运行报告：`../Evidence/InvestorDelivery/investor_profile_english_runtime_2026-08-16.json`

![[../Evidence/InvestorDelivery/18_english_person_profile_window_2026-08-16.jpg]]

截图中 UE 编辑器菜单和 City Sample 加载提示仍可能采用本机中文语言环境；左下角人物资料面板本身已经全部为英文。

## OpenSpec 状态

该修订属于 `scale-central-crowd-to-300` 的既有人物演示实现范围。未把任何仍未完成的 30/200/300 人规模验收任务标记完成，整体进度仍为 17/26。
