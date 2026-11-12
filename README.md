# AgentAssetKit (aak) · 面向 Agent 的资产管理工具

AgentAssetKit is a CLI for AI agents to manage reusable agent assets across devices. Codex, Claude Code, Hermes and OpenCode can use it to try agent skills, save local edits and share selected versions. The same Git library can hold user preferences, shared instructions and project templates.

你可以直接告诉 Agent：「把这个技能装到 Codex 试试。好用的话，把我改过的版本收进资产库，再分享给 Claude Code。」Agent 通过 `aak guide` 查用法，按你的指令操作。每个客户端使用独立副本，双方都改过时可以对照合并。

技能（`SKILL.md`）支持试用、入库、回流和分发。画像、跨 Agent 约定和模板按目录组织，随个人 Git 资产库在设备间同步。

## 交给 Agent 安装和使用

把这句话交给你的 Agent，并附上本项目的路径或链接：

> 请按项目的 Agent 操作指南安装 AgentAssetKit，沿用我已有的资产库，只接入 Codex，验证后告诉我如何让它试用一个技能。

[Agent 操作指南](src/agent_asset_kit/resources/agent-guide.md) 随安装包提供。安装后，在任意目录运行 `aak guide` 即可读取本机版本的同一份指南。`aak attach` 可将入口加入所选客户端的全局指令，无需安装管理技能。

## 安装

