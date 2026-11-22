# 回流、试用与同步

以下工作流以已初始化的个人 Home Repo 为资产库，由 aak 管理技能在库与各 Agent 客户端之间的试用、回流、分发和跨设备同步。各 Agent 使用完整独立的技能副本，修改经审阅后可保存回库并按需分发。

涉及提交时，由用户配置目标库的 Git 作者；引入、采集、入库和实际部署等扫描操作需要可执行的 Gitleaks。预览与库内静态审计不要求扫描器。普通使用不需要任何业务 Skill 随工具安装，命令前提和写入范围见[命令导航](commands.md)。

## 建库与目录选择

`aak init --agents codex` 默认在 `~/agent-home`（Windows 为 `%USERPROFILE%\agent-home`）建立空白 Git 仓库并完成本机配置。Gitleaks 从 `PATH` 查找，也可通过 `--gitleaks` 指定。`init` 不提交、不连接远端；客户端入口仍需 `attach` 预览并用 `attach --apply` 应用。

目录选择顺序为 `--root`、`AGENT_ASSET_KIT_ROOT`、当前目录及其父目录中的已初始化库，最后是 `~/agent-home`。因此默认库可在任意项目中操作，进入其他已初始化库时自动使用该库；`aak status` 可核对实际路径。

此顺序用于操作已有库。`init` 不根据当前所属库选择目标，而是使用目录参数、`--root` / 环境变量或默认位置创建新库；帮助、版本和 `guide` 无需已建库。路径包含空格时加引号，Windows 使用实际 Windows 路径。

自定义位置用 `aak init /path/to/agent-home --agents codex`，后续进入该库或显式选择 `--root`。`init` 的目录参数与 `--root`/环境变量同时存在时必须指向同一位置。`aak init` 默认初始化 Git，旧 `--git` 参数仍可使用；仅需文件框架可选 `--no-git`，使用同步前再初始化 Git。

尚未建库时命令会提示先运行 `aak init`，不再由 `try` 隐式创建 `~/.agent-asset-kit`。旧位置的有效 AAK 库仍可通过 `--root` 或在库内使用；工具不自动搬迁，也不接管已有普通目录。

## 原生改动进入候选

单技能日常操作可直接在主库使用 `try`、`save`、`share`。这三条命令不支持独立工作区，且 `save` 只处理留有快捷操作收据的副本。下面的候选流程用于批量回流、已有原生技能和需要单独审阅的变更。

运行 `aak collect` 将新建或修改的完整 Skill 包快照保存到 `.local/candidates/`。它排除内置资源及运行环境，保留可执行文件意图，不复制认证或原生数据库。采集不会改写原生副本。

在主库采集后，处理并提交主库已有改动，再运行 `aak refresh` 和 `aak workspace --agent codex`。无远端时 `refresh` 返回未刷新。进入返回的工作区，或显式传入其 `--root`；本机候选和配置由主库共享，工作区的资产与 Git 暂存区分别隔离。检查完整文件、来源、许可、依赖、适用范围与验证结果，再执行：

```sh
aak accept CANDIDATE_ID --kind custom --agent codex --reviewed
aak sync --agent codex --message "Accept reviewed candidate" --local-only
```

第三方资源使用 `--kind third-party`。已有 ID 保留绑定和审阅后的分类、依赖等元数据。

`sync --local-only` 提交当前工作区并普通合并回主库，保留工作区和分支。回到主库后，才能继续使用快捷操作或预览批量分发。

入库先准备完整包和元数据快照。写入报错时，工具在内容仍匹配快照的情况下回退到入库前状态，保留候选供重试。进程中断或后续编辑阻止安全回退时，未完成的收据会阻止继续操作；用 `status` 查看原工作区与收据 ID，在该工作区审阅后恢复：

```sh
aak status
aak accept --recover ACCEPTANCE_ID --agent codex --reviewed
# 恢复到入库前状态后，可重试原来的 accept 命令
```

恢复核对完整包、备份以及所有相关元数据，不覆盖后续编辑；准备副本和原候选继续保留。工作区发生过变化时须选择收据中的原 `--root`。旧格式收据没有完整恢复快照，需要人工对照，工具不会猜测回退内容。

## 隔离编辑与双端变化

主资产库保留在 main。`aak workspace --agent codex` 从干净的主库创建 `assets/` 分支和独立 Git 工作区；后续进入该目录，或传入其路径作为 `--root`。不同会话不共享暂存区，候选和本机状态仍由主库管理。`sync --local-only` 普通合并回主资产库并保留工作分支，不自动 stash、不强推。

