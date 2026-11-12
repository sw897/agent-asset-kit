# 项目入口索引

需要跨项目导航时，将本模板另存为 `agents/projects.md`，用自己的项目信息替换示例条目。

| 项目 | 用途 | 仓库地址 | 约定入口 | 关联项目 |
| --- | --- | --- | --- | --- |
| 网站 | 用户界面 | `https://github.com/OWNER/website` | `AGENTS.md` | API |
| API | 网站使用的接口 | `https://github.com/OWNER/api` | `AGENTS.md` | 网站 |

仓库地址使用不含凭据的 URL，约定入口相对目标仓库根目录填写。各设备的克隆路径可按需记在 Git 忽略的 `.local/project-paths.md`，以项目名对应。

处理任务时只查阅相关条目，确认实际仓库位置，读取其中现有的 `AGENTS.md` 与更具体约定，在对应仓库执行验证和经授权的 Git 操作。索引用于定位上下文，修改范围以本次任务为准。
