# 验证范围

从源码安装当前版本，在仓库根目录运行以下命令。若省略 `--scanner`，测试会下载并校验固定版本的 Gitleaks。

```sh
python tests/e2e/ci.py --scanner /absolute/path/to/gitleaks
```

[GitHub Actions](https://github.com/sw897/agent-asset-kit/actions/workflows/verify.yml) 分别在 Ubuntu、macOS 和 Windows 运行 CLI。各平台都测试初始化、库边界、技能试用与回流、跨库同步和 MCP 接入协议。涉及 POSIX 符号链接、可执行位和进程钩子的场景按平台运行。

测试使用临时 Git 仓库、合成用户目录和模拟客户端。每个套件把断言和结果写入本机 `artifacts/e2e/`。要验证实际客户端是否发现技能，在对应设备上运行 `aak doctor --native`。模型调用和具体业务效果需要用实际任务验证。

`transactions.py` 通过真实 CLI 执行文件切换、Git 和扫描，并用 Python 审计钩子注入元数据写入错误与进程退出，检查入库回退、重复恢复、并发编辑保护以及无效同步参数不改变暂存区和提交。POSIX 还使用操作系统文件大小限额检查准备失败。故障注入不等于真实磁盘耗尽或断电验收。
