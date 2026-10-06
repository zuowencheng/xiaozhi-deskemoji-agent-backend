# 18 · Stage A 刷机与设备联调（已完成）

> 执行时间：2026-10-02 16:00–16:55
> 结论：**✅ 刷机成功。设备已运行自编固件、启动分区为 ota_1、OTA 已指向自建后台。**

---

## 一、结果速览

| 项 | 结果 |
|---|---|
| 芯片实测 | **ESP32-S3 (QFN56) rev v0.2 / 16MB Flash / 8MB PSRAM** ✓ N16R8 |
| MAC | `84:c7:bb:58:17:44` |
| 串口 | **COM3**（`VID_303A&PID_1001` = Espressif USB-Serial/JTAG 内置 USB） |
| 新固件 | 写入 **ota_1 @0x500000**，2,759,600 B，Hash 校验通过 |
| 启动分区 | **ota_1**（设备日志 `Ota: Running partition: ota_1` 自证） |
| 固件编译时间 | `Oct 2 2026 15:40:52` / IDF `v5.5.4`（就是我编的那版） |
| OTA 指向 | `http://192.168.3.139:8003/xiaozhi/ota/`（自建后台，**不再是 api.tenclass.net**） |
| Wi-Fi | 连上 `HW-ZooKeeper`，设备拿到 IP **192.168.3.137** |
| 后台 | 8000(WS)/8003(OTA)/8004(播报) 三端口全监听，OllamaLLM+SileroVAD+SherpaASR 初始化成功 |
| 设备状态 | `activating → idle`，唤醒词 `wn9_nihaoxiaozhi_tts` 已加载，**待在 idle 等唤醒** |

---

## 二、设备真实布局（实测 dump）

### 分区表（flash 0x8000 读出，与 `partitions/v2/16m.csv` **完全一致**）

```
nvs       0x9000    0x4000
otadata   0xD000    0x2000
phy_init  0xF000    0x1000
model     0x10000   0xF0000
ota_0     0x100000  0x400000   ← 原厂固件（Jan 16 2026 / IDF 5.5.1）保留未动
ota_1     0x500000  0x400000   ← ★ 我们刷进去的新固件
assets    0x900000  0x700000
```

### otadata（flash 0xD000）

| slot | 刷前 | 刷后 |
|---|---|---|
| slot0 @0xD000 | seq=1 VALID crc OK → **ota_0** | 未改动 |
| slot1 @0xE000 | 全 FF（空） | **seq=2 VALID crc OK → ota_1** |

---

## 三、⭐ 关键技术发现（踩了一小时坑换来的）

### 1. otadata 的 CRC **只对前 4 字节**计算

最初按"28 字节"和各种 CRC32 变体反复试算都对不上，最后在 IDF 源码里找到答案：

```c
// components/bootloader_support/src/bootloader_common_loader.c
uint32_t bootloader_common_ota_select_crc(const esp_ota_select_entry_t *s)
{
    return esp_rom_crc32_le(UINT32_MAX, (uint8_t*)&s->ota_seq, 4);   // ← 只有 4 字节
}
```

等价 Python 实现（**init=0，末尾异或 0xFFFFFFFF**）：

```python
def ota_select_crc(ota_seq_le4: bytes) -> int:
    c = 0
    for b in ota_seq_le4:
        c = TAB[(c ^ b) & 0xFF] ^ (c >> 8)
    return c ^ 0xFFFFFFFF
```

用设备上真实记录反验证：`seq=1 → crc=0x4743989A` **完全吻合** ✓

### 2. 选分区公式是 `(ota_seq - 1) % app_count`，**不是** `seq % 2`

```c
// components/bootloader_support/src/bootloader_utility.c
uint32_t ota_seq = otadata[active_otadata].ota_seq - 1;
boot_index = ota_seq % bs->app_count;
```

所以 `app_count=2` 时：

| ota_seq | 启动分区 |
|---|---|
| 1 | ota_0 |
| **2** | **ota_1** |
| 3 | ota_0 |
| 4 | ota_1 |

→ 要切到 ota_1，**必须写 seq=2**（写 1 会留在 ota_0，这是个反直觉的坑）。

### 3. 两个 slot 都有效时，取 **seq 较大**的那个

```c
int bootloader_common_select_otadata(...)  →  比较 ota_seq，取 max
```

### 4. 分区表 magic 是 **2 字节 `0x50AA`**（自 IDF v5.0 起）

```c
// esp_flash_partitions.h
#define ESP_PARTITION_MAGIC 0x50AA
typedef struct { uint16_t magic; uint8_t type; uint8_t subtype;
                 esp_partition_pos_t pos;      // {uint32 offset; uint32 size;}
                 uint8_t label[16]; uint32_t flags; } esp_partition_info_t;
```

按旧格式（1 字节 magic）解析会全乱 —— 我第一版脚本就栽在这。

### 5. `ota_select_invalid` 不查 CRC，`ota_select_valid` 才查

