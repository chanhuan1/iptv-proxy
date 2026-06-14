# 飞牛 IPTV 代理

将运营商 RTSP 直播源实时转码为 HLS 流，让飞牛影视可以直接播放 IPTV 频道。

## 背景

国内运营商 IPTV 直播源通常是 **RTSP 协议 + MPEG-2 编码**，而飞牛影视只支持 **HTTP/HLS 协议 + H.264 编码**，无法直接播放。

本代理在收到飞牛的播放请求时自动启动 ffmpeg，将 RTSP 流转码为 HLS 切片并通过 HTTP 输出，飞牛拿到 HTTP 地址就能直接播放。频道不看了之后自动关闭转码释放资源。

## 前提条件

- 一台能跑 Docker 的 NAS 或服务器
- 已安装飞牛影视
- 有可用的运营商 IPTV 直播源（重庆联通已验证）

## 第一步：获取频道数据

需要准备两个文件：`channels.json`（RTSP 地址库）和 `iptv_channels.m3u`（频道列表）。

### 1. 生成 channels.json

访问运营商的 IPTV JSON 接口（类似 `http://<运营商IP>:8081/service/<账号>.json`），例如：

将返回的 JSON 保存为 `channels.json`。格式要求：

```json
[
  {"id": 1, "name": "CCTV-1高清", "url": "rtsp://..."},
  {"id": 2, "name": "CCTV-2高清", "url": "rtsp://..."}
]
```

> 每个频道必须有唯一的数字 `id`，`url` 是 RTSP 地址。

### 2. 生成 iptv_channels.m3u

M3U 文件决定飞牛影视里显示哪些频道。格式：

```
#EXTM3U
#EXTINF:-1 tvg-name="CCTV1" group-title="央视频道",CCTV-1高清
http://NAS_IP:18888/ch1/index.m3u8
#EXTINF:-1 tvg-name="CCTV2" group-title="央视频道",CCTV-2高清
http://NAS_IP:18888/ch2/index.m3u8
```

- `ch1` 对应 `channels.json` 里 `id` 为 1 的频道
- `group-title` 控制飞牛影视里的分组
- IP 地址改为你 NAS 的 IP

项目里的 `generate_m3u.py` 可以根据 `channels.json` 自动生成 M3U：

```bash
python3 generate_m3u.py
```

## 第二步：部署到飞牛 NAS

在飞牛 NAS 上创建一个目录（如 `/vol1/1000/docker/iptv-proxy`），放入以下三个文件：

```
iptv-proxy/
├── channels.json          # RTSP 地址库
├── iptv_channels.m3u      # 频道列表
└── docker-compose.yml     # 部署配置
```

`docker-compose.yml` 内容：

```yaml
services:
  fntv-iptv-proxy:
    image: chanhuan01/fntv-iptv-proxy:latest
    container_name: fntv-iptv-proxy
    restart: unless-stopped
    network_mode: host
    volumes:
      - ./channels.json:/app/channels.json:ro
      - ./iptv_channels.m3u:/app/iptv_channels.m3u:ro
```

在飞牛 Docker 中导入 compose 并启动容器。

## 第三步：导入飞牛影视

1. 打开飞牛影视 → 设置 → 电视直播
2. 添加直播源，选择 **文件导入**
3. 上传 `iptv_channels.m3u`
4. 导入成功后会显示频道列表，点击即可播放

## 管理频道

部署后想增删频道，直接编辑 NAS 上的 `iptv_channels.m3u` 文件：

- **删除频道**：删掉对应的 `#EXTINF` 行和下一行 URL
- **添加频道**：按上述格式新增两条，URL 中的 `chXX` 必须对应 `channels.json` 里的 ID
- **改名改分组**：修改 `tvg-name` 和 `group-title`

代理会自动检测文件变化，无需重启容器。

> 如果新加频道在 `channels.json` 中不存在对应的 RTSP 地址，则无法播放。

## 进一步配置

编辑 `app.py` 中的以下变量（需要重新构建镜像）：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `IDLE_TIMEOUT` | 30 | 频道没人看后多少秒关闭转码 |
| `MAX_CONCURRENT` | 3 | 最多同时转码几个频道 |
| `-crf` | 28 | 画质参数，越小越清晰但 CPU 越高 |
| `-threads` | 1 | 编码线程数 |

## 故障排查

**导入 M3U 提示格式不正确**
检查 M3U 里的 URL 是否以 `http://` 开头，飞牛不支持 `rtsp://`。

**频道列表正常但无法播放**
确认代理容器已启动且正常运行。浏览器访问 `http://NAS_IP:18888/health` 检查。

**播放卡顿**
NAS 性能不足。尝试降低 `MAX_CONCURRENT` 到 2 或 1，或调高 `-crf`（如 30）。

**CPU/内存占用高**
正常：每个频道转码约占用 5-15% CPU（弱 NAS）和 80MB 内存。换台后 30 秒自动释放，或降低 `MAX_CONCURRENT`。
