# 故障处理

先在本次操作的目录运行 `aak status`，核对 `workspace`、`primary`、本机配置和未完成入库收据。使用自定义库或工作区时显式指定 `--root`，保留报错、候选、备份和本机收据；后续操作以当前内容为准。

## 安装与目录

| 现象 | 处理方式 |
| --- | --- |
| 安装后找不到 `aak` | 核对 `uv tool list` 和 PATH；按 [uv 指南](https://docs.astral.sh/uv/guides/tools/#installing-tools) 使用 `uv tool update-shell`，再打开新终端 |
| `no Home Repo found` | 用 `aak init --agents codex` 创建新库，或通过 `--root` 指向已初始化的库 |
| 默认位置已有普通目录 | 保留目录，选择新的空位置初始化；当前不支持将普通仓库直接接管为 AAK 库 |
| 命令用了意外的库 | 核对 `--root`、`AGENT_ASSET_KIT_ROOT`、当前所属库和默认位置的优先级；`init` 不自动使用当前所属库 |
| 升级后旧库里的指南或模板没变 | 用 `aak guide` 读取新工具指南；建库时复制的文件由用户维护，升级不会覆盖它们 |
| `local configuration already exists` | `configure` 不覆盖配置；用 `agent add/enable/disable` 调整实例，或明确审阅后编辑 `.local/config.json` |

`init` 不迁移旧目录。已有 `~/.agent-asset-kit` 有效库可从库内使用，或显式传入 `--root`。库标记和共享元数据不是可随意删除的缓存。

## 技能与客户端

| 现象 | 处理方式 |
| --- | --- |
| Gitleaks 缺失或不能执行 | 先核对 `gitleaks version`；若本机配置已登记旧绝对路径，更新 `gitleaks` 字段，或设为 `null` 以重新从 PATH 查找 |
| 来源包含多个技能 | 按返回的目录使用 `try --skill`，或对 `fetch` 使用 `--path`；本地目录应直接指向含 `SKILL.md` 的包，不加 `--skill` |
| 目标被禁用或有多个实例 | 用 `aak agent list` 核对；需要时显式启用，或在操作中增加 `--instance`。`attach` 应在实例配置确定后预览 |
| `save` 没有找到收据 | 使用 `try` / `share` 输出的安装目录名；未由这两条命令登记的原生技能走 `collect`、审阅、`accept` |
| `save` 有多个副本可选 | 增加 `--from codex`，同类型多实例时再增加 `--instance`；先用 `--dry-run` 核对修改 |
| `SKILL.md` 无法发现 | 检查首行 frontmatter、非空 `name` 和 `description`，再用对应客户端的 `doctor --native` 验证；AAK 的字段检查不是完整 YAML 解析器 |
| 文件一致但 `UNVERIFIED` / `NOT_VISIBLE` | 核对客户端版本、已启用实例、运行路径和目录探针；文件一致不能替代原生发现或实际任务验证 |

`audit` 的 `PENDING`、`INSTALL_PENDING`、`UPDATE_PENDING` 表示登记版本还未完全部署；`CONFLICT`、`MISSING` 需要处理真实副本差异。`NOT_RELEASED` 表示待试用或已停用版本，不应通过修改报告把它视为已采用。

## 版本、冲突与中断

库内包修改后哈希不一致：先审阅完整 diff，再用 `revision --skill-id ... --agent ... --reviewed` 更新登记。可执行意图变化用 `--executable` / `--not-executable` 明确指定，不能仅在 Windows 上依赖 `chmod`。

库与客户端都修改：先采集并保留两边，再按[合并流程](workflows.md#隔离编辑与双端变化)处理共同基线。找不到共同基线时人工核对完整包，不按时间戳选胜者。缺失副本不代表删除授权；仅在明确决定恢复时，在新的分发预览和应用中使用相同的 `--restore-missing`。

冻结计划过期：重新预览、检查变化，再用新计划应用。目标有本机运行环境时，确认需保留的内容后，在预览和应用中使用相同的 `--preserve-runtime`。不要复用旧计划绕过目标变化。

| 未完成状态 | 核对后使用的入口 |
| --- | --- |
| 入库收据 | 在 `status` 指出的原工作区执行 `accept --recover ACCEPTANCE_ID --agent codex --reviewed`，恢复后再决定是否重试 |
| 部署收据 | 保留原件与备份，手工恢复到收据记录的某个状态。原先没有副本的目标可以恢复为空。执行 `recheck DEPLOYMENT_ID --reviewed` 校正本机基线并关闭收据 |
| `incomplete candidate directory` 或 `existing candidate content differs` | 按报错中的 ID 核对旧候选的包和评估记录。需要重新生成时，将异常目录移到保留位置，再重试原来的 `collect`、`fetch` 或 `merge --finalize` |
| 版本试用收据 | 用 `trial status` 查看；对 preparing、rolling_back、interrupted 等状态使用 `trial recover TRIAL_ID --reviewed`，核对后再决定下一步 |

直接删除收据会丢失恢复依据。`passed` 试用仍占用目标，直到正式采用或回退；普通部署不会接管它。

## Git 与远端

| 现象 | 处理方式 |
| --- | --- |
| 没有 Git 提交、作者或 `main` | 核对主库 Git 状态。用 `--no-git` 创建的库先初始化 `main`，由用户配置作者，再创建首次本地提交 |
| `working tree has changes` | 先审阅、更新必要的技能登记并提交主库改动，再 `refresh`、创建工作区或批量部署 |
| `unknown staged changes` | 用 `git diff --cached` 检查暂存区，按自己的提交意图处理；aak 不自动清空暂存区 |
| `publication is disabled` | 只需本地提交时用 `sync --local-only`；远端发布按[私有库同步](workflows.md#可选-github-私有资产库)配置和授权 |
| `refresh` 不能快进或合并冲突 | 保留双方提交和工作区，按普通 Git 合并处理；工具不强推、不自动 stash 或丢弃冲突 |
| `gh` 已登录但 HTTPS Git 推送无法认证 | `gh` API 和 Git 凭据可能不同。审阅后可用 [GitHub CLI 的凭据设置](https://cli.github.com/manual/gh_auth_setup-git) `gh auth setup-git --hostname github.com`，或使用已配置的 SSH 认证 |
| 浅克隆无法解析正式版本 | 获取正式版本指向的历史提交；Home Repo 的 CI 使用完整历史，之后重新审计 |

定位问题时只分享合成复现、版本、命令和脱敏状态。`.local/` 被 Git 忽略不代表其中没有私人路径或技能快照，公开报告中不要附上整个目录。
