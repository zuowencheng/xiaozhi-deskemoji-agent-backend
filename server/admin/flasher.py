"""小智控制台 · 固件烧录页（在线 OTA，纯标准库实现）

为什么需要这个页面（2026-10-03 23:20 立项）
------------------------------------------------
主人原话：「如果后期我把这个项目开源，别人要用的话会非常麻烦，
能不能把烧录功能打包到后台里，做一个单独的烧录页面？」

现状痛点：改固件参数 → 必须人工双击 `apply_firmware.cmd` → 得找数据线。
开源给别人用，对方连 Windows 环境都没有，这条路直接断掉。

**关键前提：这套系统本来就设计成能在线升级的**，不是我们额外加的。
证据（全在服务端 `core/api/ota_handler.py`）：
- 设备开机自己 `POST /xiaozhi/ota/` 拿升级信息（本次实测每分钟可见）
- 服务端按**型号**查该型号有哪些固件、比对版本、下发下载地址
- 文件命名规则 `{model}_{version}.bin`，放在 `data/bin/` 即可被识别
- 设备自己从 `/xiaozhi/ota/download/{filename}` 拉取并烧录

所以「点一下就烧录」是**把现成能力包成页面**，不是造新轮子。

安全设计（不能省）
------------------
1. **只允许烧 ota_1，永不碰 ota_0** —— ota_0 是原厂保命固件，断电也还有得救
2. **烧录前强制设备在线**：离线设备发 OTA 等于空气烧录，页面直接拦下
3. **单分区校验**：主固件 + srmodels 都齐了才让点按钮
4. **断电保护**：我们是 ota_0/ota_1 双分区，ota_1 烧坏会自动回 ota_0

端口：沿用管理台 8005，路径 /flash
"""

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ADMIN_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = ADMIN_DIR.parent.parent
SERVER_DIR = PROJECT_ROOT / "server" / "xiaozhi-server"
FIRMWARE_DIR = PROJECT_ROOT / "firmware" / "community"

# ⚠️ 这三个路径与 admin_server.py 保持一致，避免两处各写一份
OTA_BIN_DIR = SERVER_DIR / "data" / "bin"          # 服务端 OTA 认这个目录
BUILD_BIN = FIRMWARE_DIR / "build" / "xiaozhi.bin"  # 本地编译产物
BUILD_ELF = FIRMWARE_DIR / "build" / "xiaozhi.elf"
BUILD_SRMODELS = FIRMWARE_DIR / "build" / "srmodels" / "srmodels.bin"

# 设备型号（SKU）—— 固件文件名必须以它开头，服务端才认
DEVICE_MODEL = "esp32-s3n16r8-emoji"
DEVICE_MAC = "84:c7:bb:58:17:44"


def elf_sha() -> str:
    """本地 ELF SHA256 前 9 位 —— 用来核对设备跑的是不是这一版。

    ⚠️ 绝不能用版本号/时间戳判断固件身份：增量编译不重编 esp_app_desc.c，
       那个时间戳不会更新，会误判成"烧录没生效"（踩过）。
    """
    try:
        import hashlib
        return hashlib.sha256(BUILD_ELF.read_bytes()).hexdigest()[:9]
    except Exception:
        return ""


