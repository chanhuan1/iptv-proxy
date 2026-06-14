# 飞牛 IPTV 代理

将运营商 RTSP 直播源实时转码为 HLS 流，让飞牛影视内外网均可播放 IPTV 频道。

## 工作流程

```mermaid
flowchart LR
    A[飞牛请求] --> B{频道有效?}
    B -->|否| C[404]
    B -->|是| D{ffmpeg 运行?}
    D -->|否| E[启动转码]
    D -->|是| F[等待切片]
    E --> F
    F --> G{有数据?}
    G -->|是| H[返回 HLS]
    G -->|否| I[返回空]
    J[空闲30秒] -.-> K[自动杀进程]
    L[编辑M3U] -.-> M[热加载]
```

## 特性

- **按需转码** — 点播时才启动 ffmpeg，不看自动释放，节省 NAS 资源
- **M3U 热加载** — 编辑频道文件秒级生效，无需重启容器
- **URL 导入** — 支持飞牛 URL 导入方式，改完频道列表刷新即可
- **死源保护** — RTSP 源连接超时自动跳过，不阻塞其他频道
- **台标支持** — 支持 tvg-logo，飞牛可自动拉取频道图标
- **生产级服务** — Waitress WSGI 服务器，比 Flask 开发服务器更稳定
- **并发控制** — 最多同时转码 3 个频道（可调），CPU 内存有硬上限
- **日志输出** — 启动信息和 ffmpeg 启停事件可查看

## 背景

国内运营商 IPTV 直播源通常是 **RTSP 协议 + MPEG-2 编码**，而飞牛影视只支持 **HTTP/HLS 协议 + H.264 编码**，无法直接播放。

本代理在收到飞牛的播放请求时自动启动 ffmpeg，将 RTSP 流转码为 HLS 切片并通过 HTTP 输出。配合反向代理可实现内外网统一播放。

## 前提条件

- 一台能跑 Docker 的 NAS 或服务器
- 已安装飞牛影视
- 有可用的运营商 IPTV 直播源

## 第一步：准备频道文件

只需一个文件 `iptv_channels.m3u`，包含你的所有 IPTV 频道，直接写 RTSP 地址即可：

```
#EXTM3U
#EXTINF:-1 tvg-name="CCTV-1高清" tvg-logo="http://epg.51zmt.top:8000/tb1/CCTV/CCTV1.png" group-title="央视频道",CCTV-1高清
rtsp://123.147.xxx.xxx/xxxxxx
#EXTINF:-1 tvg-name="CCTV-2高清" tvg-logo="http://epg.51zmt.top:8000/tb1/CCTV/CCTV2.png" group-title="央视频道",CCTV-2高清
rtsp://123.147.xxx.xxx/xxxxxx
```

- `tvg-name` 和 `group-title` 控制飞牛里的显示名称和分组
- `tvg-logo` 指定台标（可选，不填也可以）
- 下一行直接写 RTSP 地址
- 代理会自动分配 `ch1`、`ch2`…… 无需手动编号

> 文件编码为 UTF-8，`channels.json` 不再需要。

## 第二步：部署

在 NAS 上创建目录，放入两个文件：

```
iptv-proxy/
├── iptv_channels.m3u
└── docker-compose.yml
```

`docker-compose.yml`：

```yaml
services:
  fntv-iptv-proxy:
    image: chanhuan01/fntv-iptv-proxy:latest
    container_name: fntv-iptv-proxy
    restart: unless-stopped
    network_mode: host
    volumes:
      - ./iptv_channels.m3u:/app/iptv_channels.m3u:ro
```

启动容器。

## 第三步：导入飞牛影视

**方式一：URL 导入（推荐）**

添加直播源 → URL 导入，填入：

```
http://NAS_IP:18888/iptv.m3u
```

改了频道列表后飞牛刷新即可。

**方式二：文件导入**

上传 `iptv_channels.m3u` 文件。

## 管理频道

直接编辑 NAS 上的 `iptv_channels.m3u`：

- **删除频道**：删掉对应的 `#EXTINF` 行和下一行 URL
- **添加频道**：新增 `#EXTINF` 行和下一行 RTSP 地址，代理自动分配新 slug
- **改名改分组**：修改 `tvg-name` 和 `group-title`

代理自动检测文件变化，无需重启。

## 查看日志

```bash
docker logs fntv-iptv-proxy
```

正常输出示例：

```
14:30:01 Starting IPTV Proxy — 189 channels in DB, 3 active in M3U
14:30:01 Listening on http://0.0.0.0:18888
14:30:01 M3U: http://NAS_IP:18888/iptv.m3u
14:30:15 Starting ffmpeg for ch1
14:32:00 Stopping ffmpeg for ch1
```

## 配置参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `IDLE_TIMEOUT` | 30 秒 | 频道空闲后关闭转码 |
| `MAX_CONCURRENT` | 3 | 最大同时转码数 |
| `-crf` | 20 | 画质（越小越清晰，CPU 越高） |
| `-threads` | 1 | 编码线程数 |

## 故障排查

**导入 M3U 提示格式不正确**
检查 URL 是否以 `http://` 或 `https://` 开头，飞牛不支持 `rtsp://`。

**频道列表正常但无法播放**
执行 `docker logs fntv-iptv-proxy` 查看是否有 ffmpeg 启动日志。无日志则说明 M3U 文件格式有问题。

**播放卡顿**
降低 `MAX_CONCURRENT` 到 2 或 1，或调高 `-crf`（如 26）。

**CPU/内存占用高**
每个频道约占用 5-15% CPU 和 80MB 内存。换台后 30 秒自动释放。
