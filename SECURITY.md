# Security Policy / 安全策略

## Supported versions / 支持版本

OpenNexus is currently in alpha. Security fixes are applied to the latest release line and the current `main` branch. Older alpha builds may not receive backports.

OpenNexus 目前处于 Alpha 阶段。安全修复面向最新发行线和当前 `main` 分支，旧 Alpha 版本不保证获得回移修复。

| Version | Supported |
| --- | --- |
| 0.5.2-alpha1 | Yes |
| Earlier alpha versions | No guaranteed backports |

## Reporting a vulnerability / 报告漏洞

Do not open a public Issue for an unpatched vulnerability, leaked secret, or report containing private data. Use GitHub Private Vulnerability Reporting when it is enabled for this repository. If it is unavailable, contact the repository owner privately through an address published on the owner's GitHub profile without including exploit payloads in a public message.

未修复漏洞、已泄露密钥或包含隐私数据的报告不得创建公开 Issue。请优先使用仓库启用后的 GitHub Private Vulnerability Reporting；如该功能不可用，请通过仓库所有者 GitHub 主页公开的联系方式私下联系，不要在公开消息中附带利用载荷。

Include, when available:

- affected version or commit;
- affected boundary: WebView, Tauri host, AI Core, extension runtime, export, local model, or sync client;
- prerequisites and minimum reproduction steps;
- expected impact and whether user interaction is required;
- sanitized logs or proof of concept;
- suggested mitigation, if known.

可提供的信息包括：受影响版本或提交、受影响安全边界、最小复现条件、预期影响、是否需要用户交互、脱敏日志或验证材料，以及已知缓解方式。

## Sensitive data / 敏感数据

Never send real provider keys, account passwords, private vault content, personal information, production database dumps, private hostnames, or third-party data without authorization. Replace secrets before attaching logs or screenshots.

不得发送真实模型密钥、账户密码、私人 Vault 内容、个人信息、生产数据库、私人主机名或未经授权的第三方数据。附加日志和截图前必须替换或删除敏感字段。

## Response process / 处理流程

Maintainers will attempt to acknowledge a complete report, reproduce it, assess severity and affected versions, prepare a fix and regression test, and coordinate disclosure. Timelines depend on impact and maintainer availability; no guaranteed response SLA is offered during alpha development.

维护者会尽力确认完整报告、复现问题、评估严重程度和受影响版本、准备修复及回归测试，并协调披露时间。Alpha 开发阶段不承诺固定响应时限。
