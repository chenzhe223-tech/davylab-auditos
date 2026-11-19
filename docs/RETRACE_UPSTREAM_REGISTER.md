# 图像重绘模块（cad-image-retrace）第三方/上游边界登记

## pengxiaoan/autocad-dwg-redraw-skill

| 项 | 记录 |
|---|---|
| 核验日期 | 2026-11-13（Codex 轮）、2026-11-19（本轮） |
| 核验方式 | GitHub API `GET /repos/pengxiaoan/autocad-dwg-redraw-skill` |
| license 字段 | `null`（两次核验一致；仓库无 LICENSE 文件） |
| pushed_at | 2026-07-11（本轮核验时无新推送） |
| 结论 | **无授权，不复制、不移植、不打包**任何上游源码或实现细节 |
| 采取路径 | DAVYLAB 独立实现（clean-room）：`backend/auditos/retrace/` |
| 上游脚本留存 | 仅存在于评测工作区 `work/upstream-readonly/`（只读对比用），不进入本仓库 |

## 本模块依赖（全部为 PyPI 标准许可）

| 依赖 | 版本 | 许可 | 用途 |
|---|---|---|---|
| opencv-python-headless | 4.11.0.86（固定） | Apache-2.0 | 形态学结构线提取 |
| ezdxf | 1.4.5（固定） | MIT | DXF 构建/审计/渲染 |
| numpy | <3.0 | BSD-3 | 数组运算 |
| matplotlib | 最新稳定 | PSF/BSD | DXF 预览渲染 |
| ODA File Converter | 27.1.0（本机外部安装） | ODA 免费 but 非开源 | DXF↔DWG 编码（外部进程调用，不分发） |

锁定策略：本轮在隔离 venv 固定版本；接入 AuditOS 后端时在
`pyproject.toml` 以范围约束声明并在锁定发布前记录哈希。