这决定了 fallback 路径：两条记录 seq 都是 0xFFFFFFFF → 找 factory；无 factory → **ota_0**。

---

## 四、本次刷机动作（可复现）

```bash
# 0) 体检
esptool --chip esp32s3 --port COM3 --baud 460800 flash_id
esptool --chip esp32s3 --port COM3 --baud 460800 read_flash 0x8000 0x8000 head_0x8000_32k.bin

# 1) 写新固件到 ota_1（不碰 ota_0）
esptool --chip esp32s3 --port COM3 --baud 460800 write_flash 0x500000 build/xiaozhi.bin
#    → Wrote 2759600 bytes, Hash of data verified. (14.5s)

# 2) 生成并写入 otadata：seq=2 → ota_1
python tools/otahelper.py mkotadata 2 firmware/ota_patch/otadata_slot1_seq2.bin
esptool --chip esp32s3 --port COM3 --baud 460800 write_flash 0xE000 firmware/ota_patch/otadata_slot1_seq2.bin
#    → Flash will be erased from 0x0000e000 to 0x0000efff（只擦 slot1，slot0 不动）
```

生成的那条记录（32 字节）：

```
02000000 ffffffffffffffffffffffffffffffffffffffffffffffff 02000000 7437f655
└ seq=2     └ seq_label 20 字节 FF                        └ state=2(VALID)  └ crc
```

---

## 五、🔙 回滚方法（任何时候都能一键切回 ota_0）

**原理**：擦掉 slot1 那个扇区即可 —— 只剩 slot0（seq=1）有效，自动回到 ota_0。

```bash
# 回滚：擦除 otadata slot1（flash 0xE000，一个 4KB 扇区）
esptool --chip esp32s3 --port COM3 --baud 460800 erase_region 0xE000 0x1000
```

**极端情况（ota_0 也坏了）**：用整片备份还原

```bash
esptool --chip esp32s3 --port COM3 --baud 460800 write_flash 0x0 hardware/reports/backup_20261002_16MB.bin
```

**现有备份清单**：

| 文件 | 大小 | 用途 |
|---|---|---|
| `hardware/reports/backup_20261002_16MB.bin` | 16,777,216 B | 整片还原（终极保险） |
| `hardware/reports/rollback_kit/ota_0_app.bin` | 4,194,304 B | ota_0 固件单独还原 |
| `firmware/device_dump/otadata_live.bin` | 8,192 B | 刷机前 otadata 原样 |
| `firmware/device_dump/head_0x8000_32k.bin` | 32,768 B | 刷机前分区表+otadata |

---

## 六、⚠️ 发现的设计约束：设备**不会**一直连着 WebSocket

读 `main/application.cc` 的 `OnWakeWordDetected()`：

```cpp
if (device_state_ == kDeviceStateIdle) {
    if (!protocol_->IsAudioChannelOpened()) {     // ← 按需连接
        SetDeviceState(kDeviceStateConnecting);
        if (!protocol_->OpenAudioChannel()) { ... }
    }
```

**连接只由这三个触发**：① 唤醒词 ② 物理按键 ③ 状态机内部调用。
**关闭时机**：listening 再按一次、AEC 模式切换等 —— **没有"idle 超时自动断开"**。

### 对"主动播报"的影响

- ❌ 设备纯 idle 时，WS 未连接 → `POST /notify` 会返回 503（online 列表为空）——**这正是当前状态**
- ✅ 一旦被唤醒连上，**连接会保持**，此后 `/notify` 可以随时投递
- 💡 所以主动播报的可用前提是"**先唤醒一次**"；若要"随时可投递"，后续可考虑：
  - 固件层面：idle 时保持 WS，或
  - 后台上开启 MQTT 网关模式（日志里那行"未配置MQTT网关，为设备下发WebSocket配置"就是在提示这条路）

---

## 七、下一步（待用户配合）

1. **对设备说"你好小智"**（或按一下 BOOT 键）→ 设备应连上 WS
2. 看后台日志出现会话记录 + `GET /notify/status` 的 online 列表非空
3. 然后 `POST /notify` 测试**真机主动播报**：
   ```bash
   curl -X POST http://127.0.0.1:8004/notify -H "Content-Type: application/json" -d '{"text":"我是峰哥，链路已通"}'
   ```
4. 验证对话：说一句话看后台 ASR→LLM→TTS 是否跑通

---

## 八、本轮新增工具

| 工具 | 用途 |
|---|---|
| `tools/otahelper.py` | 解析分区表/otadata + 生成 ota_select 记录（CRC 算法已按源码核实） |
| `tools/read_serial.py` | 读串口日志，支持 `--reset` 复位脉冲抓启动日志 |
| `tools/parse_device_head.py` | 解析 0x8000 起的头部 dump |
| `tools/parse_app_head.py` | 解析 app 镜像 esp_app_desc / otadata |
| `tools/compare_zip_vs_repo.py` | 外部固件包与本地仓库逐文件比对（含行尾归一化） |