```sh
aak merge --candidate CANDIDATE_ID
# 检查保留的 base、repository、native 和 merged 完整包，解决冲突
aak merge --finalize MERGE_ID --reviewed
# 使用 finalize 输出的 candidate（新 ID），不要复用原候选 ID
aak accept MERGED_CANDIDATE_ID --agent codex --reviewed
```

双方变化时不会按时间戳选一方。资产库文件与登记哈希不一致也会阻止接受候选，先审阅并记录当前编辑，再生成合并。无法找到共同基线时停止并保留候选。缺失副本也会停止，需要明确选择恢复或停用目标绑定。

## 有限试用与正式版本

先提交旧基线，再编辑并审阅待试用内容。以下命令在主库执行；若在工作区准备修订，先将其同步合并回主库，再回到主库运行 `trial`、`promote` 与分发命令。

```sh
aak revision --skill-id SKILL_ID --agent codex --reviewed --trial
aak sync --agent codex --message "Stage reviewed trial" --local-only
aak trial start --skill-id SKILL_ID --ref HEAD --target codex --agent codex --reviewed
aak trial start --skill-id SKILL_ID --ref HEAD --target codex --agent codex --reviewed --apply --plan PLAN_ID
```

把预览返回的 `plan_id` 用于第二条 `trial start`。预览和应用的版本、技能、目标、实例与选项必须一致，目标变化会使计划失效。试用版本使用已提交的确切内容；其他目标继续收到旧正式版本。若目标有本机运行环境，检查后在预览和应用中都增加 `--preserve-runtime`。

验证实际任务后，准备证据 JSON，其中 `skill_id`、`sha256` 必须与本次试用一致：

```json
{
  "skill_id": "EXACT_SKILL_ID",
  "sha256": "EXACT_CONTENT_HASH",
  "status": "PASS",
  "checks": [{"scope": "actual task", "observed": "describe the observed result"}]
}
```

```sh
aak trial finish TRIAL_ID --result passed --reason "Observed task result" --evidence /path/to/evidence.json --reviewed
aak promote --skill-id SKILL_ID --ref HEAD --agent codex --reason "Reviewed passing evidence" --reviewed
aak sync --agent codex --message "Promote verified revision" --local-only
aak deploy
aak deploy --apply --plan PLAN_ID
```

失败时仍需准备标识同一技能、内容和实际检查的证据，把结果记录为 `trial finish TRIAL_ID --result failed --reason "Observed failure" --evidence /path/to/evidence.json --reviewed`，再用 `trial rollback TRIAL_ID --reviewed`。回退前复核当前副本和备份；并发编辑会停止回退并保留现场。preparing、rolling_back、interrupted 状态仍保有目标，passed 试用在采用或回退前也占用目标。试用中断用 `trial recover TRIAL_ID --reviewed`；独立部署中断时，先把目标恢复到收据记录的部署前或部署后状态，再执行 `recheck DEPLOYMENT_ID --reviewed`。首次分发的目标可恢复为空，目标均已核对后命令按实际副本校正本机基线。

新包没有旧正式版本时，待试用修订会被普通分发跳过；已有包的旧正式版本继续生效。`promote --without-trial` 是明确审阅后跳过业务试用记录的采用方式，不代表业务验证通过，需写明理由。对未处于待试用状态的技能，普通 `revision` 或快捷 `save` 会直接更新可供分发的已审版本；要保留旧正式版本并限定试用，应在入库或修订时明确使用 `--trial`。已处于待试用状态的包继续等待明确采用。

## 外部来源

`discover` 联网查询 skills.sh；`inspect`/`fetch` 将指定来源固定到上游提交并获取完整包和许可，不执行下载的脚本。`inspect` 不保存候选，`fetch` 返回本机候选与包路径。`evaluate` 提供审阅内容，使用者明确决定 reject、reference、trial 或 adopt。已有拒绝或停用决策保留，不能从历史自动恢复。

例如，在已初始化并配置的库中检查上游，再对确定的技能保存候选：

```sh
aak inspect OWNER/REPO
aak fetch OWNER/REPO --path skills/SKILL_DIRECTORY --ref COMMIT_OR_TAG
aak evaluate CANDIDATE_ID
aak evaluate CANDIDATE_ID --decision trial --reason "Reviewed complete package" --use-case "Describe the intended task" --agent codex --reviewed
```

