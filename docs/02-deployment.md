# 02 · 从空白Ubuntu到小肥鱼上线

固定安装目录`/opt/qq-deepseek`。网关与业务在同一服务器，不需要域名、Nginx或公网机器人端口。先读完一遍再执行，所有示例账号/IP均需替换。

## 1. 更新系统与安装基础工具

```bash
sudo apt-get update
sudo apt-get install -y python3 git curl unzip xvfb xauth ca-certificates
python3 --version
uname -m
```

需要Python 3.11或更新。业务代码无第三方pip依赖。首次系统升级是否需要重启，以系统提示为准；不要在QQ扫码到一半时重启。

## 2. 下载项目并跑离线测试

```bash
git clone https://github.com/yjj195679/xiaofeiyu-qq-bot.git
cd xiaofeiyu-qq-bot
python3 -m unittest discover -s tests -v
```

离线测试使用临时目录、模拟API，不应连接真实QQ、发送消息或消耗DeepSeek额度。

## 3. 安装本项目并交互配置

```bash
sudo bash scripts/install.sh
sudo python3 /opt/qq-deepseek/configure.py
```

按提示填写机器人QQ、主人QQ、群白名单。留空群号可以先验证私聊。API Key用隐藏输入；不要把Key作为命令行参数或粘贴进群。OneBot Token自动随机生成。

配置向导生成两份私密文件：`config.json`和`onebot11_机器人QQ.json`。它们权限600，不可提交GitHub。安装脚本不会覆盖已有`config.json`，也不会启动机器人发送请求。

DeepSeek Key在[官方平台](https://platform.deepseek.com/)创建，按官方方式使账户可用。云服务器券与DeepSeek账户余额是不同的东西，不能假设云券能支付DeepSeek官方API。模型默认`deepseek-flash`，实际可用模型以你的账户和官方文档为准。

## 4. 安装 NapCat 和 QQ

在[NapCat官方Shell文档](https://napneko.pages.dev/guide/boot/Shell)找到当前Linux安装命令。安装器地址、参数和兼容QQ版本会变化，因此本仓库不复制旧安装器或打包QQ二进制。

选择Linux原生Shell路线，与本文的systemd示例保持一致。不要先选择Docker，再把原生QQ进程的服务模板套到容器上。

按官方文档将脚本下载为本地文件，先查看脚本和帮助，再执行：

```bash
# 先用官方页面当前提供的下载命令生成 napcat.sh
less napcat.sh
bash napcat.sh --help
# 确认来源和参数后，按其原生 Shell 安装方式执行
sudo bash napcat.sh
```

若安装器询问Docker或CLI模式，依据当前官方帮助选择原生QQ/NapCat Shell。安装完成后记下真实的QQ启动文件、NapCat配置目录和WebUI入口。常见路径可能是`/Napcat/opt/QQ/qq`，但不要假设所有版本相同。

```bash
sudo find /Napcat /opt /root -maxdepth 8 -type f -name qq 2>/dev/null
```

一次只运行一个网关实例，避免两个QQ进程竞争相同账号或端口。

## 5. 配置 OneBot 双向 HTTP

将向导生成的私密OneBot配置合并到当前账号配置，或在NapCat WebUI按下表填写。不要覆盖网关中无关的既有账号设置。

| 网关设置 | 内容 |
|---|---|
| HTTP服务端 | 开启；127.0.0.1:3000 |
| HTTP客户端/反向HTTP | 开启；http://127.0.0.1:8765/event |
| 消息上报格式 | array |
| 两端Token | 与本项目config.json里的onebot_token一致 |
| reportSelfMessage | 需要自身命令时才开启，并配合allow_self_commands |
| WebUI | 127.0.0.1:6099，保留你自己的WebUI密码 |
| CORS/调试公开访问 | 不需要开启 |

若已确定NapCat配置目录，可再次运行向导并指定：

```bash
sudo python3 /opt/qq-deepseek/configure.py --napcat-config-dir /实际的/napcat/config
```

目标账号文件不存在时会生成；存在时向导保留原文件，需自己合并。再次运行向导时留空Key保留已有Key。

WebUI密码与OneBot Token不是一个凭据。回调认证是NapCat发送的`x-signature` HMAC-SHA1，业务已实现；不要把它改成仅检查Bearer头。

## 6. 远程访问WebUI与扫码

WebUI只监听本机时，从自己的电脑建立SSH转发。下面IP为文档示例地址：

```bash
ssh -L 6099:127.0.0.1:6099 admin@203.0.113.10
```

将用户名和IP换成自己的服务器信息，保持该终端打开，再访问自己电脑的`http://127.0.0.1:6099`。不要因为打不开就把WebUI无密码开放到公网。

手机QQ扫描当前机器人账号的登录二维码并按提示确认。二维码过期就重新获取当前二维码，不要反复扫截图。验证码、设备确认等由账号持有人在官方QQ中处理。

## 7. 配置NapCat开机自启

复制模板，在服务器本地编辑，把`/ABSOLUTE/PATH/qq`替换为第4步找到的启动路径。模板的`--no-sandbox`属于该原生QQ部署示例，是否需要应与当前QQ/NapCat官方启动方式一致。

```bash
sudo cp systemd/qq-napcat.service.example /etc/systemd/system/qq-napcat.service
sudo nano /etc/systemd/system/qq-napcat.service
sudo systemctl daemon-reload
sudo systemctl enable --now qq-deepseek.service
sudo systemctl enable --now qq-napcat.service
```

模板设置`HOME=/root`，用于本文按root安装网关的路线；如果你的网关是其他用户安装，应统一该用户的HOME、QQ登录数据目录、文件权限和启动方法。不要混用root与admin的登录数据。

## 8. 连通性检查

在仓库目录运行：

```bash
sudo python3 scripts/check.py
systemctl is-active qq-deepseek qq-napcat
curl -fsS http://127.0.0.1:8765/health
sudo ss -lntp | python3 -c 'import sys; print("".join(x for x in sys.stdin if any(p in x for p in (":3000",":6099",":8765"))))'
```

检查消息服务在线、QQ身份匹配，以及三个服务端口没有监听到不需要的公网地址。health正常不代表QQ一定登录，必须分别检查。

## 9. 从私聊到群聊验收

1. 主人号私聊机器人“你好”，确认它实际回复。
2. 再发“刚才我说了什么”，确认同会话上下文。
3. 发`/功能`查看菜单；用`/深度思考 状态`核对默认状态。
4. 请求一张“加油”表情包，双击头像测试戳一戳。
5. 将机器人加入白名单群，再由群成员@它测试；群号写进配置不等于机器人已入群。
6. 测试第二个未授权账号，确认不会私聊响应；在未授权群同样不应响应。
7. 最后测试余额、搜索与需要实际写入的签名/空间指令，见[验收表](07-validation.md)。

至此基础部署结束。可选提醒、主动问候和低频群聊参与见下一章与运维章，不在首次安装时自动启用。
