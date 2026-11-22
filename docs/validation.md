# 验证范围

## 验证工具本身

先按[贡献指南](../CONTRIBUTING.md)将当前源码安装到隔离环境，确认 `aak` 来自该环境，再在工具仓库运行：

```sh
aak --help
python tests/e2e/ci.py --scanner /absolute/path/to/gitleaks
git diff --check
```

若省略 `--scanner`，测试会下载并校验固定版本的 Gitleaks。测试使用临时 Git 仓库、合成用户目录和模拟客户端，不调用模型或业务服务。外部技能场景使用本机合成的 Git 来源，报告写入忽略的 `artifacts/e2e/`；失败报告可能包含临时路径，公开前需审阅。

[GitHub Actions](https://github.com/sw897/agent-asset-kit/actions/workflows/verify.yml) 配置为在 Ubuntu、macOS 和 Windows 使用 Python 3.11 运行 CLI。下面列的是测试范围，不代表某次提交的 CI 已通过；具体结果需核对该提交的工作流运行。

| 套件 | 主要观察 | 平台 |
| --- | --- | --- |
| `bootstrap.py` | 安装入口、打包指南、空白建库、接入、快捷试用 / 保存 / 分享及平台 shell 提示 | 全部 |
| `home_repo.py` | 默认目录与安装目录分离、一次建库配置、目录优先级、从项目操作及既有内容保护 | 全部；目录链接项限 POSIX |
| `library_audit.py` | 未配置库的公开审计、已审包、引用、依赖、正式版本、跟踪路径与资产字节保护 | 全部 |
| `project_templates.py` | 静态共性模板、追加预览、已有约定和变更目标保护 | 全部 |
| `boundaries.py` | 库标记、路径、目录边界与链接重定向保护 | 全部；部分链接项限 POSIX |
| `transactions.py` | 入库和分发恢复、候选发布中断、扫描拒绝及同步前置检查 | 全部；操作系统文件大小限额项限 POSIX |
| `workflows.py` | 外部来源、工作区、双端变化、有限试用、正式采用、恢复和跨库同步 | 全部 |
| `skill_dependencies.py` | MCP 静态声明、索引、回流和跨库保留；不调用连接管理程序 | 全部 |
| `run.py` | 完整副本、二进制 / CRLF、执行意图、原生改动、部署及共享内容边界 | 非 Windows |
| `skill_catalog.py` | 分类、技能依赖、索引、别名、本机运行依赖和合成原生目录协议 | 非 Windows |

`transactions.py` 用 Python 审计钩子注入元数据写入错误与进程退出，检查入库与分发恢复、候选的原子发布和未知编辑保护。POSIX 还使用文件大小限额检查准备失败。这些是受控故障注入，不等于真实磁盘耗尽或断电验收。

`skill_dependencies.py` 通过可执行陷阱验证资产流程不调用连接管理程序，合成的旧本机收据与原生 MCP 配置保持原字节。它不连接 MCP 服务，也不核验账号或工具可用性。

## 验证个人 Home Repo

个人库通过公开命令消费 AAK，不复制本仓库的通用测试或导入内部 Python 模块：

```sh
aak audit --library-only --output artifacts/library-audit.json
```

该模式检查 Git 跟踪路径可移植性、已审包的字节与逻辑模式、技能引用、技能依赖关系、运行依赖和 MCP 声明的格式，以及不可变正式版本。它不要求 `.local/config.json` 或客户端安装，不读取本机基线和试用状态，不验证命令、文件、Python 环境或服务是否可用，也不替代 Gitleaks 内容扫描。包含正式版本指针的库需要相应的完整 Git 历史。

Home Repo 可使用以下 GitHub Actions 示例。保存为个人库自己的 `.github/workflows/audit.yml`；例中 AAK 跟随 `main`，需要可复现版本时换成已验证的完整提交 SHA：

```yaml
name: Audit personal assets
on: [push, pull_request, workflow_dispatch]
permissions:
  contents: read
jobs:
  audit:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1
        with:
          fetch-depth: 0
          persist-credentials: false
      - uses: actions/setup-python@6d4fd22b20465ccc0f7354476588e8df7c9ca0e0
        with:
          python-version: '3.11'
      - name: Install AAK
        run: python -m pip install "git+https://github.com/sw897/agent-asset-kit.git@main"
      - name: Audit library
        run: aak audit --library-only --output artifacts/library-audit.json
```

`fetch-depth: 0` 获取完整历史，参见 [checkout 的历史获取说明](https://github.com/actions/checkout#fetch-all-history-for-all-tags-and-branches)。示例不安装客户端，不提供个人服务凭据，不执行个人技能，也不推送更改。任务日志和报告属于个人库的验证资料，按该库的可见范围保管。AAK 自身的兼容性测试仍由工具仓库执行。

## 区分验证层次

| 层次 | 可用证据 | 不代表 |
| --- | --- | --- |
| 库内静态检查 | `audit --library-only` | 本机环境或敏感信息已全部检查 |
| 本机文件与依赖 | `audit` / `doctor` 的包、副本、路径和安装元数据结果 | 客户端已经发现、模型已采用 |
| 合成原生协议 | 本仓库的模拟目录响应与断言 | 真实客户端或所有版本兼容 |
| 真实客户端发现 | 对应设备运行 `doctor --native --target codex`，记录版本与实例 | 模型执行了该技能 |
| 实际任务 | 同一内容哈希的操作记录与业务证据 | 未测试目标、账号或环境也已通过 |

普通本机审计可能运行已声明的 Python 解释器，原生发现会启动客户端或 runtime；先确认程序可信。内置目录探针不发送用户问题或创建模型回合，探针失败保留 `UNVERIFIED`，不以文件相同替代原生发现。

每次交付说明环境、版本、实际命令、退出状态、断言和报告路径；未执行的客户端、平台或业务检查单独列出。`PENDING`、`UNVERIFIED` 与失败的区别见[命令输出说明](commands.md#输出与退出码)。
