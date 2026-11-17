# AgentAssetKit (aak)

aak 是跨 Agent 的个人资产管理工具。它创建和维护个人 Home Repo，组织个人资料，并管理技能的引入、试用、保存、回流、分发和跨设备同步。

Home Repo 保存个人背景、偏好、共享记忆、已入库技能、服务环境和模板，作为各 Agent 的共同入口。各 Agent 使用完整独立的技能副本，修改经审阅后可通过 aak 保存回库，再按需分发。

aak 单独安装，个人资产保存在独立的 Home Repo 中，两者分别维护。项目开发仍遵守各项目自己的约定。

AgentAssetKit is a CLI for managing personal context and reusable skills across AI agents and devices. It creates and maintains a personal Home Repo, with workflows for trying skills, saving reviewed changes, sharing independent copies, and synchronizing across devices.

## 交给 Agent 开始

把这句话连同本项目的链接或本地路径交给 Agent：

> 请按 Agent 操作指南安装 aak，创建一个新的 Home Repo，只接入我指定的 Agent，验证后告诉我如何整理资料、试用和保存技能。

[Agent 操作指南](src/agent_asset_kit/resources/agent-guide.md) 随安装包提供。安装后运行 `aak guide`，即可读取当前版本的步骤。

## 创建 Home Repo

