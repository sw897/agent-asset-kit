# 客户端适配与信任范围

以下是当前实现的默认值，可用 `agent add` 在本机登记不同路径、实例和启动参数，不代表客户端未来版本的保证。路径相对于 `configure --home` 选择的用户目录；它与 Home Repo 的位置不同。aak 不自动接管客户端的所有 profile 或环境变量，只管理明确启用的目标。

这些适配器负责技能目录、全局指令入口和技能发现。MCP 账号、原生连接及聚合入口由对应客户端或外部工具维护；技能依赖声明由 AAK 保留并静态校验。

| 类型 | 默认技能目录 | 指令入口 | 内置发现方式 |
| --- | --- | --- | --- |
| Codex | .codex/skills | .codex/AGENTS.md | app-server 的 skills/list |
| Claude Code | .claude/skills | .claude/CLAUDE.md | SDK initialize 目录 |
| Hermes | .hermes/skills | .hermes/SOUL.md | 所选 runtime 的原生技能目录 |
| OpenCode | .config/opencode/skills | .config/opencode/AGENTS.md | debug skill；可登记版本对应的只读探针 |
| ZCode | .zcode/skills | .zcode/AGENTS.md | skills list --json |
| DeepSeek Harness | .dsh/skills | .dsh/AGENTS.md | 见下节 |

表中列的是 AAK 实现的探针，不代表当前设备已验证对应客户端或所有版本。客户端还能发现其他目录时，仍以其本机配置和原生报告为准。`shared` 是底层共享目录绑定，不是原生客户端适配器，不能作为 `try --to` 或 `doctor --target` 的客户端名。

## 本机实例

`aak init --agents codex,claude` 或首次 `configure` 只登记所选客户端，不创建客户端账号或连接。查看和调整本机启用状态：

```sh
aak agent list
aak agent enable --name claude --reviewed
aak agent disable --name claude --reviewed
```

已有配置不会被重复 `configure` 覆盖。为一个客户端登记独立实例时，选择互不重叠的技能目录，并核对它实际读取的全局指令文件，例如：

```sh
aak agent add --kind codex --name codex-work --skills-root "/path/to/work-profile/skills" --instructions "/path/to/work-profile/AGENTS.md" --reviewed
aak doctor --native --target codex --instance codex-work
```

示例路径需换成本机真实位置。登记路径不会替客户端创建 profile 或改变其启动环境；原生探针的命令必须指向同一实例。需要额外启动参数时，用 `--command-json` 登记非空字符串数组；自定义目录探针用 `--probe-command-json`。按当前 shell 的规则传递 JSON，先检查 `agent list` 输出和探针行为。

本机实例名如 `codex-work` 保存在 `.local/config.json`。共享技能绑定仍使用 `codex` 这一类型，同类型的已启用实例都会参与目标展开；用支持的 `--instance` 参数缩小范围。`--agent codex` 仅记录维护者身份，不能代替 `--instance`。

确认实例后再运行 `attach` 预览；它会考虑所有启用的指令目标，`attach --apply` 才写入。新增或切换配置后，重新预览分发计划。

## Hermes

`agent bundled --instance INSTANCE` 列出所选 Hermes 的内置技能目录。内置技能由客户端维护，不自动入库。原生发现使用该实例的 runtime 和 Python 解释器调用 `_skill_catalog`，不调用 `skill_view`；安装布局与默认值不同的实例，用 `agent add --runtime ... --interpreter ...` 登记已审阅的实际路径。

## DeepSeek Harness

此适配需要用户提供并审阅本机安装包的 runtime 与 interpreter。工具使用随包附带的 Node 脚本调用 FileSystemSkillProvider.list，传入所选资产库 cwd 和原生 home，不发送用户问题。不从远端下载安装包，也不将本机安装路径写入共享包。

## 验证边界

内置探针按目录模式设计，不发送用户问题、不创建模型回合；它们仍会启动客户端或其 runtime。自定义 `command`、`probe_command` 和解释器是用户显式信任的本机程序；需要独立确认其行为，工具无法保证任意程序没有副作用。

合成协议通过、文件副本相同、真实客户端可发现、模型实际采用和业务验收分别记录。缺少客户端、版本不兼容或探针失败应保留 UNVERIFIED，不能以文件扫描替代。验证层次和工件位置见[验证范围](validation.md)；报告需记录本次设备、版本和实际观察，不能把合成测试当作真实客户端兼容承诺。