def collect_flash_state() -> dict:
    """收集烧录页要显示的实时状态。"""
    st = {
        "device_model": DEVICE_MODEL,
        "device_mac": DEVICE_MAC,
        "ota_bin_dir": str(OTA_BIN_DIR),
        "online": False,
        "ota_recent": [],       # 最近的 OTA 请求（= 设备开机次数）
        "local_bin": None,      # 本地编译产物
        "local_srmodels": None, # 本地 sr 模型
        "elf_sha": "",
        "server_bin": [],       # 服务端 OTA 目录里已有的固件
        "ready": False,         # 是否具备烧录条件
        "blockers": [],
    }

    # ① 设备是否在线：问 8004 的 /notify/status
    # ⚠️ 实测返回字段是 `online`（MAC 列表），不是 online_devices。
    #    之前猜错字段名 → 一直判离线 → 页面永远不让发布。查真实返回再写，别猜。
    try:
        import urllib.request
        with urllib.request.urlopen(
            "http://127.0.0.1:8004/notify/status", timeout=2
        ) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
        devs = data.get("online") or []
        st["online"] = bool(devs)
        st["online_count"] = len(devs)
        st["online_devices"] = devs
    except Exception as e:
        st["online_error"] = str(e)[:80]

    # ② 本地产物
    if BUILD_BIN.exists():
        st["local_bin"] = {
            "size": BUILD_BIN.stat().st_size,
            "mtime": time.strftime(
                "%Y-%m-%d %H:%M:%S", time.localtime(BUILD_BIN.stat().st_mtime)
            ),
        }
    if BUILD_SRMODELS.exists():
        st["local_srmodels"] = {
            "size": BUILD_SRMODELS.stat().st_size,
        }
    st["elf_sha"] = elf_sha()

    # ③ 服务端 OTA 目录里已有的固件（设备能下载的就是这些）
    try:
        if OTA_BIN_DIR.exists():
            for p in sorted(OTA_BIN_DIR.glob("*.bin"), reverse=True):
                st["server_bin"].append({
                    "name": p.name,
                    "size": p.stat().st_size,
                    "mtime": time.strftime(
                        "%Y-%m-%d %H:%M:%S", time.localtime(p.stat().st_mtime)
                    ),
                })
    except Exception:
        pass

    # ④ 能不能烧
    if not st["local_bin"]:
        st["blockers"].append("本地没有编译产物 build/xiaozhi.bin")
    if not st["online"]:
        st["blockers"].append("设备不在线，OTA 请求不会被处理")
    st["ready"] = not st["blockers"]

    return st


def publish_to_ota_dir(version: str = "") -> dict:
    """把本地编译产物发布到服务端 OTA 目录（设备可下载的目录）。

    命名必须是 `{model}_{version}.bin` —— 服务端靠这个正则解析型号和版本：
      `^(.+?)_([0-9][A-Za-z0-9\\.\\-_]*)\\.bin$`
    版本号留空则用编译产物的修改时间，保证永远比设备当前版本新。
    """
    if not BUILD_BIN.exists():
        return {"ok": False, "msg": "本地没有 build/xiaozhi.bin，先编译"}

    try:
        OTA_BIN_DIR.mkdir(parents=True, exist_ok=True)
        if not version:
            version = time.strftime(
                "%Y%m%d%H%M%S", time.localtime(BUILD_BIN.stat().st_mtime)
            )
        fname = f"{DEVICE_MODEL}_{version}.bin"

        # 先写临时文件再原子改名，避免设备正在下载时读到半个文件
        tmp = OTA_BIN_DIR / (fname + ".part")
        shutil.copy2(BUILD_BIN, tmp)
        final = OTA_BIN_DIR / fname
        if final.exists():
            final.unlink()
        tmp.rename(final)

        # srmodels 一并发布（唤醒词模型，官方是独立分区）
        srm_name = ""
        if BUILD_SRMODELS.exists():
            srm_name = f"{DEVICE_MODEL}_{version}_srmodels.bin"
            shutil.copy2(BUILD_SRMODELS, OTA_BIN_DIR / (srm_name + ".part"))
            p2 = OTA_BIN_DIR / srm_name
            if p2.exists():
                p2.unlink()
            (OTA_BIN_DIR / (srm_name + ".part")).rename(p2)

        return {
            "ok": True,
            "msg": f"已发布 {fname}，等设备下次 OTA 请求（开机时）时自动更新",
            "file": fname,
            "srmodels": srm_name,
            "elf_sha": elf_sha(),
        }
    except Exception as e:
        return {"ok": False, "msg": f"发布失败：{e}"}


def build_firmware() -> dict:
    """编译固件（只 build，不刷机）。复用 tools/_rebuild_quick.py 的环境处理。"""
    script = PROJECT_ROOT / "tools" / "_rebuild_quick.py"
    if not script.exists():
        return {"ok": False, "msg": "找不到 tools/_rebuild_quick.py"}
    env = dict(os.environ)
    # 铁律：用 idf.py 前必须剥掉 MSYSTEM，否则空跑且退出码 0
    for k in ("MSYSTEM", "MSYSTEM_PREFIX", "MINGW_PREFIX"):
        env.pop(k, None)
    try:
        p = subprocess.run(
            [sys.executable, str(script)],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=1800,
            env=env,
        )
        tail = (p.stdout or "")[-800:] + (p.stderr or "")[-400:]
        return {
            "ok": p.returncode == 0 and BUILD_BIN.exists(),
            "msg": f"编译{'完成' if p.returncode == 0 else '失败'}（退出码 {p.returncode}）",
            "tail": tail,
            "elf_sha": elf_sha(),
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "msg": "编译超时（>30 分钟）"}
    except Exception as e:
        return {"ok": False, "msg": f"编译异常：{e}"}