准备 Python 3.11 或更新版本、Git 和 [Gitleaks](https://github.com/gitleaks/gitleaks#installing)。运行 `gitleaks version` 应能看到版本号。从 GitHub 获取 `main` 的当前源码后，在源码目录运行下方安装命令。

```sh
git clone --branch main https://github.com/sw897/agent-asset-kit.git
cd agent-asset-kit
```

**macOS / Linux**

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
```

**Windows PowerShell**

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install .
```

## 快速上手

**先试用。** 下面使用源码自带的演示技能，可以直接复制命令。把 `codex` 换成 `claude` 等目标即可装到另一个 Agent。

```sh
aak try ./tests/fixtures/native-skill --to codex
```

成功时会显示技能名 `fixture-skill` 和安装目录。在 Codex 新会话中，请它“使用 fixture-skill 运行示例”。示例脚本会输出 `synthetic fixture`。你也可以直接编辑安装目录里的副本。首次试用会自动准备本地库 `~/.agent-asset-kit`，此时 `aak catalog` 中还没有这个技能。

**用好了再保存。** 这一条会把试用过的副本连同你的改动收进资产库：

```sh
aak save fixture-skill
```

只有一个已记录的副本时会自动选中。有多个时，命令会列出可选来源，例如用 `--from codex` 指定。加 `--dry-run` 可以先看文件名变化。

**需要时再分发。** 要让本机 Claude Code 使用保存的版本，运行：

```sh
aak share fixture-skill --to claude
```

两个 Agent 各有一份独立副本。之后若在 Claude 中修改，可用 `aak save fixture-skill --from claude` 收回，再用 `aak share fixture-skill --to codex` 更新 Codex。遇到冲突会停下并保留双方修改。

换成自己的技能时，把第一条命令中的目录替换为含 `SKILL.md` 的实际路径，保存和分发使用安装结果中的技能名。也支持 GitHub 仓库和 skills.sh 技能链接。仓库中有多个技能时用 `--skill NAME` 选择。通过链接保存的第三方技能保留来源、提交和许可。用 `aak agent list` 查看本机配置；有多个同类实例时用 `--instance` 选择。

## 从资产库到一次完整任务

除了管理技能，你也可以让 Agent 在处理项目任务时查阅自己的偏好、共享方法和项目入口。

**接入一次。** 完成上面的试用后，进入自己的资产库，预览并应用全局入口。沿用默认库时运行下方命令，自定义库则进入对应目录。

```sh
cd ~/.agent-asset-kit
aak attach
aak attach --apply
```

新会话可以通过这个入口找到资产库，当前会话需要重新读取入口。

**直接交代任务。** 在项目目录或资产库中告诉 Agent 你的目标，例如下面这句话。

> 参考我的资产库，给网站增加搜索功能，需要时配合修改 API。先核对相关项目的约定和验证命令，完成后把验证过的通用方法整理到相应技能。

Agent 通过 `aak guide` 查用法，按需读取 `USER.md`、`MEMORY.md` 和相关技能。有项目索引时，从中找到本次任务涉及的仓库，也可以直接使用你给出的项目路径。进入目标仓库后，读取现有 `AGENTS.md` 与更具体的约定，在对应项目中执行修改和验证，提交按本次授权处理。

**收回有用的改进。** 如果 Agent 改进了已安装的技能副本，审阅后用 `aak save 技能名 --from codex` 收回，需要给其他 Agent 使用时再运行 `aak share 技能名 --to claude`。项目特有的约定写入项目文档，可复用的方法整理进所属技能。资产库的提交和跨设备同步见下方进阶工作流。

## 可选的项目索引

需要在多个项目间导航时，让 Agent 将资产库内的 `templates/project-index.md` 另存为 `agents/projects.md`，填写项目用途、仓库地址、约定入口和关联项目。新建的资产库自带模板，已有库也可以从工具项目复制。[项目索引模板](src/agent_asset_kit/resources/library/templates/project-index.md)

索引是一份按需查阅的 Markdown 文件。各设备的实际克隆路径可按需记录在 Git 忽略的 `.local/project-paths.md`，以项目名对应。Agent 根据当前任务读取相关条目，进入项目后核对现场。

## 你的资产库里有什么？

首次在库外试用会默认建立 `~/.agent-asset-kit/`。这是你自己的资产库。以下是常用位置，`.local/` 中的文件由工具在本机操作时生成。

```text
~/.agent-asset-kit/
├── AGENTS.md          Agent 维护这个库时遵循的约定
├── USER.md            用户的稳定偏好与协作习惯
├── MEMORY.md          多个 Agent 共用的核心事实与查阅入口
├── memory/            shared/ 放已确认的明细，inbox/ 放待核实内容
├── skills/
│   ├── custom/        自定义技能，每个目录是一个完整的 SKILL.md 包
│   ├── third-party/   引入的第三方技能，保留来源与许可
│   └── manifest.json  登记技能 ID、内容哈希和分发目标
├── agents/            客户端接入、工具说明与可选的 projects.md 项目索引
├── templates/
│   ├── agents/        可复用的项目约定模板
│   └── project-index.md  可选的项目索引模板
└── .local/            本机路径、候选修改和部署记录，Git 忽略
```

例如，上面的 `aak try` 把示例装进 **Codex 的技能目录**。运行 `aak save fixture-skill` 后，库里才会出现 `skills/custom/fixture-skill/`。随后 `aak share` 从库里复制一份给 Claude Code。换设备时通过 Git 同步已提交的资产，再选择要在新设备上启用的技能。详见[资产组织方案](docs/organization.md)。

## 自定义资产库位置

```sh
aak init my-agent-assets --git
cd my-agent-assets
aak catalog
```

`init` 只创建空白文件与目录，不采集已安装资源、不写入原生 Agent、不配置远端，也不提交。目标必须为空或尚不存在。操作需要有效的 `.agent-asset-kit.json` 标记；`--root` 不会自动接管普通目录或工具仓库。隔离工作区的主库也必须通过标记检查。在资产库或其子目录中运行时，可省略 `--root`。

## 进阶审阅与跨设备同步

在资产库中运行进阶命令。使用默认库时先运行 `cd ~/.agent-asset-kit`。只想在本机 Agent 之间分发技能，前面的 `share` 就够了。

要给资产库创建本地 Git 提交，先配置自己的 Git 作者，再运行 `aak sync --agent codex --message "Save skills" --local-only`。跨设备可使用自己的 GitHub 私有资产库。首次在另一台机器克隆该资产库后，在克隆目录运行 `aak share 技能名 --to codex`，即可把库里的技能复制到本机。已有远端配置的设备可运行 `aak refresh` 拉取更新，再按需分发。[远端配置与完整工作流](docs/workflows.md#可选-github-私有资产库)

若需收集其他原生修改、比较双方版本、试用已入库的新修订或批量部署，见[审阅与分发工作流](docs/workflows.md)。`aak init-project` 可为其他项目生成约定预览。

## 接入与边界

- 适配器包含 Codex、Claude Code、Hermes、OpenCode、ZCode、DeepSeek Harness；每台设备只配置实际管理的目标。原生版本变化可能要求更新适配器。
- 技能保留完整目录副本，不用链接连接资产库和客户端。未知改动或缺失副本阻止覆盖，缺失不会变成全局删除。
- 来源、服务分类、目标绑定、条件依赖和正式版本分别登记；客户端路径、实例别名、连接映射、候选、收据和运行环境留在本机；领域服务需要的路径可经审阅进入用户自己的私有技能，工具模板不包含这些内容。
- 客户端身份、认证、原生记忆、完整项目历史和知识库正文继续由原来源维护。
- `--reviewed` 记录操作者确认；审核质量依靠真实审阅和验收。扫描不是运行时沙箱。

可选远端同步当前保留对 **GitHub 私有资产库** 的校验，推送时需要匹配的 `origin`、已认证的 `gh` 和本机发布授权。工具代码本身的公开发布与用户资产库的私有同步分别管理。初次本地使用不需要 GitHub。

## 开发与验证

```sh
aak --help
python3 tests/e2e/ci.py --scanner /absolute/path/to/gitleaks
git diff --check
```

测试使用合成 home、临时 Git 仓库与原生协议桩，包含真实文件切换、Git 合并和秘密扫描；不接触真实 Agent 配置或调用业务服务。报告写入忽略的 `artifacts/e2e/`。[验证范围](docs/validation.md)明确区分本机执行、协议模拟、真实原生发现和未执行的平台。

贡献方式见 [CONTRIBUTING.md](CONTRIBUTING.md)，许可为 [MIT](LICENSE)。
