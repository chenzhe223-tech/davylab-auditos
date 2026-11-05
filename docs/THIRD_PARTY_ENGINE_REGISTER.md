# 第三方 CAD 引擎登记

| 项目 | 仓库 | 核验到的许可证 | 本产品采用方式 |
|---|---|---|---|
| DWG Reader Skill | https://github.com/kasc0206/dwg-reader-skill | MIT | 可选安装/外部调用；保留 MIT 声明 |
| CAD AI Agent | https://github.com/jeremylongshore/cad-ai-agent | Apache-2.0 | 可选安装/隔离进程调用；保留许可证及上游 NOTICE（如有） |
| DXF Civil | https://github.com/pedroschz/dxf-civil | MIT | 可选安装/外部调用；保留 MIT 声明 |

## 已确认能力与局限

### DWG Reader Skill
- DWG 通过 ODA File Converter 转 DXF；DXF 可直接处理。
- 解析图层、实体、文字、块属性、尺寸标注及构件识别。
- 提供几何统计及 CSV/XLSX 算量输出。
- 其 README 明确指出天正代理实体转换后可能导致天正文字/轴号/尺寸信息丢失，需回原软件图形导出或炸开处理。
- 不把上游转换后“实体/尺寸可读”误认为计量事实已经完整。

### CAD AI Agent
- 支持 DXF 任务意图分类、量算、闭合区域/房间识别、RFI 和摘要等工作流。
- DWG 需要 ODA File Converter，DXF 才是主要处理输入。
- 有 Mock LLM 运行模式；Mock 不用于真实生产判断。
- 官方设计原则强调模型规划结构化操作而不直接操作原始 DXF。AuditOS 将遵循同类隔离原则：LLM 只生成经过 schema 校验的任务计划。

### DXF Civil
- 提供 summary、render、layer、query、spatial、validate 和 takeoff 工具。
- 适合大型 DXF 渐进式查看和视觉复核。
- README 明确表示 takeoff 是 preliminary；Hatch 面积可能通过 bounding box 近似。
- 默认层命名规则以北美 NCS 为样例，必须替换为中国项目/企业自己的图层映射规则，不能直接套用。

## 锁定策略

当前不锁定上游 commit，避免误称已完成源码集成。运行时登记必须保存：
- upstream_repo
- upstream_commit/tag
- license_id
- adapter_version
- installed_version
- local_executable_path
- capability_probe
- known_limitations

发布包含第三方源代码的安装包前，必须执行许可证文件与 NOTICE 清单检查。