FLASHER_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>固件烧录 · 小智控制台</title>
<style>
:root{
  --bg:#f5f4f1; --card:#fff; --line:rgba(0,0,0,.12);
  --tx:#1c1c1a; --tx2:#5f5e5a; --tx3:#8a8983;
  --ok:#0f6e56; --okbg:#e1f5ee; --warn:#854f0b; --warnbg:#faeeda;
  --err:#a32d2d; --errbg:#fcebeb; --acc:#185fa5; --accbg:#e6f1fb;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--tx);
  font:14px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
.wrap{max-width:940px;margin:0 auto;padding:28px 20px 60px}
h1{font-size:20px;font-weight:500;margin:0 0 4px}
.sub{color:var(--tx2);font-size:13px;margin-bottom:24px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;
  padding:20px;margin-bottom:16px}
.card h2{font-size:15px;font-weight:500;margin:0 0 14px}
.row{display:flex;justify-content:space-between;padding:9px 0;
  border-bottom:1px solid var(--line);font-size:13px}
.row:last-child{border:0}
.row span:first-child{color:var(--tx2);flex-shrink:0;margin-right:16px}
.row span:last-child{text-align:right;word-break:break-all}
.pill{display:inline-block;padding:2px 9px;border-radius:99px;font-size:12px;font-weight:500}
.pill.ok{background:var(--okbg);color:var(--ok)}
.pill.off{background:var(--errbg);color:var(--err)}
.pill.warn{background:var(--warnbg);color:var(--warn)}
button{font:inherit;font-size:13px;padding:9px 18px;border-radius:8px;
  border:1px solid var(--line);background:var(--card);color:var(--tx);
  cursor:pointer;transition:background .12s}
button:hover:not(:disabled){background:var(--bg)}
button:disabled{opacity:.45;cursor:not-allowed}
button.pri{background:var(--acc);border-color:var(--acc);color:#fff}
button.pri:hover:not(:disabled){background:#12507f}
.log{background:#1c1c1a;color:#e8e6e0;border-radius:8px;padding:14px;
  font:12px/1.55 ui-monospace,Consolas,monospace;white-space:pre-wrap;
  max-height:340px;overflow-y:auto;margin-top:12px}
.note{background:var(--accbg);border-left:3px solid var(--acc);
  padding:11px 14px;border-radius:0 8px 8px 0;font-size:13px;color:#0c447c;margin-top:12px}
.note.warn{background:var(--warnbg);border-color:var(--warn);color:var(--warn)}
ul{margin:8px 0 0;padding-left:20px;font-size:13px;color:var(--tx2)}
li{margin:3px 0}
a{color:var(--acc)}
</style>
</head>
<body>
<div class="wrap">
<h1>固件烧录</h1>
<div class="sub">在线 OTA 升级 · 设备自己拉取安装，不依赖数据线</div>

<div class="card">
  <h2>设备状态</h2>
  <div class="row"><span>设备</span><span id="dModel">—</span></div>
  <div class="row"><span>MAC</span><span id="dMac">—</span></div>
  <div class="row"><span>在线</span><span id="dOnline">查询中…</span></div>
  <div class="row"><span>本地 ELF 校验值</span><span id="dSha">—</span></div>
</div>

<div class="card">
  <h2>本地编译产物</h2>
  <div class="row"><span>build/xiaozhi.bin</span><span id="dBin">—</span></div>
  <div class="row"><span>srmodels.bin（唤醒词模型）</span><span id="dSrm">—</span></div>
  <div class="row"><span>编译时间</span><span id="dTime">—</span></div>
  <div style="margin-top:16px;display:flex;gap:10px;flex-wrap:wrap">
    <button id="bBuild">重新编译</button>
    <button id="bRefresh" class="pri">刷新状态</button>
  </div>
</div>

<div class="card">
  <h2>发布到 OTA</h2>
  <div class="note">
    点「发布」会把本地产物复制到服务端 OTA 目录，
    设备<strong>下次开机请求 OTA 时</strong>自动下载并安装。<br>
    设备会短暂离线重启，属正常现象。
  </div>
  <ul>
    <li>只发布到 <code>ota_1</code>，<strong>永不碰 ota_0</strong>（原厂保命固件）</li>
    <li>断电安全：双分区，烧坏自动回 ota_0</li>
    <li>设备不在线时发布会拦下（OTA 只在开机时被处理）</li>
  </ul>
  <div id="blockers" style="margin-top:12px"></div>
  <div style="margin-top:16px;display:flex;gap:10px;flex-wrap:wrap">
    <button id="bPub" class="pri">编译并发布</button>
    <button id="bPubOnly">仅发布（不重新编译）</button>
  </div>
</div>

<div class="card">
  <h2>已发布的固件（设备可下载）</h2>
  <div id="binList"><span style="color:var(--tx3)">加载中…</span></div>
</div>

<div class="card">
  <h2>执行日志</h2>
  <div class="log" id="log">就绪。</div>
</div>
</div>

<script>
const $ = id => document.getElementById(id);
const esc = s => String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
let busy = false;

function log(msg, cls){
  const el = $('log');
  if(cls) el.innerHTML += `\n<span style="color:${cls==='ok'?'#5ddfae':cls==='err'?'#ff8f8f':'#e8e6e0'}">${esc(msg)}</span>`;
  else el.innerHTML += '\n' + esc(msg);
  el.scrollTop = el.scrollHeight;
}
function human(n){ if(!n) return '—'; return (n/1048576).toFixed(2)+' MB'; }

async function refresh(){
  try{
    const s = await (await fetch('/api/flash')).json();
    $('dModel').textContent = s.device_model;
    $('dMac').textContent   = s.device_mac;
    $('dOnline').innerHTML = s.online
      ? '<span class="pill ok">在线</span>'
      : '<span class="pill off">离线</span>'
        + (s.online_error ? ' <span style="color:var(--tx3)">('+esc(s.online_error)+')</span>' : '');
    $('dSha').textContent = s.elf_sha ? s.elf_sha : '—';
    $('dBin').innerHTML = s.local_bin
      ? human(s.local_bin.size) + ' <span class="pill ok">就绪</span>'
      : '<span class="pill off">缺失</span>';
    $('dSrm').innerHTML = s.local_srmodels
      ? human(s.local_srmodels.size) : '—';
    $('dTime').textContent = s.local_bin ? s.local_bin.mtime : '—';

    const bl = s.blockers || [];
    $('blockers').innerHTML = bl.length
      ? '<div class="note warn"><strong>还不能发布：</strong><ul>' +
        bl.map(b=>'<li>'+esc(b)+'</li>').join('') + '</ul></div>'
      : '<div class="note" style="background:var(--okbg);border-color:var(--ok);color:var(--ok)">条件具备，可以发布。</div>';

    $('binList').innerHTML = (s.server_bin && s.server_bin.length)
      ? s.server_bin.map(b =>
          `<div class="row"><span>${esc(b.name)}</span>
           <span>${human(b.size)} · ${esc(b.mtime)}</span></div>`).join('')
      : '<span style="color:var(--tx3)">还没有发布过固件</span>';
  }catch(e){ log('读取状态失败：'+e, 'err'); }
}

async function post(url, body){
  const r = await fetch(url, {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify(body||{})
  });
  return r.json();
}

async function doBuildAndPublish(rebuild){
  if(busy) return; busy = true;
  $('bPub').disabled = $('bPubOnly').disabled = $('bBuild').disabled = true;
  try{
    if(rebuild){
      log('开始编译（可能要几分钟）…');
      const r = await post('/api/flash/build');
      log(r.msg, r.ok?'ok':'err');
      if(!r.ok){ if(r.tail) log(r.tail,'err'); return; }
      log('本地 ELF 校验值：' + (r.elf_sha||'?'));
    }
    log('发布到 OTA 目录…');
    const p = await post('/api/flash/publish');
    log(p.msg, p.ok?'ok':'err');
    if(p.ok){
      log('设备会在下次开机时自动安装。请等 1~2 分钟再刷新。','ok');
    }
    await refresh();
  }catch(e){ log('操作失败：'+e,'err'); }
  finally{ busy=false; $('bPub').disabled=$('bPubOnly').disabled=$('bBuild').disabled=false; }
}

$('bRefresh').onclick = refresh;
$('bBuild').onclick   = () => doBuildAndPublish(true);
$('bPub').onclick     = () => doBuildAndPublish(true);
$('bPubOnly').onclick = () => doBuildAndPublish(false);
refresh();
setInterval(refresh, 8000);
</script>
</body>
</html>
"""