替换返回的技能目录、确切提交和候选 ID；不要把上游 URL 中的版本 / 路径与显式 `--ref` / `--path` 混用。审阅完整包后，在工作区运行 `accept`，再同步、有限试用和正式采用。`adopt` 也可作为已审采用决策；`reject` / `reference` 不安装。许可未知的外部候选不能直接采用或分发。需要重新考虑已拒绝来源时，显式使用相应命令的 `--reconsider --reviewed`。

`try` 支持本地完整技能目录、`OWNER/REPO`、GitHub 与 skills.sh 链接。本地目录在快捷入口中按自定义来源处理，不自动推断第三方作者；第三方技能优先使用上游地址保留来源、提交和许可。

上游更新与本地修改冲突时仍需合并。下载、接受、正式采用和对某端启用是不同步骤。

## 本机接入与验证

`configure` 只写已初始化资产库的 `.local/config.json`。普通目录不能仅通过 `--root` 接入。`agent add` 可以登记自定义路径与实例；共享 manifest 的绑定只包含 agent 和 path，本机 instance 不得写入。

`configure` 不覆盖已有配置，显式选定的 `--agents` 名单之外的目标保持停用。新设备未配置时，首次 `try` / `share` 可准备本机配置；显式禁用的目标不会被快捷命令重新启用。后续管理见[客户端适配](adapters.md)。

`attach` 预览向启用客户端添加资产库入口，`attach --apply` 备份并保留非受管内容。资产库可用 `agents/instruction-blocks.json` 指定受管标记与库内 Markdown 模板，例如 `{"TEAM_RULES":"agents/team.md"}`。模板支持 `{{aak}}`、`{{root}}`、`{{local}}`、`{{skills_root}}`、`{{agent}}`。运行前检查预览中的确切文件。

`audit` 核对包、引用、依赖声明和副本，可能运行已登记的包内 Python 解释器读取安装元数据；`doctor --native` 进一步启动真实客户端的技能目录探针。只使用已审阅的本机程序。服务可用性、模型采用和业务任务效果需要在目标环境核验。

