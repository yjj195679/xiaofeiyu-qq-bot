# 小肥鱼 · QQ × DeepSeek

一个运行在 Ubuntu 云服务器上的中文 QQ AI 伙伴。使用 NapCat 的 OneBot HTTP 接口与 DeepSeek API；业务代码仅依赖 Python 标准库，不需要在服务器本地部署大模型或配 GPU。

本仓库把从阿里云实名认证、学生认证、领券买服务器，到 QQ 扫码登录、机器人配置、systemd 自启、表情包、上下文和空间操作的过程整理为可复现步骤。公开版使用可配置账号，不包含原部署账号、群号、服务器地址、API Key、Token、登录态或聊天数据库。

## 从哪里开始

| 阶段 | 文档 |
|---|---|
| 账号实名、学生认证、领券、购买、续费检查 | [01 云服务器购买与认证](docs/01-aliyun-purchase.md) |
| Ubuntu 准备、项目安装、DeepSeek、本地网络与 QQ 登录 | [02 完整部署](docs/02-deployment.md) |
| 每个配置字段、OneBot 参数、权限和功能命令 | [03 配置与命令](docs/03-configuration.md) |
| 启动、定时任务、备份、升级与回滚 | [04 日常运维](docs/04-operations.md) |
| 不回消息、401、413、登录过期、空间失败 | [05 排错手册](docs/05-troubleshooting.md) |
| 架构、消息路径、上下文、思考模式 | [06 架构](docs/06-architecture.md) |
| 测试、复现范围和限制 | [07 验收与测试](docs/07-validation.md) |
| 官方文档、养鱼帖子、第三方项目参考 | [08 参考资料](docs/08-references.md) |

## 当前能力

- 私聊白名单；群内 @、叫“小肥鱼”、引用它的同群回复可以触发。直接回复后同一提问者60秒内的明确追问可继续。
- 可选低频群聊参与，默认不开；随机接话可以选择安静。
- 固定小肥鱼人设与示例；聊天无修改人设入口。程序隔离群、私聊和不同用户的上下文。
- 普通回复准备好后等2–4秒；长回复分条，条间1.5–3秒；代码块和块公式尽量保持完整。
- 最多30轮完整问答、24000字符；闲置24小时后失效。SQLite保存，重启可续聊，`/清空`可删除当前会话上下文。
- DeepSeek联网搜索、深度思考独立开关。默认思考关闭，只发送最终答案。
- 8张原创AI生成鲸鱼娘表情包、QQ自带表情、戳一戳回应。
- 主人私聊查询余额；可选低于1元提醒、低频主动问候。
- 主人私聊修改签名、发布文字/表情包图文说说、生成草稿、查看近期说说。说说默认所有人可见，明确指令直接提交。

## 环境

已在 Ubuntu 24.04、2核CPU、2GB内存的轻量服务器上运行过业务服务。Python 3.11+，systemd；NapCat与QQ版本需相互兼容。2GB主要用于QQ与机器人进程，模型推理发生在DeepSeek服务器。

业务代码无额外pip依赖。NapCat、QQ、xvfb、xauth属于外部运行环境，按部署文档安装。

## 最短路径

先完成[服务器购买与认证](docs/01-aliyun-purchase.md)，然后在服务器上：

```bash
sudo apt-get update
sudo apt-get install -y python3 git curl unzip xvfb xauth ca-certificates
git clone https://github.com/yjj195679/xiaofeiyu-qq-bot.git
cd xiaofeiyu-qq-bot
python3 -m unittest discover -s tests -v
sudo bash scripts/install.sh
sudo python3 /opt/qq-deepseek/configure.py
```

接下来按[完整部署第4步](docs/02-deployment.md#4-安装-napcat-和-qq)安装NapCat、导入生成的私密OneBot配置、扫码登录，最后启动服务。`install.sh`只安装本仓库源码和机器人服务，不会自行购买云资源、安装QQ、登录账号或启动收费请求。

## 目录

```text
bot.py                  HTTP事件入口、模型请求和固定人设
social.py               主人签名、空间、草稿与功能菜单
memory_flow.py          有界持久化上下文
thinking_flow.py        会话级思考开关
reply_flow.py           分条、等待、发送前权限检查
fish_flow.py            连续追问与引用接话
balance_watch.py        余额检查与去重提醒
private_greeting.py     低频问候
scripts/                安装、交互配置、只读检查
systemd/                自启服务与可选定时任务
config/                 空白配置模板
memes/                  8张表情包与白名单目录
persona*.json / *.txt    人设备份（运行时以bot.py为准）
tests/                  不连接真实QQ或模型的单元测试
.github/workflows/      GitHub Actions测试定义
```

## 实际验证与公开版

个人部署版曾实测QQ收发、群聊、图片、签名提交、公开说说、官方余额与搜索、思考模式、长上下文重启恢复。公开版将原先固定账号替换为`owner_qq`等配置项，并重写旧安装脚本；43项现有测试在公开版通过，安装脚本做了隔离目录演练。未宣称在全新付费实例上重新走完采购和NapCat安装。首次部署请逐项执行验收表。

许可证尚待仓库所有者选择，见[许可说明](LICENSE-NOTICE.md)。外部项目和软件各自的许可证保持不变。
