#!/usr/bin/env python3
"""Generate iptv_channels.m3u from channels.json"""
import json

BASE_URL = "http://NAS_IP:18888"  # 改成你的 NAS IP

# Channel grouping by name keyword → group-title
GROUPS = [
    ("CCTV", "央视频道"),
    ("CGTN", "央视频道"),
    ("卫视", "卫视频道"),
    ("重庆", "重庆本地"),
    ("CQTV", "重庆本地"),
]

with open("channels.json") as f:
    channels = json.load(f)

lines = ['#EXTM3U x-tvg-url="http://epg.51zmt.top:800/e.xml"']

for ch in channels:
    name = ch["name"]
    slug = f"ch{ch['id']}"
    group = "其他频道"
    for keyword, g in GROUPS:
        if keyword in name:
            group = g
            break
    lines.append(f'#EXTINF:-1 tvg-name="{name}" tvg-logo="" group-title="{group}", {name}')
    lines.append(f"{BASE_URL}/{slug}/index.m3u8")
    lines.append("")  # blank line between entries

with open("iptv_channels.m3u", "w") as f:
    f.write("\n".join(lines))

print(f"Generated {len(channels)} channels → iptv_channels.m3u")
