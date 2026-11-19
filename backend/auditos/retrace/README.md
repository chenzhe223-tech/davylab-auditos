# DAVYLAB AuditOS — CAD Image Retrace (P0 module)

独立实现的栅格线稿 → 标定 DXF（含原生 DIMENSION 实体）管线。本模块
**不包含**来自 `pengxiaoan/autocad-dwg-redraw-skill` 的任何代码或实现细节：

- 该上游仓库经两次核验（2026-11-13、2026-11-19，GitHub API `license: null`）
  均无 LICENSE 文件，无集成授权。见 `docs/THIRD_PARTY_ENGINE_REGISTER.md`。
- 本模块由 DAVYLAB 独立编写（clean-room），能力为保守的结构线提取：
  只输出经形态学验证的水平/垂直主导线、外框与全跨度分隔线，
  并显式记录单位声明来源（declared / assumed / unitless）。

## 能力边界（诚实声明）

| 能力 | 状态 |
|---|---|
| 外框/分隔检测 + 多锚标定 | 已实现；像素量化包络 ±2px（按图源分辨率） |
| 原生 DIMENSION 实体 | 已实现（`$INSUNITS` 显式、`dimlfac=1`） |
| 人工读图文字标签 | 支持 JSON 输入（非 OCR，来源标记 human-read） |
| OCR / 语义识别 | **未实现**，不在本模块宣称范围 |
| 渲染图/照片语义提取 | 不支持（负对照确认：产生无意义线条） |
| DWG 写出 | 经 ODA File Converter 外部调用（round-trip 验证通过） |
| AutoCAD GUI 打开性 | **BLOCKED**：需授权 Windows + AutoCAD 环境 |

## 用法

```bash
python pipeline.py --image plan.png --out out.dxf \
  --unit mm --declare-mm-assumed --width-mm 3200 --height-mm 1180 \
  --chain-mm 2000 100 500 600 --dims \
  --labels-json labels.json \
  --evidence evidence.json --png preview.png
```

每次运行输出证据 JSON：输入/输出 SHA-256、代码版本哈希、命令、软件版本、
标定锚点残差（mm 与 %）、audit 结果。未提供标定锚时 `$INSUNITS=0`
（unitless），**不会**像部分上游工具那样把像素单位误标为 mm。

## P0 Round 1 实测（2026-11-19，1209×696 端架平面图）

- 校验锚：宽 3200（标定基准）、高 1180、链 2000/100/500/600（人工读图）
- 残差：高 −0.85mm（−0.07%）~ −9.4mm（−0.79%，视边缘取法）；
  链段 +2.4 / +0.1 / +4.5 / −7.0 mm（最差 −1.16%）
- 6 个原生 DIMENSION 经 ODA ACAD2018 DWG 写出→回读后测量值逐一保持
- 完整证据：工作区 `davylab-cad/round1/`（哈希、命令、输出、误差对照）

## 与 drawings（takeoff）模块的关系

本模块产出的 DXF 与证据记录可作为 `feature/drawing-takeoff-engine`
的证据输入：重绘尺寸标注对应其 confidence_class `B/annotation`，
描迹几何对应 `approximate`，需人工复核后才可进入算量。
