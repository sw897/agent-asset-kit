# AgentAssetKit 操作指南

用户说“给这个 Agent 试试”时，只向指定目标安装。用户说“收起来”或“也给另一个 Agent 用”时，再执行对应步骤。以下示例中 codex、claude 和技能名均按用户的选择替换。

## 安装和接入

先确认用户指定的资产库，或从客户端的全局指令定位已接入的库（根目录有 `.agent-asset-kit.json`）。已有库按库内 README 安装其固定版本；有 wheel 和校验值时先核对 SHA256，再用原安装方式安装或升级。用 `aak --version` 和 `aak guide` 验证当前终端实际使用的版本。工具可从库外运行这两条命令。

从工具源码安装需要 Python 3.11+。在源码目录运行 `uv tool install .` 可把 aak 放入用户工具目录；若使用虚拟环境，在该环境运行 `python -m pip install .`，让使用它的客户端也能找到该环境的 aak。准备 Git 和 Gitleaks，用 `git --version` 和 `gitleaks version` 核对。

在已选资产库中执行后续命令，或显式传 `--root 库路径`。先读库内 AGENTS.md，按任务检索库内资料。已有配置用 `aak agent list` 核对。首次建立专用库可运行 `aak init 目录 --git`，进入该目录，运行 `aak configure --agents codex --gitleaks 扫描器绝对路径`，只指定用户选择的客户端。如仅想马上试一个技能、也没有已有资产库，直接执行下面的 try 即可自动建立默认库。如需让以后的会话找到库，在库内运行 `aak attach` 检查目标文件，再用 `aak attach --apply` 写入入口；活跃会话需重读。

## 试用、入库和分享

在主库中按本次请求选择一条命令：

- 试用：`aak try 来源 --to codex`。来源可以是含 SKILL.md 的目录、GitHub 或 skills.sh 链接。多个技能用 `--skill 名称` 选择。
- 入库：`aak save 技能名`。保存实际试用的副本，包括用户的改动。多个来源时用 `--from codex` 指定。
- 分发：`aak share 技能名 --to claude`。各 Agent 获得完整独立副本。在 Claude 改好后，可用 `aak save 技能名 --from claude` 收回。
- 确认结果：向用户报告实际来源、目标和安装位置。需要核对客户端能否发现技能时用 `aak doctor --native --target codex`。实际使用效果要在目标客户端验证。

## 结合资产库处理项目任务

用户要求结合资产库处理项目任务时，按需读取 `USER.md`、`MEMORY.md` 和相关技能。需要跨项目导航时，可创建 `agents/projects.md`，记录项目用途、仓库地址、约定入口与关联项目。库内有 `templates/project-index.md` 时可直接复制，已有索引时只查阅相关条目。本机克隆路径可按需放入 Git 忽略的 `.local/project-paths.md`。用户给出的项目路径也可直接使用。

操作前定位并读取目标仓库现有的 `AGENTS.md` 与更具体约定，在对应项目执行验证和经授权的 Git 操作。任务结束后，将验证过的通用方法整理到所属技能，项目特有的约定写回项目文档。已安装技能副本有改进时，审阅后用 `aak save 技能名 --from codex` 收回，再按需 share。

## 回流和跨设备维护

原生目录还有其他修改时先用 `aak collect` 保存候选。已配置远端的库用 `aak refresh` 拉取更新；在主库运行 `aak workspace --agent codex`，进入返回的工作区审阅。核对完整内容、来源许可、依赖与验证结果后，用 `aak accept 候选ID --agent codex --reviewed` 接受；直接编辑已入库技能后用 `aak revision --skill-id ID --agent codex --reviewed` 登记。双方都改过时用 `aak merge --candidate 候选ID` 比较共同基线与双方内容，审阅并 finalize，再接受新候选。副本缺失时保留库内版本。

本地提交和合并用 `aak sync --agent codex --message "Update reviewed assets" --local-only`。跨设备按资产库的远端配置和发布授权同步；另一台设备在它自己的库执行 refresh，再按需 share。本机目标、路径、凭据及运行环境留在本机。批量部署先用 `aak deploy` 看计划，确认目标和冲突后用 `aak deploy --apply --plan 计划ID`。对于已入库技能的新修订，只有需要保持其他目标的正式版本时才使用 `revision --trial`、`trial` 和 `promote`。

可用 `aak 子命令 --help` 查看参数。外部候选筛选用 discover、inspect、fetch、evaluate；保留第三方许可与上游提交。共享画像和约定用 note，待核实内容用 --inbox。技能的 `requirements.mcp` 保留逻辑服务、模式、使用方式、工具与包内说明引用；用 `aak audit` 静态校验、`aak index --agent codex --reviewed` 展示依赖。MCP 账号、连接和聚合入口由客户端或外部工具管理，实际可用性在目标环境核验。升级 CLI 后核对版本、指南和既有目标，再按需要更新 attach 入口。推送、恢复删除项或改变启用目标，按本次请求和库内授权执行。

入库写入异常会尝试安全回退，原候选保留。进程中断或后续编辑阻止回退时，用 `aak status` 查看未完成入库，在收据的原工作区审阅包、备份和元数据，再运行 `aak accept --recover 收据ID --agent codex --reviewed` 恢复到入库前状态。恢复拒绝覆盖新编辑；完成后可重试原候选。旧格式收据缺少恢复快照时保留现场并人工核对。
