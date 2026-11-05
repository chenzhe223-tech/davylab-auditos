# 技术架构

## 架构原则
- 模块化单体优先，避免过早拆分微服务。
- 领域逻辑不依赖 LLM、Web 框架或文件格式。
- 金额计算使用 Decimal；规则可单测、重放、版本化。
- 解析器输出结构化事实和来源定位，不直接产出最终审减结论。
- AI 通过受控工具访问用户授权的项目上下文。
- 模型供应商、规则包和提示词可替换、可版本化。

## 分层
```text
React UI (future)
  -> FastAPI / API schemas
  -> Application services / workflow orchestration
  -> Ingestion | Deterministic Audit Engine | Agent adapters
  -> Domain models + repositories
  -> SQLite + local evidence files
```

## 模块
- domain：Project、Document、ContractTerm、SettlementItem、AuditFinding、EvidenceRef、ReviewEvent
- ingestion：文件登记、哈希、解析适配器、字段映射、来源定位
- audit_engine：数量/单价/合价校验、重复线索、规则执行
- agents：提取、解释、任务编排；工具白名单和人工审批门
- storage：SQLite repository、迁移、本地文件管理
- reports：XLSX/DOCX 输出和一致性检查
- api：输入校验、错误模型、REST endpoints

## Agent 分工
- Audit Master：组织任务，不算数、不确认结论。
- Document Agent：识别文件和资料缺失线索。
- Contract Agent：提取条款候选并给出页码、原文供确认。
- Quantity Agent：调用确定性量算/校验服务；CAD 结果只作辅助。
- Pricing Agent：核查价格数据来源和日期，不伪造市场价。
- Variation Agent：关联变更、签证、合同及清单。
- Risk Agent：汇总规则触发线索，不把异常等同违规。
- Report Agent：汇总已确认数据，并显式标识待确认事项。

## 数据流
文件登记（文件哈希、项目 ID、版本）-> 解析（结构化数据及源位置）-> 人工确认字段与规则 -> 确定性执行 -> 审核发现 -> 人工复核事件 -> 指定版本成果导出。

## 本地安全
- 默认只绑定 127.0.0.1。
- 上传限制文件类型、大小，并安全规范化路径。
- 项目数据存放在用户数据目录，禁止进入仓库。
- 云端模型默认关闭，启用前展示供应商和数据范围。
- Key 只读环境变量/系统密钥存储，不写入日志。
- 开源示例仅使用合成数据。

## 扩展
地区规则作为独立规则包，记录来源、版本、地域和有效日期；CAD、OCR、定额数据库作为 adapter；多用户协作和 PostgreSQL 后续评估。
