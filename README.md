# DAVYLAB AuditOS

**智能工程结算审核 Agent（首期：安装工程）**

DAVYLAB AuditOS 是面向中国大陆工程建设市场的本地优先工程结算审核平台。首期聚焦安装工程，帮助造价工程师整理结算资料、提取合同条件、复核清单与工程量、检查综合单价和费用、关联变更签证，并形成可追溯审核底稿。

> **专业边界：** 本软件是造价专业人员的审核辅助工具，不替代依法承担责任的专业人员或机构作出最终结论。审核线索和建议必须经过人工复核。

## 核心原则

1. 本地优先：项目文件和审核记录默认保留在用户设备。
2. 确定性计算：工程量、单价、合价、税费和差异由可测试的规则引擎计算，不由大模型口算。
3. 证据先行：审核发现关联来源文件、页码/工作表/单元格/条款、计算过程和规则版本。
4. 规则适用性：记录规则来源、版本、地域、生效时间和适用条件，不混用地区与版本。
5. 人工在环：AI 只辅助提取、归类、解释和发现线索；最终确认由授权人员完成。
6. 可复核可重算：输入、规则、计算过程、人工处置和成果版本可追踪。

## 首期功能
- 项目管理与审核任务
- 结算资料登记及结构化解析
- 合同计价条件提取与人工确认
- XLSX 结算清单读取和数据校验
- 安装工程清单、工程量、综合单价和费用复核
- 重复申报、缺项、异常值和合同边界线索
- 审核台账、证据链与人工复核流程
- XLSX 审核明细和 DOCX 审核报告
- DWG/DXF 图纸登记、哈希追溯和修订号管理
- DXF 原生几何候选算量：直线、圆弧、圆、多段线和顶层块插入
- 安装专业图层映射模板建议（电气、给排水、暖通、消防）

## 技术基线
Python 3.11+、FastAPI、Pydantic、SQLite、Decimal、openpyxl；前端后续采用 React + TypeScript。AI Provider 默认关闭，按用户授权按需启用。

## 快速开始
```bash
cd backend
python -m venv .venv
# Windows PowerShell: .venv\\Scripts\\Activate.ps1
# macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
uvicorn auditos.main:app --reload
```
打开 http://127.0.0.1:8000/docs 。

## 图纸算量 API（开发分支）
- `GET /api/drawings/engines`：本机引擎能力探测。
- `POST /api/projects/{project_id}/drawings/upload`：上传 DWG/DXF，登记哈希、修订号和单位。
- `POST /api/projects/{project_id}/drawings/{drawing_id}/analyze`：执行首期几何分析。
- `GET /api/projects/{project_id}/drawings/{drawing_id}/runs`：读取分析历史。
- `GET /api/drawings/mapping-templates`：查看安装专业图层映射模板。
- `GET/POST /api/projects/{project_id}/drawings/mappings`：维护项目自定义映射，自动版本化。
- `GET /api/projects/{project_id}/drawings/mappings/{rule_id}/history`：查询映射历史版本。

图纸算量边界：原生内核当前只处理模型空间 XY 平面中已支持的几何；单位未知时不换算；HATCH、Xref、嵌套块、3D 实体及安装工程竖向段/管件/损耗规则尚未纳入正式计量。映射模板仅为候选建议，不能直接作为国家规范、地方定额或项目规则依据。

## 状态
Phase 0/1 后端基础能力已建立；Phase 2 图纸识读与算量正在开发，当前改动位于 Draft PR。自动化测试通过不等于已在用户本机安装三个上游引擎或完成真实工程图纸验收。仓库内规则和示例并非任何地区当前有效计价依据。实际项目必须先确认所在地、基准日期、合同约定和授权规则。

采用 Apache-2.0 License。禁止提交真实合同、结算书、施工图、个人信息、商业报价、密钥或未获授权的规范/定额数据库。
