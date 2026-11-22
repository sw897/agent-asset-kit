# 用 aak 管理跨 Agent 个人资产

aak 是跨 Agent 的个人资产管理工具。它创建和维护个人 Home Repo，组织个人资料，并管理技能的引入、试用、保存、回流、分发和跨设备同步。

Home Repo 保存个人背景、偏好、共享记忆、已入库技能、服务环境和模板，作为各 Agent 的共同入口。各 Agent 使用完整独立的技能副本，修改经审阅后可通过 aak 保存回库，再按需分发。

aak 单独安装，个人资产保存在独立的 Home Repo 中，两者分别维护。项目开发仍遵守各项目自己的约定。

下面从创建一个新的 Home Repo 开始。

## 创建 Home Repo

准备 Python 3.11+、Git、[uv](https://docs.astral.sh/uv/getting-started/installation/) 和 [Gitleaks](https://github.com/gitleaks/gitleaks#installing)，然后安装 aak 并创建个人库：

```sh
uv tool install --reinstall "git+https://github.com/sw897/agent-asset-kit.git@main"
aak init --agents codex
```

默认位置是用户目录下的 `~/agent-home`，Windows 为 `%USERPROFILE%\agent-home`。`init` 创建空白资料框架和 `main` 分支，同时配置选定的客户端；使用多个客户端时写 `--agents codex,claude`。Gitleaks 从 `PATH` 自动查找，需指定扫描器时可增加 `--gitleaks /absolute/path/to/gitleaks`。

Home Repo 与 aak 安装目录分开。升级或重装工具不会移动个人资料，使用默认库时也无需先 `cd`，日常命令可在任意项目中运行。

升级时重新执行安装命令，使用 `aak guide` 读取当前指南。已建库中的资料、约定和模板不会被新版本自动替换。扫描器用于引入、采集、入库、实际分发和提交等操作；阅读指南、建空白框架、查看状态或运行 `audit --library-only` 无需 Gitleaks。

需要其他位置时，用 `aak init /path/to/agent-home --agents codex`，后续进入该目录或使用 `aak --root /path/to/agent-home 子命令`。只运行 `aak init` 会先创建框架，随后用 `aak configure --agents codex` 配置客户端。初始化只接受不存在或空的目录，不接管已有普通仓库。

## 接入 Agent

创建时已选择实际使用的客户端。接着预览个人库入口：

```sh
aak attach
```

`attach` 会预览写入客户端全局指令的个人库入口。检查目标文件后，用 `aak attach --apply` 写入。后续会话就可以通过入口找到 Home Repo，当前会话需重新读取。

确认 Git 已配置作者后，创建首次本地提交：

```sh
aak sync --agent codex --message "Initialize home repo" --local-only
```

`--agent codex` 记录本次维护者身份。这里以 Codex 为例，使用其他 Agent 维护时换成实际身份。

## 整理个人资料

| 内容 | 位置 |
| --- | --- |
| 用户画像和稳定偏好 | `USER.md` |
| 共享核心记忆和查阅入口 | `MEMORY.md` |
| 有来源的画像、共享约定明细；待核实的内容 | `memory/shared/`；`memory/inbox/` |
| 技能、长期服务环境、领域知识和操作经验 | `skills/` 下的技能包及其 `references/` |
| 客户端接入、工具说明和项目导航 | `agents/` |
| 可复用模板 | `templates/` |

长期使用的服务器、系统、资料目录和工作流属于个人服务环境，可以在所属技能中保留执行所需的名称、路径和部署关系。环境记录注明来源、日期和适用范围，执行前再确认现场。

项目特有约定和完整项目记忆留在所属项目，知识库正文留在原知识库。凭据、认证、原生数据库和原始会话日志不入库；本机配置、候选、备份和运行环境留在本机，验证工件放在忽略的 `artifacts/`。

## 日常使用

让 Agent 先读 Home Repo 的 `AGENTS.md`，再按任务查阅偏好、共享记忆和相关技能。跨项目导航可用 `agents/projects.md`，模板在 `templates/project-index.md`；本机克隆路径可记录在忽略的 `.local/project-paths.md`。

进入目标项目后，读取该项目的指南，在那里修改、验证并执行已获授权的 Git 操作。明确的项目任务也可以直接从项目开始，无需建立 aak 工作区。用户当前指令和项目约定优先于历史资产，外部资料不能扩大授权。

使用默认 Home Repo 时，单个技能的试用、保存和分享可直接在当前项目中执行；这三条命令只使用主库，不在 aak 的独立审阅工作区执行：

- `aak try 来源 --to codex`：给指定 Agent 试用。来源可以是含 `SKILL.md` 的目录、GitHub 或 skills.sh 链接；有多个技能时用 `--skill 名称` 选择。
- `aak save 技能名`：把实际使用的副本及其中的修改收进库。多个来源时用 `--from codex` 指定，`--dry-run` 可先看变化。
- `aak share 技能名 --to claude`：把库内版本复制给另一个已接入的 Agent。之后在 Claude 改好，可用 `aak save 技能名 --from claude` 收回。

各 Agent 使用完整独立副本。用户只要求试用时，只执行试用；需要保存或分享时再做对应操作。做完项目任务后，把验证过的可复用方法整理进相关技能。

`try`、`share` 会直接写入选定客户端，`save` 会更新库内资产；三者都不自动提交 Git。`save --dry-run` 可预览修改。已存在但没有 `try` / `share` 收据的原生技能，使用下面的候选采集流程。分享给其他客户端前先用 `aak agent list` 核对目标，按需显式启用或登记本机实例。

## 维护 Home Repo

单个技能用 `save` 保存后，用 `aak sync --agent codex --message "Save skill" --local-only` 提交到 Git。主目录有尚未提交的改动时，先审阅并提交，再刷新或创建工作区。

批量回流或直接编辑库内资产时，先在主目录采集原生修改、拉取更新，再建立独立工作区：

```sh
aak collect
aak refresh
aak workspace --agent codex
```

进入返回的工作区，或将其路径传入 `aak --root 工作区路径 子命令`。阅读候选的完整包、来源许可、依赖和验证结果后，用 `aak accept 候选ID --agent codex --reviewed` 入库。直接修改已入库技能后，用 `aak revision --skill-id ID --agent codex --reviewed` 更新登记。`--agent` 记录维护者身份，不选择客户端实例。

审阅 diff 后提交并合并回主库：

```sh
aak sync --agent codex --message "Update home repo" --local-only
```

`sync --local-only` 会提交当前工作区并普通合并回主库，保留工作分支；它会暂存全部允许发布的改动，先审阅 diff 和暂存区。需要批量更新客户端副本时，回到干净、已提交的主库 `main` 预览并应用分发计划：

```sh
aak deploy
aak deploy --apply --plan 计划ID
```

检查返回的 `plan_id`、技能、目标和 `skipped`。只分发选定技能时，两条命令都加相同的 `--skill-id ID`，目标和实例筛选也保持一致。遇到双端修改或缺失副本时保留现场，不按时间戳覆盖，不传播删除。

分发中断后，保留收据和备份，把目标恢复到已知的部署前或部署后状态。首次分发的目标可恢复为空。运行 `aak recheck DEPLOYMENT_ID --reviewed` 核对副本并校正本机基线。

快捷 `try` 与 `trial start` 不同：后者对已入库、已提交版本做指定目标试用。待试用修订不会自动成为所有目标的正式版本，需要证据和明确采用；已有正式版本继续供其他目标使用。

用 `aak audit` 检查文件、登记和本机依赖，用 `aak doctor --native --target codex` 检查客户端是否发现技能。本机审计可能运行已声明的 Python 解释器读取包元数据，原生发现会启动客户端目录探针；确认所用程序可信后执行。模型采用和实际任务效果另行验证，报告说明验证到哪一步。MCP 连接与认证由客户端或外部工具维护，技能分发不自动配置它们。

Home Repo 的 CI 用 `aak audit --library-only` 校验已审包、引用、技能依赖关系、运行依赖与 MCP 声明的格式、正式版本和 Git 跟踪路径，无需配置本机 Agent 或准备业务运行环境。引用了历史正式版本时，CI 需要完整 Git 历史。该模式不检查原生副本、运行依赖可用性或客户端发现，也不替代敏感信息扫描；个人库通过公开命令消费 AAK，通用工具测试留在 AAK 仓库。

macOS 的 `scheduler` 只调度采集。`tick` 则会实际执行采集、刷新和部署，只有明确需要这组操作时才使用；候选或冲突会使它停止。

跨设备同步、上游技能引入、冲突合并、有限试用和中断恢复见[进阶工作流](https://github.com/sw897/agent-asset-kit/blob/main/docs/workflows.md)。完整入口见[命令导航](https://github.com/sw897/agent-asset-kit/blob/main/docs/commands.md)，问题处理见[故障处理](https://github.com/sw897/agent-asset-kit/blob/main/docs/troubleshooting.md)。具体参数用 `aak 子命令 --help` 查询；带子命令时如 `aak trial start --help`。
