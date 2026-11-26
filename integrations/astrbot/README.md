# AstrBot 运行版：语音与酷狗概念版

这是 2026-11-22 已部署并验证的运行源码快照。根目录的 `bot.py` 与旧教程仍是标准库主服务版本；本目录对应当前 AstrBot + NapCat 的运行方式。兼容插件按需调用原 `/opt/qq-deepseek` 中的余额、签名和空间逻辑，不需要同时启动旧主服务。

## 当前使用方式

1. `/点歌 邓紫棋 句号` 或 `/搜歌 歌名`：只发送候选，列出歌手、专辑、版本和时长。
2. 回复编号 `2` 或 `/点歌 2`：使用当前用户、当前会话的缓存候选发送，不重新搜索。候选有效期 5 分钟。
3. 选中后 **同时发送源音频文件和 QQ 语音版**，只下载一次。文件保持源编码、采样率和声道；语音版会压缩，长歌会分段。
4. `/音质 标准`、`/音质 高`、`/音质 无损`：分别请求 128 档、320 档、FLAC 档；默认高音质。`/音质` 查看设置。偏好按用户和会话保存。
5. 档位表示向平台请求的等级，不保证固定码率。平台不提供所选音质时明确报错；无损需要实际检测到无损编码，高音质也要通过实际码率检查，禁止将有损音源改名冒充无损。
6. `/酷狗登录`、`/酷狗登录状态`：仅原 AstrBot 管理员私聊使用。登录凭据只保存在服务器，不应导出到 Git。
7. `/语音 你好呀` 或明确要求“用语音回答我”：使用中文女声合成；不把合成语音说成真人录音。
8. `/余额`、`/近期说说`、`/空间 内容`、`/签名 内容` 等沿用旧版后端的权限。草稿不等于发布授权。

## 已修复的错歌问题

酷狗搜索结果中的 `Scid` 不是这里要传入的专辑歌曲 ID。专辑音频 ID 优先使用 `AlbumAudioID` / `album.album_audio_id`，旧搜索结构使用 **`MixSongID`**；不得回退到 `Scid`。

曾用邓紫棋《句号》复现：错误 ID 返回另一首歌曲；修正后返回《句号》，约 235 秒。现在解析播放地址前后还会核对候选歌名、版本及完整时长，校验失败立即停止，不擅自换歌。不同编码的文件哈希可能不同，不能把编码变化等同于歌曲身份变化。

## 文件清理与音质

- 禁用歌曲磁盘缓存。源文件上传完成后仍暂留给语音转换使用；两个发送流程结束后统一清理。
- 每段语音发送后删除对应切片；转码文件和源文件在 `finally` 中删除，异常或取消时同样清理。
- 曲目文件的后缀根据实际编码选择，例如 Vorbis 使用 `.ogg`，不是把它误标成 MP3。
- QQ 语音编码会降低音质；需要保留声道、采样率和源码率时使用同时发送的原文件。
- 酷狗公开音频 CDN 中有返回 HTTP 地址、但同域名 HTTPS 证书不匹配的情况。仅对代码中明确列出的公开音频主机保留原始协议；不关闭 TLS 验证，不把账号 Cookie 发送给音频 CDN。

## 目录与依赖

| 路径 | 内容 |
|---|---|
| `kugou/bridge.py` | 仅监听 `127.0.0.1:3072` 的 Meting 适配、扫码登录、歌曲身份核对、音质选择 |
| `plugins/astrbot_plugin_meting/` | 根据固定上游修改的 AGPL-3.0 插件，包含源文件、配置 schema、依赖与许可证 |
| `plugins/xiaofeiyu_compat/` | 旧功能兼容、计算、语音、酷狗登录命令及本机健康检查 |
| `persona.json` | 与已部署功能一致的小肥鱼 v3 人设 |
| `systemd/` | AstrBot、酷狗 API、适配服务的运行配置，不包含 EnvironmentFile 的真实内容 |
| `config/` | 无凭据的配置示例 |
| `UPSTREAM.json` | 上游地址和部署时的提交 SHA |
| `SOURCE_MANIFEST.json` | 从运行服务器导出文件的 SHA-256 校验清单 |

验证环境：Ubuntu 24.04、Python 3.12、AstrBot 4.29.4、NapCat OneBot v11、Node.js 18。系统需要 `ffmpeg` / `ffprobe`；AstrBot 虚拟环境还需 `edge-tts` 和插件 `requirements.txt` 中的依赖。酷狗 Node 服务使用 MakcRe/KuGouMusicApi，`platform=lite`，仅监听 `127.0.0.1:3071`。

## 安装到已配置的 AstrBot 实例

本目录是现有 AstrBot 实例的集成包，不会替你创建 QQ 登录态、模型凭据或管理账号。先完成 AstrBot / NapCat 基础接入，并把平台 ID 配置为 `xiaofeiyu`。原兼容目录应位于 `/opt/qq-deepseek`，其中配置由管理员在服务器本地维护。

