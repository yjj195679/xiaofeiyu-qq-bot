# 大肥鱼 Agent 升级：架构、部署与验收

## 当前结论

现网是 Ubuntu 24.04、2 vCPU、2 GiB、40 GiB 的轻量应用服务器。当前主线为 AstrBot 4.29.4 + NapCat + Python，另有酷狗 Node API 与 Python 桥接；不是 DeepSeek Harness。为避免双框架争用 QQ 登录、重复消费消息和增加常驻内存，本次继续使用 AstrBot 主线。

`dafeiyu_agent_core` v0.1.0 是第一阶段基础：SQLite 迁移、分范围长期记忆、关键词/可选向量混合检索、冲突替代、活动回执、日记真实性校验、独立预算、权限闸门、模块化提示词和本地心跳。它默认暂停自主活动，且在真实外部执行器接通前不会自行联网或发消息。

管理面板默认只监听 `127.0.0.1:8770`，所有状态 API 和开关均需要随机管理令牌。令牌首次启动时写入 `/opt/astrbot/data/dafeiyu_agent/admin.token`（`0600`），不得复制到仓库、聊天或公网 URL。远程查看应使用 SSH 端口转发；在完成 HTTPS、强认证和访问审计前不要把 8770 加入阿里云防火墙。

## 数据流

```text
QQ -> NapCat/OneBot -> AstrBot -> Agent Core -> DeepSeek/provider
                              |       |
                              |       +-> agent.sqlite（记忆、活动、预算、日记）
                              +-> 现有插件（语音、音乐、天气、情绪、群上下文）
```

工具结果必须先产生回执，回执状态为 `succeeded` 才能写成“做过的事”。计划、模型建议、失败或超时不能伪装为完成。

## 部署前备份

在服务器上的仓库目录执行：

```bash
sudo bash scripts/backup-agent.sh
```

脚本复制 AstrBot 数据、旧兼容目录和相关 systemd 单元；SQLite 使用在线备份 API，最后生成 SHA-256 清单。还应在阿里云控制台创建磁盘快照。任何回滚都先停止 AstrBot，再恢复对应目录，不直接覆盖运行中的 SQLite WAL 文件。

## 分阶段部署

先只暂存，不重启：

```bash
sudo bash scripts/deploy-agent-core.sh
```

脚本优先使用 `/opt/astrbot/venv/bin/python` 运行集成测试，以便复用 AstrBot 已安装的 `aiohttp` 等依赖；只有该虚拟环境不存在时才回退到系统 `python3`。

检查 `/opt/astrbot/data/plugins/dafeiyu_agent_core`、`/opt/astrbot/data/prompts` 和权限。维护窗口内再激活：

```bash
sudo bash scripts/deploy-agent-core.sh --activate
```

激活后验证：

1. `systemctl is-active qq-astrbot qq-napcat qq-kugou-api qq-kugou-bridge` 均为 `active`。
2. `curl -fsS http://127.0.0.1:8766/health` 成功且 QQ 连接存在。
3. 管理员私聊 `/Agent状态`；默认应显示“自主活动：暂停”。
4. 明确说“记住：我更喜欢……”，确认记忆工具成功；在另一群和另一用户中检索不到。
5. 测试点歌、语音、天气、记忆、群聊上下文和余额，确认旧功能没有回归。
6. 日志中不得出现 API Key、OneBot Token、Cookie、二维码或聊天正文。

## 回滚

```bash
sudo systemctl stop qq-astrbot
sudo mv /opt/astrbot/data/plugins/dafeiyu_agent_core /opt/astrbot/data/plugins/dafeiyu_agent_core.failed
sudo mv /opt/astrbot/data/dafeiyu_agent /opt/astrbot/data/dafeiyu_agent.failed
sudo systemctl start qq-astrbot
```

若现有插件也有升级，使用 `backup-agent.sh` 输出的目录逐项恢复；禁止整机盲目覆盖。

## 资源边界

2 GiB 主机当前可用内存约 586 MiB、无 Swap。不要同时部署 DSH、PostgreSQL、Redis、本地多模态模型和浏览器容器。第一阶段继续用 SQLite/WAL；视频理解和绘画走受限的外部服务，下载与抽帧设置大小、时长、并发和预算上限。若要运行浏览器或本地 ASR，应先升级内存或单独部署工作节点。