准备 Python 3.11+、Git、[uv](https://docs.astral.sh/uv/getting-started/installation/) 和 [Gitleaks](https://github.com/gitleaks/gitleaks#installing)，然后运行：

```sh
uv tool install --reinstall "git+https://github.com/sw897/agent-asset-kit.git@main"
aak init --agents codex
```

默认创建在用户目录下的 `~/agent-home`；Windows 为 `%USERPROFILE%\agent-home`。`init` 生成空白资料框架、建立 `main` 分支，并配置选定的客户端。使用多个客户端时写 `--agents codex,claude`，Gitleaks 从 `PATH` 自动查找。

Home Repo 与 aak 安装目录分开，升级或重装工具时个人资料继续保留。默认库创建后，日常命令可在任意项目目录运行，无需切换目录或重复填写路径。

升级时重新执行安装命令，用 `aak guide` 读取新版本指南。已建库中的资料、约定和模板由用户维护，不随工具升级自动覆盖。安装后找不到命令时，见[故障处理](docs/troubleshooting.md#安装与目录)。

需要其他位置时用 `aak init /path/to/agent-home --agents codex`；后续进入该目录，或用 `aak --root /path/to/agent-home 子命令`。只创建框架可运行 `aak init`，稍后用 `aak configure --agents codex` 配置客户端。初始化只接受不存在或空的目录。

## 接入 Agent

创建时已选好客户端，接着预览个人库入口：

```sh
aak attach
```

`attach` 预览将写入客户端全局指令的个人库入口。检查目标文件后应用：

```sh
aak attach --apply
```

新会话可以通过入口找到 Home Repo，当前会话需重新读取。

确认 Git 已配置作者后，创建首次本地提交：

```sh
aak sync --agent codex --message "Initialize home repo" --local-only
```

`--agent codex` 记录本次维护者身份，按实际使用的 Agent 替换。支持的客户端包括 Codex、Claude Code、Hermes、OpenCode、ZCode 和 DeepSeek Harness，各设备分别配置自己的目标和路径。

## 库里放什么

| 内容 | 位置 |
| --- | --- |
| 用户画像和稳定偏好 | `USER.md` |
| 共享核心记忆和查阅入口 | `MEMORY.md` |
| 有来源的画像、共享约定明细；同一范围内待核实的内容 | `memory/shared/`、`memory/inbox/` |
| 技能、服务环境、领域知识和操作经验 | `skills/` 下的技能包及其 `references/` |
| 客户端接入、工具说明、项目导航 | `agents/` |
| 可复用模板 | `templates/` |

长期使用的服务器、系统、资料目录和工作流属于个人服务环境，可以在相关技能中保留执行所需的具体信息。环境记录注明来源、日期和适用范围，执行前确认现场。

项目特有约定和完整项目记忆留在所属项目，知识库正文留在原知识库。凭据、认证、原生数据库和会话日志不入库；本机配置、候选、备份和运行环境各自留在本机。

## 日常使用

个人服务和跨项目任务可以从 Home Repo 开始，让 Agent 按 `AGENTS.md` 查阅偏好、共享记忆和相关技能。跨项目导航可从 `templates/project-index.md` 建立 `agents/projects.md`，本机克隆路径放在忽略的 `.local/project-paths.md`。

明确的项目任务也可以直接从项目开始。Agent 进入目标仓库后读取该项目的指南，在那里修改、验证并执行已获授权的 Git 操作。项目开发无需创建 aak 工作区，验证过的可复用方法可以整理进相关技能。

使用默认 Home Repo 时，下面的命令可以直接在当前项目中执行；这三条命令使用主库，不在 aak 的独立审阅工作区中执行：

- `aak try 来源 --to codex`：给指定 Agent 试用。来源可以是含 `SKILL.md` 的目录、GitHub 或 skills.sh 链接；有多个技能时用 `--skill 名称` 选择。
- `aak save 技能名`：把实际使用的副本和修改收进库。多个来源时用 `--from codex` 指定，`--dry-run` 可先看变化。
- `aak share 技能名 --to claude`：把保存的版本复制给另一个已接入的 Agent。之后的改进也可以通过 `save` 收回。

各 Agent 使用完整独立副本。只要求试用时，只执行试用；需要保存或分享时再做对应操作。遇到双端修改或缺失副本时保留现场，不自动覆盖或传播删除。

`try` 和 `share` 会直接写入选定客户端，`save` 会更新库内文件和登记，三者都不自动提交 Git。分享前用 `aak agent list` 核对目标是否启用；多个实例与自定义路径见[客户端适配](docs/adapters.md)。

审阅改动后，用 aak 创建本地提交：

```sh
aak sync --agent codex --message "Update home repo" --local-only
```

个人库可以只在本地使用。跨设备时，可将它放到自己的 GitHub 私有仓库，同步后在各设备分发所需技能。远端配置和推送授权见[GitHub 私有库同步](docs/workflows.md#可选-github-私有资产库)。

Home Repo 的 CI 可运行 `aak audit --library-only`，检查库内登记、包哈希、技能引用、技能依赖关系和 MCP 声明。该模式无需本机配置或 Agent 安装，也不验证服务可用性或替代敏感信息扫描；[验证说明](docs/validation.md#验证个人-home-repo)提供 CI 示例。工具的通用测试由本仓库维护，个人库只校验自己的资产。

## 更多用法

- [Agent 操作指南](src/agent_asset_kit/resources/agent-guide.md)：建库、接入、日常使用和维护；安装后也可运行 `aak guide`。
- [进阶工作流](docs/workflows.md)：候选审阅、独立工作区、冲突合并、批量分发、有限试用、恢复和依赖声明。
- [资产组织](docs/organization.md)：个人资料、技能、服务环境与本机状态分别放在哪里。
- [命令导航](docs/commands.md)：各命令的前提、写入范围、Git 行为和退出状态。
- [客户端适配](docs/adapters.md)：默认路径、本机实例、原生发现和验证边界。
- [故障处理](docs/troubleshooting.md)：目录选择、缺失依赖、冲突、中断与远端认证。

项目约定模板用 `aak init-project 项目路径` 生成预览，`--template` 可选 `software`、`research` 或 `documents`。审阅后用 `aak apply-project 计划ID --reviewed` 应用，保留目标项目原有约定；模板只放共性行为约束。

技能可以声明 MCP 能力依赖，aak 负责静态校验和展示。连接、认证和服务可用性由客户端或外部工具维护，分发技能不会自动配置 MCP。

## 开发与验证

在激活的 Python 3.11+ 虚拟环境中，进入工具源码目录运行：

```sh
python -m pip install .
aak --help
python tests/e2e/ci.py --scanner /absolute/path/to/gitleaks
git diff --check
```

测试使用合成用户目录和临时 Git 仓库，不修改真实 Agent 配置或调用业务服务。报告写入忽略的 `artifacts/e2e/`。文件复制、原生发现、模型采用和业务效果分别验证，范围见[验证说明](docs/validation.md)。

开发约定见 [AGENTS.md](AGENTS.md)，贡献方式见 [CONTRIBUTING.md](CONTRIBUTING.md)，安全边界见 [SECURITY.md](SECURITY.md)。许可为 [MIT](LICENSE)。
