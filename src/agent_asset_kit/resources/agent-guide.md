# 用 aak 创建和维护 Home Repo

aak 是管理个人 Home Repo 的开源工具。Home Repo 保存个人背景、偏好、技能、服务环境、约定和模板，也可以作为个人服务与跨项目任务的共同入口。

下面从创建一个新的 Home Repo 开始。

## 创建 Home Repo

准备 Python 3.11+、Git、uv 和 Gitleaks，然后安装 aak 并创建个人库：

```sh
uv tool install --reinstall "git+https://github.com/sw897/agent-asset-kit.git@main"
aak init agent-home --git
cd agent-home
```

`agent-home` 是示例目录名，可以更换。`init` 会生成空白的资料框架并建立本地 Git 仓库，所需的标记文件由工具自动创建。

工具代码和个人库分别维护。以后升级 aak，重新执行安装命令；个人资料继续保存在自己的 Home Repo 中。

## 接入 Agent

在 Home Repo 内配置本机使用的 Agent 和 Gitleaks 路径。下面以 Codex、Claude Code 为例，`--agents` 只填写实际使用的客户端，用逗号分隔：

```sh
aak configure --agents codex,claude --gitleaks "扫描器绝对路径"
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

单个技能的试用、保存和分享，在 Home Repo 主目录执行：

- `aak try 来源 --to codex`：给指定 Agent 试用。来源可以是含 `SKILL.md` 的目录、GitHub 或 skills.sh 链接；有多个技能时用 `--skill 名称` 选择。
- `aak save 技能名`：把实际使用的副本及其中的修改收进库。多个来源时用 `--from codex` 指定，`--dry-run` 可先看变化。
- `aak share 技能名 --to claude`：把库内版本复制给另一个已接入的 Agent。之后在 Claude 改好，可用 `aak save 技能名 --from claude` 收回。

各 Agent 使用完整独立副本。用户只要求试用时，只执行试用；需要保存或分享时再做对应操作。做完项目任务后，把验证过的可复用方法整理进相关技能。

## 维护 Home Repo

单个技能用 `save` 保存后，用 `aak sync --agent codex --message "Save skill" --local-only` 提交到 Git。主目录有尚未提交的改动时，先审阅并提交，再刷新或创建工作区。

批量回流或直接编辑库内资产时，先在主目录采集原生修改、拉取更新，再建立独立工作区：

```sh
aak collect
aak refresh
aak workspace --agent codex
```

进入返回的工作区。阅读候选的完整包、来源许可、依赖和验证结果后，用 `aak accept 候选ID --agent codex --reviewed` 入库。直接修改已入库技能后，用 `aak revision --skill-id ID --agent codex --reviewed` 更新登记。

审阅 diff 后提交并合并回主库：

```sh
aak sync --agent codex --message "Update home repo" --local-only
```

需要批量更新客户端副本时，回到主目录预览并应用分发计划：

```sh
aak deploy
aak deploy --apply --plan 计划ID
```

检查计划中的技能和目标；只分发选定技能时，两条命令都加相同的 `--skill-id ID`。遇到双端修改或缺失副本时保留现场，不按时间戳覆盖，不传播删除。

用 `aak audit` 检查文件、登记和依赖，用 `aak doctor --native --target codex` 检查客户端是否发现技能。模型采用和实际任务效果另行验证，报告说明验证到哪一步。MCP 连接与认证由客户端或外部工具维护，技能分发不自动配置它们。

跨设备同步、上游技能引入、冲突合并、有限试用和中断恢复见[进阶工作流](https://github.com/sw897/agent-asset-kit/blob/main/docs/workflows.md)。具体参数用 `aak 子命令 --help` 查询。
