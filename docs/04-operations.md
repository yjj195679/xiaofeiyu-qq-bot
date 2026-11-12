# 04 · 日常运维、提醒、备份与更新

## 启停和日志

```bash
sudo systemctl status qq-deepseek qq-napcat --no-pager
sudo systemctl restart qq-deepseek
sudo journalctl -u qq-deepseek -n 80 --no-pager
```

业务日志只记事件类型和失败类别，不应输出Key或消息正文。NapCat日志可能含登录二维码、WebUI凭据或其他账号信息，分享前自行脱敏，不要把整份日志发到公开Issue。

## 低余额提醒（可选）

先确认主人私聊、`/余额`与余额接口可用，再开启：

```bash
sudo systemctl enable --now qq-balance-watch.timer
systemctl list-timers qq-balance-watch.timer
```

每10分钟检查CNY可用余额，严格小于1元时私聊`owner_qq`。同一低余额周期只通知一次，恢复到至少1元后重置。状态文件只保存提醒标记，不保存余额；查询仍会在程序内获得实际金额。

```bash
sudo systemctl disable --now qq-balance-watch.timer
```

## 低频主动问候（可选）

```bash
sudo systemctl enable --now qq-private-greeting.timer
systemctl list-timers qq-private-greeting.timer
```

北京时间10点和18点各触发后随机延迟0–2小时；每天最多2条、间隔至少4小时，9点前与22点后不发。仅发给配置的主人，主人还必须在私聊白名单。生成问候会调用模型，停止方式：

```bash
sudo systemctl disable --now qq-private-greeting.timer
```

没有自动发空间动态的timer；空间发布通过明确的主人私聊命令执行。

## 安全备份

部署代码可从GitHub重新获取；真正不能丢的是服务器私密配置、QQ登录数据（若选择备份）、SQLite上下文和定时任务状态。它们不要放进公开仓库。

停业务服务后备份更容易得到一致的SQLite文件。示例在服务器私密目录保存业务备份：

```bash
sudo systemctl stop qq-deepseek
sudo install -d -m 700 /root/fish-backups
sudo tar -czf /root/fish-backups/fish-private-$(date +%Y%m%d-%H%M%S).tar.gz -C /opt qq-deepseek
sudo chmod 600 /root/fish-backups/*.tar.gz
sudo systemctl start qq-deepseek
```

该归档含Key与私人聊天，转移时使用你信任的私密存储。QQ登录数据的实际位置由QQ与启动用户决定，单独遵循其账号迁移流程；不要把原部署人的登录态复制给别人。

## 更新代码

在仓库目录先确认自己修改过哪些文件：

```bash
git status --short
git pull --ff-only
python3 -m unittest discover -s tests -v
sudo systemctl stop qq-deepseek
sudo bash scripts/install.sh
sudo systemctl start qq-deepseek
sudo python3 scripts/check.py
```

安装脚本保留原`config.json`与数据库，但会用仓库版本替换Python源码和自带素材。修改过运行时固定人设时应先保存自己的补丁，避免更新时覆盖。不要用已经过期的、内嵌旧bot.py的安装脚本覆盖新版模块。

## 回滚

记录本次更新前的Git提交：`git rev-parse HEAD`。遇到回归可把该提交检出到独立目录，测试后再运行安装脚本。不要直接删除现有数据库来“试试看”。如果涉及数据库结构变化，应依据对应版本说明恢复合适备份。

## 磁盘与资源

```bash
free -h
df -h /
sudo du -sh /opt/qq-deepseek
sudo journalctl --disk-usage
```

关注QQ缓存、NapCat日志与系统日志；本项目43个测试不代表外部QQ协议永不变更。health在线、模型可用与QQ已登录是三项独立状态。
