# 图纸算量引擎本地安装配置

AuditOS 本地优先运行。原生 DXF 几何内核依赖 `ezdxf`，随 AuditOS 后端安装；三个上游引擎按需独立安装。外部引擎未安装时，DXF 原生算量仍可工作。

## 1. AuditOS 原生 DXF 几何内核（必需）

在 AuditOS 后端虚拟环境中安装：

```bash
cd backend
pip install -e ".[dev]"
```

当前后端依赖 `ezdxf>=1.4,<2`。原生内核支持 DXF 模型空间 LINE、ARC、CIRCLE、LWPOLYLINE、二维 POLYLINE 和顶层 INSERT；支持常见 $INSUNITS 转换及实体级证据输出。

## 2. DWG Reader Skill（DWG 转换与结构候选）

上游：https://github.com/kasc0206/dwg-reader-skill

```bash
git clone https://github.com/kasc0206/dwg-reader-skill
cd dwg-reader-skill
pip install -e ".[dxf,xlsx]"
dwg-reader --version
dwg-reader doctor --format json
```

正式 CLI 契约：
- `dwg-reader convert <input.dwg> <output_dir>`：转换到指定目录，产物为同名 DXF。
- `dwg-reader read <file.dxf> --format json --entities`：结构化读取。
- `dwg-reader quantity <file.dxf> -o <result.csv> --sections components,layers,dimensions`：导出候选清单。

DWG 需要单独安装 ODA File Converter，或配置 `DWG_READER_ODA_PATH`。AuditOS 将转换产物放在独立 run 目录，不覆盖原图。

## 3. CAD AI Agent（确定性分析与区域识别）

上游：https://github.com/jeremylongshore/cad-ai-agent

```bash
git clone https://github.com/jeremylongshore/cad-ai-agent
cd cad-ai-agent
python -m venv .venv
# Windows PowerShell: .venv\\Scripts\\Activate.ps1
# macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
cad-analyze --version
cad-analyze takeoff drawing.dxf --json
cad-analyze zones drawing.dxf --json
```

AuditOS 仅调用上游公开的 `cad-analyze` 确定性 CLI（takeoff / zones / summary 等）。`cad-dxf-agent` 是桌面程序入口，不作为无头分析 CLI。Mock LLM 仅用于开发测试，不作为生产分析证据；真实 Planner/LLM 集成需另行授权与锁定模型接口。

## 4. DXF Civil（大图摘要、渲染和初步交叉检查）

上游：https://github.com/pedroschz/dxf-civil

```bash
git clone https://github.com/pedroschz/dxf-civil
cd dxf-civil
pip install "ezdxf[draw]" matplotlib pyyaml
```

设置环境变量 `AUDITOS_DXF_CIVIL_DIR` 指向本地仓库根目录。公开脚本契约：
- `python scripts/summary.py drawing.dxf`：JSON 输出。
- `python scripts/render.py drawing.dxf --out preview.png`：生成预览。
- `python scripts/takeoff.py drawing.dxf --layers "LAYER-A*,LAYER-B*"`：JSON 初步算量。
- `python scripts/spatial.py drawing.dxf --near x,y --radius r` 或 `--bbox xmin,ymin,xmax,ymax`：空间筛选，必须提供坐标范围。

上游 takeoff 明确是 preliminary：多段线长度辅助函数忽略 bulge；HATCH 面积使用包围盒近似。AuditOS 只将其作为交叉检查，不作为正式计量结果。

## 5. API 使用

启动 AuditOS 后，先创建项目，再使用以下接口：

- `GET /api/drawings/engines`：查看本机引擎能力。
- `POST /api/projects/{project_id}/drawings/upload`：multipart 上传 DWG/DXF；可传 `revision` 和 `source_unit`。
- `GET /api/projects/{project_id}/drawings`：查看项目图纸。
- `POST /api/projects/{project_id}/drawings/{drawing_id}/analyze`：执行首期长度、闭合面积或块数分析。
- `GET /api/projects/{project_id}/drawings/{drawing_id}/runs`：查看算量历史和证据。
- `GET /api/projects/{project_id}/drawings/{drawing_id}/runs/{run_id}/export.xlsx`：导出候选工程量、实体证据、映射状态和运行信息工作簿。
- `GET /api/projects/{project_id}/drawings/{drawing_id}/runs/{run_id}/entities/{handle}`：按实体 Handle 回查几何坐标、原始测量证据和图纸哈希。

分析 JSON 示例：

```json
{
  "task_description": "统计管道长度",
  "measurement_kind": "linear",
  "layer_patterns": ["M-PIPE*", "*给水管*"],
  "source_unit_override": "mm"
}
```

如未提供单位覆盖值，AuditOS 读取图纸 `$INSUNITS`。单位未知时不进行公制换算。

项目自定义映射 JSON 示例：

```json
{
  "rule_id": "project.water.pipe",
  "discipline": "plumbing",
  "item_name": "项目自定义给水管",
  "measurement_kind": "length",
  "unit": "m",
  "layer_patterns": ["*给水管*", "P-CW*"],
  "entity_types": ["LINE", "ARC", "LWPOLYLINE", "POLYLINE"],
  "notes": "按合同和项目计量规则确认立管、管件及预留长度。"
}
```

项目映射优先于通用模板，但依然标记为待确认；修改同一 `rule_id` 会创建新版本，不会把它变成正式定额规则。建议器还会将规则预期实体类型与图纸实际实体类型对照；例如文字层命中管线规则时会标记实体类型不匹配，不会静默采用。

分析结果中的 `mapped_takeoff_candidates` 会按映射规则聚合长度/面积/块数，并保留实体 Handle。存在歧义、实体类型不匹配或单位未知时，不生成数值；可生成数值的结果也只标记为 `candidate_for_human_review`，不等于已确认清单量。

## 6. 安全与计量边界

- 原始图纸保存在 `AUDITOS_DATA_DIR/drawings/{project_id}/`，数据库保存哈希、版本和路径；转换/分析产物写入独立 run 目录。
- 只处理用户明确上传的本地图纸；外部模型调用默认关闭。
- 原始图纸不被覆盖；转换结果不自动覆盖原图。
- 不把几何长度直接等同于安装工程结算量；电缆垂直段、桥架弯头、管件、预留余量、损耗及施工规则需后续专业规则包。
- 不将 HATCH bbox、AI 推断、块统计或未经核验的标注直接转成审减结论。

## 当前状态

原生 DXF 几何内核、上传/分析/历史 API 和适配器代码已进入开发分支。上游引擎是否可用取决于用户本机安装；DWG 转换必须通过 ODA/LibreDWG 预检。所有正式工程量仍须由专业人员复核。
