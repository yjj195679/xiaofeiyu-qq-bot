# 07 · 测试与验收

## 离线单元测试

```bash
python3 -m unittest discover -s tests -v
```

当前公开版43项测试通过；覆盖身份/白名单、HMAC、chunked读取、上下文隔离/过期/重启、思考开关与请求参数、群内短时接话/引用、沉默标记、分条丢失检查、冷却、签名与空间权限、写入去重、发送失败停止。

测试中的账号为合成占位值，不是本仓库作者或部署人的真实QQ。

## 安装脚本演练

不写真实系统目录，不启动服务：

```bash
DESTDIR=/tmp/fish-stage bash scripts/install.sh
bash -n scripts/install.sh
python3 -m py_compile bot.py social.py memory_flow.py thinking_flow.py reply_flow.py fish_flow.py scripts/configure.py scripts/check.py
```

查看`/tmp/fish-stage/opt/qq-deepseek`与`/tmp/fish-stage/etc/systemd/system`。再次执行演练前可先在配置里放入自己的测试标记，确认install.sh保留已有配置。

## 上线人工验收

| 项目 | 通过标准 |
|---|---|
| 配置 | qq/owner_qq正确，Key/Token非空，主人在私聊白名单 |
| health | 8765返回ready，不以此代替QQ登录检查 |
| QQ身份 | get_login_info与配置匹配 |
| 私聊 | 主人发问并实际收到回复 |
| 群 | 已入群，白名单群中@能回复 |
| 边界 | 其他私聊/群不触发；群内不泄露余额 |
| 自循环 | 自己发出的普通回复不再触发 |
| 表情 | 至少一张白名单图能被QQ读取并送达 |
| 戳一戳 | 戳机器人回应；戳其他人不触发 |
| 慢速分条 | 长回复分条，内容未丢失，群里只首条@ |
| 续聊 | 多轮后能回答较早信息，重启后仍可恢复未过期上下文 |
| 思考 | 开/关/查状态有效，其他会话不被改变 |
| 联网 | 确实有搜索调用，不把失败说成成功 |
| 写入 | 先明确内容；签名提交后核对资料页，说说拿到tid并核对可见性 |
| 计时器 | 手动开启后才有任务，停用后不再主动触发 |

## 不作出的验证声明

- CI不使用真实QQ账号、API Key或真实云订单。
- 源个人部署曾实测的功能，不表示任意新机器/新QQ版本都已实测。
- 当前没有对公开版执行一笔新的云服务器采购，也未重新安装第三方QQ二进制。
- 空间接口、QQ登录与部分扩展接口依赖外部版本和账号条件。