1. 按 `UPSTREAM.json` 检出酷狗 API 的对应提交到 `/opt/kugou-api`，运行 `npm install --omit=dev --ignore-scripts`。
2. 在 `/opt/kugou-api/bridge-venv` 创建独立 Python 虚拟环境并安装 `aiohttp`。不要放宽 AstrBot 凭据目录的权限来给适配服务复用其虚拟环境。
3. 将 `plugins/` 下两个插件目录放到 `/opt/astrbot/data/plugins/`；安装 Meting 的 requirements 和 `edge-tts`。将 `kugou/bridge.py` 放到 `/opt/kugou-api/xiaofeiyu_bridge.py`。
4. 参考 `config/meting.example.json` 合并音乐插件配置；保留自己的其他框架设置。候选缓存 300 秒，关闭歌曲缓存，允许的单文件最大值为 80 MB。
5. 根据 `config/kugou-api.env.example` 在 `/etc/kugou-api.env` 创建本地环境文件，权限 `0600`，为设备 GUID/DEV 生成本机固定随机值。不要复用或公开别人的登录 Token。
6. 创建不允许交互登录的系统用户 `kugouapi`，代码保持可读，实际会话由 systemd 的 `StateDirectory=kugou-api` 创建在 `/var/lib/kugou-api`，模式 `0700`。
7. 采用 `systemd/` 中单元，`systemctl daemon-reload` 后启用 `qq-astrbot`、`qq-kugou-api`、`qq-kugou-bridge`。不要同时启动旧 `qq-deepseek` 主服务。
8. 将 `persona.json` 导入 AstrBot，保留或按自己的权限设置允许的工具：`astr_meting_music`、`xiaofeiyu_speak`、计算/天气/记忆等已安装工具；不要给普通成员扩大群管理权限。语音 provider ID 为 `xiaofeiyu-voice`，使用 `edge_tts`。
9. 由原管理员私聊 `/酷狗登录` 扫码授权，随后从搜索候选开始验证。

公开仓库不提供真实 `cmd_config.json`、`config.json`、OneBot Token、DeepSeek Key、酷狗 Cookie / session.json、聊天数据库或下载的音频。修改后端时应重启对应服务；仅框架启动成功不等于 QQ 反向连接已恢复，请检查本机 `127.0.0.1:8766/health`。

## 验证范围

线上已验证：登录后搜索、正确歌曲 ID、错歌/错时长拒绝、候选隔离与过期、不自动播放第一首、高音质原文件、单次下载双发送、QQ 消息序列化、正常及失败路径清理、登录重启保留。合成事件测试没有向真实群聊发送测试歌曲；客户端实际接收和试听仍需用户点歌确认。

运行本目录的离线回归检查：

```sh
python -m pip install aiohttp
python -m unittest discover -s integrations/astrbot/tests -v
```

## Agent Core v0.2.1 阶段状态

v0.2.1 采用增量升级，不替换现有 AstrBot、NapCat、音乐和兼容插件。当前代码已离线验证：

- 中文字符 N-gram/BM25 风格检索、可选语义向量混合排序，以及 Embedding 不可用时的关键词降级；外部 Embedding 默认禁用，未经授权不会发送聊天文本。
- 按平台、机器人、会话类型、会话、用户和访问范围隔离的持久记忆；支持版本、更正、归档、恢复、逻辑删除和导出。
- SQLite 原子预算预留、结算和释放，可阻止并发超额预留。AstrBot 4.29.4 的 `on_llm_request` / `on_llm_response` / `on_agent_done` 适配已接入：调用前预留、成功后按 Provider 报告的缓存/非缓存输入及输出 Token 结算、失败时释放；当前候选仍须完成真实模型请求验证，因此尚不标记为线上完成。
- 普通聊天和自主活动使用独立预算桶。默认每日普通聊天 200000 Token/200 次，自主活动 12000 Token/24 次；可用 `DAFEIYU_*_TOKEN_LIMIT`、`DAFEIYU_*_CALL_LIMIT` 和每次预留上限调整。成本计价默认关闭，只有同时配置单价与成本上限才参与拦截，避免把未知价格伪装成真实费用。
- 人格状态采用受限增量和不可变版本历史；基于活动更新时必须引用成功回执，可查看历史并恢复旧版本。联系人关系档案按会话范围隔离。
- 日记只能引用真实成功活动，空闲心跳不会生成成功活动或日记证据。
- 动态状态、记忆和预算作为临时用户内容注入；静态模块化提示词保持稳定，外部内容不能覆盖安全规则。
- 部署脚本默认只构建候选版本；显式 `--activate` 才原子切换，切换失败自动恢复上一版本。数据库迁移先在 SQLite 在线备份上预演并检查完整性。

离线测试通过不等于 QQ、模型提供商和外部服务已在线验证。部署前必须备份服务器配置与数据库；部署后需分别验证 AstrBot 健康、OneBot 连接、真实私聊命令、记忆重启持久化和 Token 账本。自主外部执行仍保持暂停。

## 来源与许可证

修改版 Meting 来源和提交见 `UPSTREAM.json`，遵循同目录 `LICENSE` 中的 GNU AGPL v3；修改日期和范围在 `main.py` 顶部注明。该许可证覆盖此修改版及其派生部分，不把根目录旧代码自动重新授权。KuGouMusicApi 仅以依赖形式引用，按其原许可证使用；仓库不包含该服务的登录数据或二进制。
