# 回流、试用与同步

所有命令针对已初始化的个人资产库。保持目标库的 Git 身份由用户自行配置，Gitleaks 必须可执行。普通使用不需要任何业务 Skill 随工具安装。

## 原生改动进入候选

运行 `aak collect` 将新建或修改的完整 Skill 包快照保存到 `.local/candidates/`。它排除内置资源及运行环境，保留可执行文件意图，不复制认证或原生数据库。采集不会改写原生副本。

检查完整文件、来源、许可、依赖、适用范围与验证结果，再执行：

```sh
aak accept CANDIDATE_ID --kind custom --agent codex --reviewed
aak sync --agent codex --message "Accept reviewed candidate" --local-only
```

第三方资源使用 `--kind third-party`。已有 ID 保留绑定和审阅后的分类、依赖等元数据。

入库先准备完整包和元数据快照。写入报错时，工具在内容仍匹配快照的情况下回退到入库前状态，保留候选供重试。进程中断或后续编辑阻止安全回退时，未完成的收据会阻止继续操作；用 `status` 查看原工作区与收据 ID，在该工作区审阅后恢复：

```sh
aak status
aak accept --recover ACCEPTANCE_ID --agent codex --reviewed
# 恢复到入库前状态后，可重试原来的 accept 命令
```

恢复核对完整包、备份以及所有相关元数据，不覆盖后续编辑；准备副本和原候选继续保留。工作区发生过变化时须选择收据中的原 `--root`。旧格式收据没有完整恢复快照，需要人工对照，工具不会猜测回退内容。

## 隔离编辑与双端变化

主资产库保留在 main。`aak workspace --agent codex` 创建独立 Git 工作区；后续传入该工作区路径作为 `--root`。不同会话不共享暂存区。`sync --local-only` 普通合并回主资产库并保留工作分支，不自动 stash、不强推。

```sh
aak merge --candidate CANDIDATE_ID
# 检查保留的 base、repository、native 和 merged 完整包，解决冲突
aak merge --finalize MERGE_ID --reviewed
# 使用 finalize 输出的 candidate（新 ID），不要复用原候选 ID
aak accept MERGED_CANDIDATE_ID --agent codex --reviewed
```

双方变化时不会按时间戳选一方。资产库文件与登记哈希不一致也会阻止接受候选，先审阅并记录当前编辑，再生成合并。无法找到共同基线时停止并保留候选。缺失副本也会停止，需要明确选择恢复或停用目标绑定。

## 有限试用与正式版本

```sh
aak revision --skill-id SKILL_ID --agent codex --reviewed --trial
aak sync --agent codex --message "Stage reviewed trial" --local-only
aak trial start --skill-id SKILL_ID --ref HEAD --target codex --agent codex --reviewed
aak trial start --skill-id SKILL_ID --ref HEAD --target codex --agent codex --reviewed --apply --plan PLAN_ID
```

预览和应用参数必须一致，目标变化会使计划失效。试用版本使用已提交的确切内容；其他目标继续收到旧正式版本。若目标有本机运行环境，检查后显式增加 `--preserve-runtime`。

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

失败时使用 `trial finish --result failed` 和 `trial rollback TRIAL_ID --reviewed`。回退前复核当前副本和备份；并发编辑会停止回退并保留现场。preparing、rolling_back、interrupted 状态仍保有目标，普通部署不能接管，其他试用不能占用。中断时检查收据，再使用 `trial recover` 或 `recheck`，不会自动重放写入。

## 外部来源

`discover` 查询 skills.sh；`inspect`/`fetch` 将指定来源固定到上游提交并获取完整包和许可，不执行下载的脚本。`evaluate` 提供审阅内容，使用者明确决定 reject、reference、trial 或 adopt。已有删除决策保留，不能从历史自动恢复。

上游更新与本地修改冲突时仍需合并。下载、接受、正式采用和对某端启用是不同步骤。

## 本机接入与验证

`configure` 只写已初始化资产库的 `.local/config.json`。普通目录不能仅通过 `--root` 接入。`agent add` 可以登记自定义路径与实例；共享 manifest 的绑定只包含 agent 和 path，本机 instance 不得写入。

`attach` 预览向启用客户端添加资产库入口，`attach --apply` 备份并保留非受管内容。资产库可用 `agents/instruction-blocks.json` 指定受管标记与库内 Markdown 模板，例如 `{"TEAM_RULES":"agents/team.md"}`。模板支持 `{{aak}}`、`{{root}}`、`{{local}}`、`{{skills_root}}`、`{{agent}}`。运行前检查预览中的确切文件。

`audit` 核对包、引用、依赖声明和副本；`doctor --native` 进一步读取真实客户端的技能目录。服务可用性、模型采用和业务任务效果需要在目标环境核验。

macOS 的 `scheduler` 只预览本地采集任务，显式 `--apply` 才安装；不会后台接受候选或发布。其他平台调度尚未实现。

## 技能依赖声明

技能之间的依赖保存在包记录的 `dependencies` 中；命令、文件、Python 环境和 MCP 能力保存在 `requirements` 中。AAK 校验和保留这些元数据，回流、修订和 Git 副本继续携带已审阅的声明。

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

在资产库目录操作。发送端需要已配置作者的 Git、已登录的 `gh` 和一个自己的 GitHub 私有仓库。把 `OWNER/REPO` 替换成这个仓库的实际名称。

1. 源库已有 `origin` 时先用 `git remote -v` 核对。首次连接空远端可用 `git remote add origin https://github.com/OWNER/REPO.git`。
2. 在本机 `.local/config.json` 中把 `github_repository` 设为 `OWNER/REPO`。允许此机推送时再把 `publication_enabled` 设为 `true`。
3. 运行 `aak sync --agent codex --message "Share saved skills"` 提交、合并并推送。工具检查远端地址、私有状态和暂存内容，完成后核对远端提交。

接收端安装 CLI 和 Gitleaks 后，用 `git clone https://github.com/OWNER/REPO.git` 克隆资产库，进入克隆目录运行 `aak share 技能名 --to claude`。以后用 `aak refresh` 更新时，这台机器也要配置 `github_repository` 并登录 `gh`。拉取前先处理本地改动；只接收的机器可以保留默认的 `publication_enabled: false`。

共享库及其状态文件不能经目录链接读写库外内容。`.env.production` 等环境文件、私钥容器、SQLite/DB 文件及日志侧车会停止采集；`.env.example` 等示例仍须通过内容扫描。
