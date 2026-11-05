# 图纸算量引擎本地安装配置

AuditOS 是本地优先程序。三个第三方引擎需由用户在本机单独安装；当前仓库不复制其源码、字体资产或不确定版本依赖。

## 1. DWG Reader Skill
上游：https://github.com/kasc0206/dwg-reader-skill

```bash
git clone https://github.com/kasc0206/dwg-reader-skill
cd dwg-reader-skill
pip install -e ".[dxf,xlsx]"
dwg-reader doctor
```
DWG 处理需单独安装 ODA File Converter，并配置 `DWG_READER_ODA_PATH`。天正/TCH 图纸须回原生软件图形导出或炸开后复核。

## 2. DXF Civil
上游：https://github.com/pedroschz/dxf-civil

```bash
git clone https://github.com/pedroschz/dxf-civil
cd dxf-civil
pip install "ezdxf[draw]" matplotlib pyyaml
```
设置 `AUDITOS_DXF_CIVIL_DIR` 为仓库目录。上游 takeoff 是 preliminary，HATCH bbox 近似不得直接用于结算计量。

## 3. CAD AI Agent
上游：https://github.com/jeremylongshore/cad-ai-agent

建议先独立虚拟环境安装并通过 mock-mode 测试。当前 AuditOS 只探测包/CLI，不执行猜测的命令参数、不自动启用 LLM Provider；须先锁定上游版本和输出 schema。

## 4. 安全
- 仅处理用户明确选择的本地图纸，转换输出不覆盖原图。
- 外部模型调用默认关闭。
- 记录上游版本、运行参数、文件哈希和结果文件哈希。
- 不要把真实工程图纸上传到公开 Issue。

## 当前状态
当前实现是引擎发现、任务路由和计划 API，不代表三个引擎已经安装/运行。安装后调用 `GET /api/drawings/engines` 检查。