Home Repo 的 CI 可以运行 `aak audit --library-only --output artifacts/library-audit.json`。它检查 Git 跟踪路径的可移植性、包的字节与逻辑模式、登记、技能引用、技能依赖关系、MCP 声明及不可变正式版本，无需 `.local/config.json`、Agent 安装或业务依赖。不读取本机基线、试用状态或原生目录，不检查命令与运行环境，也不替代敏感信息扫描。包含正式版本指针的库应使用完整 Git 历史；浅克隆缺少所需提交会明确失败。该模式不能搭配本机 `--target` 或 `--instance` 选择，CI 示例见[验证说明](validation.md#验证个人-home-repo)。

macOS 的 `scheduler` 只预览本地采集任务，显式 `--apply` 才安装；默认间隔为 60 秒，自定义间隔须为正整数秒。其他平台调度尚未实现。`tick` 是独立的组合命令，会采集、刷新并实际部署同次生成的计划；发现候选或阻塞项即停止。不要将它当作只读检查或默认采集任务。

## 技能依赖声明

技能之间的依赖保存在包记录的 `dependencies` 中；命令、文件、Python 环境和 MCP 能力保存在 `requirements` 中。AAK 校验和保留这些元数据，回流、修订和 Git 副本继续携带已审阅的声明。

下面是一个已登记包记录的字段示例，不是整个 manifest。示例名称和依赖需要替换为实际已审内容；`helper-skill` 必须是库中已登记技能：

```json
{
  "dependencies": [
    {"skill": "helper-skill", "when": "export", "conditional": true}
  ],
  "requirements": {
    "commands": [
      {"mode": "export", "any_of": ["example-export"], "optional": true,
       "platforms": ["darwin", "linux"], "requires_agent": "codex"}
    ],
    "files": [
      {"mode": "export", "path": "~/.config/example/config.toml",
       "env": "EXAMPLE_CONFIG_PATH", "optional": true}
    ],
    "python": [
      {"mode": "render", "interpreter": ".venv/bin/python",
       "interpreter_by_platform": {"win32": ".venv/Scripts/python.exe"},
       "distributions": ["example-renderer"], "optional": true}
    ]
  }
}
```

`dependencies[].skill` 使用登记名称；必需依赖应在相应客户端有目标绑定，条件依赖用 `conditional` 和 `when` 说明。本机检查中，`platforms` 接受 `darwin`、`linux`、`win32`，`requires_agent` 接受已知客户端类型；不适用的模式返回 `NOT_APPLICABLE`。`files` 可用 `paths_by_platform` 指定各平台的路径或路径列表，`env` 提供现场覆盖。`python` 用 `interpreter_by_platform` 指定各平台的解释器。平台映射未命中时取通用 `path` 或 `interpreter`，省略通用值时该平台返回 `NOT_APPLICABLE`。Python 解释器路径相对原生技能目录，运行环境留在本机，不随完整技能资产复制。

这些声明不会安装依赖、运行技能正文或认证服务。普通审计检查命令位置、文件存在性和 Python 安装元数据；必需项缺失会阻塞，可选项缺失产生警告。`audit --library-only` 检查技能依赖关系、运行依赖字段和 MCP 声明。本机可用性由普通审计检查。Markdown 相对链接须指向技能包随附的文件或目录，登记的原生别名也应保留链接可达性。

`requirements.mcp` 沿用以下格式。先在技能包内准备说明文件，再把声明加入该技能的 manifest 记录：

```json
{
  "requirements": {
    "mcp": [
      {
        "service": "notes",
        "mode": "read",
        "via": "native",
        "tools": ["read_item"],
        "optional": false,
        "reference": "references/service.md"
      }
    ]
  }
}
```

`service` 是逻辑服务名，`mode` 是技能的使用模式，`via` 可为 `native` 或 `cli`。`tools` 保留原始能力名称，`optional` 说明该模式是否可选，`reference` 指向包内已有的相对文件。连接 ID、账号、聚合入口 ID、设备路径和凭据属于目标环境，不加入能力声明。

在资产库运行 `aak audit` 校验声明格式及引用，再用 `aak index --agent codex --reviewed` 展示能力依赖。审计结果中的 `packages[].mcp_declarations` 保留原声明；MCP 可用性由使用者在目标客户端或所选外部工具中检查。安装与分发技能不触发 MCP 发现、认证或连接配置。

旧版的 `aak mcp check/bind/connect` 与 `audit/doctor --mcp` 已移除，审计输出也不再包含 `mcp_checked` 或连接就绪结果 `mcp`。已有声明格式保持兼容；原生 MCP 配置、旧 `.local/mcp-bindings.json` 和 `.local/mcp-plans/` 原样保留，AAK 不再读取或维护这些本机记录。后续接入管理在对应客户端或外部管理工具中完成。

## 可选 GitHub 私有资产库

在主资产库目录操作。发送端需要已配置作者和认证的 Git、已登录的 `gh` 和一个自己的 GitHub 私有仓库。把 `OWNER/REPO` 替换成这个仓库的实际名称。`gh` 的 API 登录与 Git 的推送认证分别核对，见[故障处理](troubleshooting.md#git-与远端)。

内置远端操作限定为 github.com 私有仓库。配置中的仓库名和唯一 `origin` 的 fetch / push 地址必须对应，支持下面的 HTTPS 地址或 `git@github.com:OWNER/REPO.git`；公共库、其他托管平台和 SSH 主机别名不属于该远端流程。

1. 源库已有 `origin` 时先用 `git remote -v` 核对。首次连接空远端可用 `git remote add origin https://github.com/OWNER/REPO.git`。
2. 在本机 `.local/config.json` 中把 `github_repository` 设为 `OWNER/REPO`。允许此机推送时再把 `publication_enabled` 设为 `true`。
3. 运行 `aak sync --agent codex --message "Share saved skills"` 提交、合并并推送。工具检查远端地址、私有状态和暂存内容，完成后核对远端提交。

主库必须在 `main`；`sync` 会暂存当前库或工作区全部允许发布的改动，已有未知暂存项会使它停止。`--local-only` 不访问远端，也不要求发布授权；`--branch-only` 则要求隔离的受管分支和发布授权，只推送该分支，不合并 `main`，不能和 `--local-only` 混用。

接收端安装 CLI 和 Gitleaks 后，用 `git clone https://github.com/OWNER/REPO.git` 克隆资产库，进入克隆目录运行 `aak share 技能名 --to claude`。以后用 `aak refresh` 更新时，这台机器也要配置 `github_repository` 并登录 `gh`。拉取前先处理本地改动；只接收的机器可以保留默认的 `publication_enabled: false`。

共享库及其状态文件不能经目录链接读写库外内容。`.env.production` 等环境文件、私钥容器、SQLite/DB 文件及日志侧车会停止采集；`.env.example` 等示例仍须通过内容扫描。
