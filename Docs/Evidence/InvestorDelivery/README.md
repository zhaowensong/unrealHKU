# Investor Delivery Evidence

本目录只保留最终交付证据，不包含视觉试拍废片。

1. `01_rooftop_network_overview.png`：干净的中环屋顶总览、双节点、KPI 和已连接人物卡。
2. `02_real_rooftop_stations.png`：两个真实屋顶节点，同时捕获人物入楼后的断联状态。
3. `03_crowd_aerial_profile.png`：地面路线上的人群俯视与完整人物档案辅助证据。
4. `03_spread_ground_paths_50.png`：50 人版本的分散路径基线截图。
5. `04_central_100_performance.png`：100 人稳定运行时的当前 PIE 视图；摄影测量近地面破碎几何属于源场景局限。
6. `investor_delivery_100_runtime_latest.json`：100 人完整机器验收报告，所有检查为 `true`。
7. `investor_100_long_run_liveness_latest.json`：100 人 60 秒持续运动报告，卡住峰值为 0。
8. `05_people_and_persisted_signal_overview_2026-08-10.png/.json`：修复后 PIE 中人物与原始四色信道同场总览；JSON 记录 30 + 1920 对象和 9 / 1950 批次。
9. `06_persisted_rooftop_landing_detail_2026-08-10.png/.json`：修复后屋顶区域近景与相机信息。
10. `07_signal_alignment_acceptance_2026-08-10.json`：冷重启后一键验收摘要；编辑器态与 PIE 的位置、旋转、缩放最大误差均为 0。
11. `08_legacy_floating_signal_layer_removed_2026-08-11.png/.json`：同一 PIE 中只保留建筑屋顶落点网络；本镜头加载 294 个旧模拟信道对象，可见数为 0。

2026-08-10 的第 8–10 项只验证了新批次与 1950 个关卡对象坐标一致，没有检查 World Partition 后加载的 `SIG_RaySegment_* / SIG_Node_* / SIG_Source_Main` 旧层，因此不能单独作为“双层信道已消除”的证明。最终结论以第 11 项和 2026-08-11 冷启动报告为准。

截图用于解释视觉结果；是否完成以 JSON、构建结果和重启验收为准。按用户要求没有最终验收视频。
