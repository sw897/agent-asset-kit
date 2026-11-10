# 客户端适配与信任范围

以下是当前实现的默认值，可用 `agent add` 在本机登记不同路径、实例和启动参数，不代表客户端未来版本的保证。路径都相对于用户选择的 home；只管理明确启用的目标。

| 类型 | 默认技能目录 | 指令入口 | 内置发现方式 |
| --- | --- | --- | --- |
| Codex | .codex/skills | .codex/AGENTS.md | app-server 的 skills/list |
| Claude Code | .claude/skills | .claude/CLAUDE.md | SDK initialize 目录 |
| Hermes | .hermes/skills | .hermes/SOUL.md | 所选 runtime 的原生技能目录 |
| OpenCode | .config/opencode/skills | .config/opencode/AGENTS.md | debug skill；可登记版本对应的只读探针 |
| ZCode | .zcode/skills | .zcode/AGENTS.md | skills list --json |
| DeepSeek Harness | .dsh/skills | .dsh/AGENTS.md | 见下节 |

## DeepSeek Harness

此适配需要用户提供并审阅本机安装包的 runtime 与 interpreter。工具使用随包附带的 Node 脚本调用 FileSystemSkillProvider.list，传入所选资产库 cwd 和原生 home，不发送用户问题。不从远端下载安装包，也不将本机安装路径写入共享包。

## 验证边界

内置探针按目录模式设计，不创建模型回合。自定义 `command` 或 `probe_command` 是用户显式信任的本机程序；需要独立确认它的行为，工具无法保证任意自定义程序没有副作用。

合成协议通过、文件副本相同、真实客户端可发现、模型实际采用和业务验收分别记录。缺少客户端、版本不兼容或探针失败应保留 UNVERIFIED，不能以文件扫描替代。当前本机报告见 [验证范围](validation.md)。
