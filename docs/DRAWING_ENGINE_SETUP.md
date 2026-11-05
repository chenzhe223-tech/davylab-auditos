# 图纸算量引擎本地安装配置

AuditOS 是本地优先程序。三个第三方引擎需由用户在本机单独安装；当前仓库不复制其源码、字体资产或不确定版本依赖。

## 1. DWG Reader Skill

上游：https://github.com/kasc0206/dwg-reader-skill

在本机克隆并安装上游支持的包，确保命令行可用：

```bash
git clone https://github.com/kasc0206/dwg-reader-skill
cd dwg-reader-skill
pip install -e ".[dxf,xlsx]"
dwg-reader doctor
dwg-reader --help
```

DWG 处理还需单独安装 ODA File Converter，并按上游说明配置 `DWG_READER_ODA_PATH`。AuditOS 仅在本机调用该可执行文件。天正/TCH 图纸需要回到原生软件图形导出或炸开后再复核。

## 2. DXF Civil

上游：https://github.com/pedroschz/dxf-civil

```bash
git clone https://github.com/pedroschz/dxf-civil
cd dxf-civil
pip install "ezdxf[draw]" matplotlib pyyaml
```

设置环境变量 `AUDITOS_DXF_CIVIL_DIR` 为本地仓库目录。执行 takeoff 时将按上游脚本输出保存候选结果。面积输出默认只作初步核查，尤其 HATCH bbox 不得直接用于结算量。

## 3. CAD AI Agent

上游：https://github.com/jeremylongshore/cad-ai-agent

该项目功能面较广，包含独立 web/desktop/LLM Provider 环境。建议先按上游 README 在单独虚拟环境完成安装和 mock-mode 测试。当前 AuditOS 只探测其包/CLI 是否存在，未启用任意自动 import、LLM Provider 或命令行参数猜测；待锁定上游版本和输出 schema 后再接入任务执行协议。

## 4. 安全和数据

- 只允许从 AuditOS 用户明确选择的文件调用引擎。
- 图纸转换和渲染输出写入项目工作目录，不覆盖原始图纸。
- 外部模型调用默认关闭；CAD AI Agent 必须使用 mock 或显式授权的本地/远程 Provider。
- 不要把包含客户名称、地址、设备表、内部图纸或人员信息的项目资料上传到公共 Issue。
- 第三方输出需保存上游版本、运行参数、stdout/stderr 摘要、文件哈希和结果文件哈希。

## 当前状态

这一版实现的是引擎发现、任务路由和 API 计划接口，不是三个引擎已经全部安装/运行。需要在用户本机安装后，执行 `GET /api/drawings/engines` 确认能力探测，再启用实际处理。
