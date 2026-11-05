# 图纸识读与算量引擎架构

## 1. 目标

在 AuditOS 中新增 Drawing Intelligence & Quantity Takeoff 子系统。用户提交 DWG/DXF 图纸并描述算量任务后，系统根据格式、任务意图、文件特征和工具能力，选择一个或多个只读解析引擎，形成可追溯的测量证据，再由 AuditOS 自有确定性计算层汇总工程量。

本能力不是把三个 Skill 简单并列，也不把第三方输出直接视为结算工程量。

## 2. 第三方引擎职责

| Adapter | 主要职责 | 适配任务 | 约束 |
|---|---|---|---|
| `dwg-reader-skill` | DWG→DXF（ODA）、图层/文字/尺寸/块/构件解析、基础几何统计、CSV/XLSX | DWG 入口、图层与标注提取、块数量、长度/尺寸候选 | DWG 转换依赖本机 ODA；天正代理对象可能丢失信息；解析摘要不是工程量依据 |
| `cad-ai-agent` | DXF 意图分类、分析/数量提取/区域识别/RFI 工作流、几何查询 | 自然语言任务规划、DXF 分析、房间/闭合区域初筛、交叉检查 | 作为独立进程/依赖隔离；LLM Planner 不负责算数；输出须结构校验 |
| `dxf-civil` | 大型 DXF 渐进式摘要、图层查询、渲染、空间查询、初步长度/面积/块数 | 土建相关图层、空间定位、图面可视复核、辅助 takeoff | 上游明确标注 preliminary；HATCH 面积可能采用 bounding-box 近似，默认禁止用于正式计量 |

来源仓库：
- https://github.com/kasc0206/dwg-reader-skill
- https://github.com/jeremylongshore/cad-ai-agent
- https://github.com/pedroschz/dxf-civil

## 3. 许可证与集成策略

核验到的仓库许可证：dwg-reader-skill 为 MIT；cad-ai-agent 为 Apache-2.0；dxf-civil 为 MIT。AuditOS 本身采用 Apache-2.0。引用、安装和分发第三方代码时必须保留原许可和归属；尤其 MIT 组件需保留版权及许可声明，Apache-2.0 组件需随附许可证并保留 NOTICE（如上游提供）。

第一阶段采取**外部安装、可选依赖、适配器调用**，不直接复制三个项目源码或字体资产到本仓库。第三方技能的完整安装、运行依赖与上游许可证变更需在实际锁定版本时复核。

## 4. 任务路由

1. 文件预检：扩展名、文件大小、哈希、版本、DWG/DXF、文件可读性、图纸单位/范围、图层和实体数量。
2. 任务理解：识别数量类型（线性、面积、个数、设备/构件、文字/标注统计、区域/系统汇总）。
3. 能力路由：
   - DWG：优先 DWG Reader 执行 ODA 转换及基础结构解析。
   - DXF 图层/尺寸/块统计：DWG Reader。
   - DXF 大图、需要视觉验证、空间筛选：DXF Civil。
   - 复杂自然语言分析、区域识别、独立交叉检查：CAD AI Agent。
   - 多引擎任务：先选一个主引擎，其余做独立交叉核对；不得平均结果或静默覆盖差异。
4. 测量适用性：判断实体类型是否能支持目标计量规则；区分 exact geometry、dimension annotation、block count、approximate bbox、AI inference。
5. 计算与输出：AuditOS 自有量算层重算；上游数量只作候选或交叉验证。
6. 人工复核：图纸版本、设计说明、系统边界、比例/单位、扣减规则、隐蔽部分和设计变更必须确认。

## 5. 证据模型

每个 MeasurementEvidence 至少包含：
- project_id、drawing_id、drawing_revision、drawing_sha256
- engine_id、engine_version、adapter_version、run_id
- source_sheet/layout、source_layer、entity_handle、entity_type、block_name
- raw_geometry_ref（实体句柄/几何快照定位）、source_text、dimension_text
- source_unit、normalized_unit、coordinate_scale、measurement_method
- raw_value、normalized_value、formula、rounding_policy
- confidence_class（exact_geometry / annotation / block_count / approximate / inferred）
- warnings、unresolved_assumptions、created_at

每个 TakeoffItem 包含专业、系统、清单项、特征描述、单位、数量、来源证据集合、计量规则包版本、计算公式、审核状态和人工调整记录。

## 6. 计量可信等级

- A / exact_geometry：基于可识别的真实几何和明确单位计算，仍需专业复核计量边界。
- B / annotation：引用图纸尺寸标注，必须核验标注与几何/版本一致。
- C / block_count：按块有效插入统计，必须核对块定义、动态块、缩放和阵列语义。
- D / approximate：近似区域/包围盒/栅格推断，只能作为线索，不能直接汇总结算量。
- E / inferred：AI 推断或文字语义推断，必须补充证据后才能采用。

## 7. 首期支持范围

- DWG 先转换为 DXF；DXF 直接解析。
- 结构化图层、实体、文字、尺寸、块和图面预览。
- 直线/多段线/圆/弧基础长度与数量提取。
- 闭合轮廓面积候选（只接受明确闭合且算法支持的几何；排除未处理的岛洞、边界交叉和复杂 Hatch）。
- 按图层、块、系统和图纸分组汇总候选工程量。
- 生成测量证据表、异常/缺失资料清单和人工复核台账。
- 与结算清单按编码/名称/单位/位置进行候选关联，不自动认定同项或重复。

## 8. 明确不承诺

- 不承诺所有 DWG、天正 TCH、Civil 3D Proxy、Xref 和动态块均可完整解析。
- 不把图面显示比例等同模型空间真实单位。
- 不从截图/扫描图自动获得可直接结算的可靠工程量。
- 不把 HATCH 包围盒面积、实体数或文字尺寸直接当作实物工程量。
- 不由 LLM 输出最终计算数字或审减结论。

## 9. QA 重点

- 单位转换和坐标尺度
- 模型空间与布局空间重复
- 块的 insert scale/rotation/mirror
- 重复实体、重复块和 Xref 边界
- 闭合多段线、bulge 圆弧和自交
- Hatch 岛洞与边界
- 尺寸标注与几何不一致
- DWG 转换警告、天正代理对象、乱码
- 结果差异与人工复核意见
