# 命令导航

参数以当前安装版本为准。`aak --help` 查看顶层命令，`aak 命令 --help` 查看完整参数；有子命令时继续细分，例如 `aak trial start --help`。命令不提供交互式问答，示例中的路径、技能名、ID 和维护者身份都需替换。

## 前提与目录

| 操作 | 前提 |
| --- | --- |
| `--help`、`--version`、`guide` | 已安装 aak，无需 Home Repo |
| `init` | 目标不存在或为空；默认需要 Git，`--no-git` 仅生成文件 |
| `status`、`catalog` | 有效 Home Repo，无需本机 Agent 配置 |
| `audit --library-only` | 有效 Home Repo，无需本机 Agent 配置；历史正式版本需要对应 Git 提交 |
| 客户端操作 | 本机配置和已启用目标；`try`、`share` 首次使用可自动生成配置，但不会重新启用显式禁用的目标 |
| 导入、采集、入库、实际分发和提交等扫描操作 | 可执行的 Gitleaks；预览和库内静态审计不要求扫描器 |
| `workspace`、`refresh`、`sync`、版本试用与采用 | 相应的 Git 历史和分支状态；创建提交前需配置 Git 作者 |

目录依次由 `--root`、`AGENT_ASSET_KIT_ROOT`、当前所属的已初始化库、默认 `~/agent-home` 选择。`init` 创建新目录，不采用当前所属库；它接受目录参数或显式根目录。详见[建库与目录选择](workflows.md#建库与目录选择)。

`try`、`save`、`share` 只在主库执行；批量审阅可使用独立工作区。`--agent` 记录维护者身份，不负责登录客户端，也不会选择本机实例；本机实例用支持该选项的命令的 `--instance` 选择。

## 建库、本机配置与查看

| 命令 | 作用与写入范围 |
| --- | --- |
| `init [DIRECTORY] --agents codex` | 建空白库、默认初始化 Git；可同时写入本机配置，不改客户端文件 |
| `configure --agents codex,claude` | 为已有库首次生成 `.local/config.json`；不覆盖已有配置 |
| `agent list` | 查看适配器、本机实例和启用状态 |
| `agent add --kind codex ... --reviewed` | 新增或更新本机实例、路径与探针；不写原生技能或指令 |
| `agent enable/disable --name INSTANCE --reviewed` | 修改本机启用状态，不删除客户端副本 |
| `agent bundled --instance INSTANCE` | 查看所选 Hermes 实例的内置技能目录名 |
| `attach` / `attach --apply` | 预览 / 备份并写入客户端全局指令中的库入口 |
| `status` | 查看实际库路径、配置、Git 状态和待处理入库收据 |
| `catalog` | 查看已登记的包清单；技能 ID 来自 `packages` 的键 |

配置和多个实例的示例见[客户端适配](adapters.md)。

## 单技能操作

| 命令 | 作用与写入范围 |
| --- | --- |
| `try SOURCE --to codex` | 直接向选定客户端写入独立试用副本，并保存本机收据；不自动入库 |
| `save NAME [--from codex] [--dry-run]` | 将 `try` / `share` 留有收据的副本保存回主库；预览只列变化，不入库 |
| `share NAME --to claude` | 将库内有效版本直接复制到一个客户端，并登记目标绑定 |

这三条命令不会自动创建 Git 提交。`try` 和 `share` 本身就是写入操作，不需要另加 `--apply`；应先审阅来源和目标。已有原生技能未留有这些命令的收据时，使用 `collect` 的候选审阅流程。完整目录有冲突、目标缺失或被其他试用占用时会停止，详见[故障处理](troubleshooting.md)。

## 候选、登记与版本

| 命令 | 作用与写入范围 |
| --- | --- |
| `collect` | 采集原生新建或修改包到本机候选，不改原生副本、不自动入库 |
| `discover QUERY` | 联网查询 skills.sh，返回来源线索，不安装技能 |
| `inspect SOURCE` / `fetch SOURCE` | 固定上游提交并检查包；`fetch` 额外保存本机候选 |
| `evaluate CANDIDATE_ID` | 查看候选审阅清单；带决策、理由、用途和审阅参数时记录决定 |
| `accept CANDIDATE_ID --agent codex --reviewed` | 将完整候选及登记通过可恢复事务入库 |
| `accept --recover ACCEPTANCE_ID --agent codex --reviewed` | 在原工作区核对快照后恢复到入库前状态，不直接重做入库 |
| `register --path skills/custom/NAME --agent codex --reviewed` | 登记已经放入库内的完整新包；不替代第三方来源与许可审阅 |
| `revision --skill-id SKILL_ID --agent codex --reviewed` | 对库内已审编辑更新哈希；可显式登记可执行位 |
| `enable --skill-id SKILL_ID --target claude --agent codex --reviewed` | 登记共享目标绑定，不立即复制文件；与 `agent enable` 的本机开关不同 |
| `decision ... --reviewed` | 记录拒绝、停用或重新允许；不自动删除或恢复原生副本 |
| `merge --candidate CANDIDATE_ID` / `merge --finalize MERGE_ID --reviewed` | 保留共同基线和两边包，手动解决后生成新的候选 |
| `trial start/finish/rollback/recover/status` | 对已提交的版本进行指定目标试用，保存证据或恢复收据 |
| `promote --skill-id SKILL_ID ... --reviewed` | 将已审提交设为正式版本，不自动分发；默认要求同一内容的通过证据 |

`try` 是外部或本地技能的快捷试用，`trial start` 是已入库、已提交版本的有限试用；二者的收据和后续命令不同。`--trial` 入库或修订的版本不会自动成为全体目标的正式版本。操作顺序见[进阶工作流](workflows.md)。

## Git 与分发

| 命令 | 作用与写入范围 |
| --- | --- |
| `workspace --agent codex` | 从主库 `main` 创建 `assets/` 分支和独立工作区；输出路径供后续选择 |
| `refresh` | 要求主库干净，拉取并快进合并配置的 GitHub 私有库；无远端时返回未刷新 |
| `sync --agent codex --message "MESSAGE" --local-only` | 提交当前库或工作区的允许路径；工作区内容普通合并回主库，不访问远端 |
| `sync --agent codex --message "MESSAGE"` | 检查发布授权和私有远端，提交、合并并推送 `main` |
| `sync ... --branch-only` | 仅推送已授权的隔离 `assets/` 或 `codex/` 分支，不合并 `main`；不能与 `--local-only` 混用 |
| `deploy` / `deploy --apply --plan PLAN_ID` | 生成冻结计划 / 核对同一计划后批量分发；默认按全部有效技能和本机启用绑定生成计划 |
| `recheck DEPLOYMENT_ID --reviewed` | 核对中断部署的已知前后状态，解除已核对收据；不重放写入 |

`sync` 会暂存当前工作区全部允许发布的改动，并强制纳入技能包的必需文件，不是单文件提交命令。已有暂存内容会使它停止，先审阅 `git diff`、`git status` 和暂存区。普通合并保留工作分支和冲突现场。

`deploy` 与 `trial start` 的来源是主库。实际批量部署要求主库 `main` 干净且已提交；预览与应用的技能、目标、实例和选项应一致。单技能分享可在首次提交前使用 `share`，不需要先批量部署。

## 检查、索引、模板与调度

| 命令 | 作用与写入范围 |
| --- | --- |
| `audit --library-only` | 检查库内包、引用、技能依赖关系、MCP 声明、跟踪路径和正式版本；不检查本机运行环境 |
| `audit` / `doctor` | 检查库、本机副本及已声明的运行依赖；普通 `doctor` 不启动原生发现 |
| `doctor --native --target codex` | 在前述检查外启动所选客户端的技能目录探针，不创建模型回合 |
| `index --agent codex --reviewed` | 根据登记重建 `skills/README.md`，覆盖该生成文件 |
| `resolve --skill NAME --target codex` | 按 ID、名称或登记别名定位库内包与本机副本 |
| `note --agent codex --scope global --title TITLE --source SOURCE --file FILE` | 保存经审阅的全局共享笔记；`--inbox` 保存待核实内容，不接受项目范围笔记 |
| `init-project PROJECT` / `apply-project PLAN_ID --reviewed` | 预览 / 应用静态项目约定，保留已有内容 |
| `scheduler [--interval 60] [--apply]` | 预览 / 安装 macOS 定时采集任务，不接受候选或推送；间隔单位为秒 |
| `tick` | 显式执行采集、刷新、生成计划并应用部署；有候选或冲突时停止 |

本机审计可运行技能声明的 Python 解释器读取安装包元数据；原生发现和自定义探针也会启动本机程序。只需库内检查或 CI 时使用 `audit --library-only`。`tick` 是会实际部署的组合命令，系统调度器默认运行的是 `collect`，不是 `tick`。

## 输出与退出码

正常操作通常输出 JSON；`guide`、版本及帮助输出正文。退出码 `2` 表示参数错误、停止条件、`BLOCKED` / `UNVERIFIED` 或非空阻塞项。退出码 `0` 仍需结合结果判断，例如 `audit` 的 `PENDING` 表示尚待安装或更新，`inspect` 的 `SELECTION_REQUIRED` 表示需要选定技能，不表示已部署。

报告中未检查的范围不会因命令成功而自动通过。常见状态和下一步见[故障处理](troubleshooting.md)。
