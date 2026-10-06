# deskemoji 硬件体检报告 · 2026-10-02 03:08
串口：`COM3`

## 芯片 / 存储
- 芯片：**ESP32-S3**（revision v0.2，晶振 40MHz）
- Flash：68 4018，大小 **16MB**
- PSRAM：**8MB**（实测自 esptool Features 行）
- MAC：`84:c7:bb:58:17:44`

## 分区表
| 类型 | 子类型 | 名称 | 偏移 | 大小 |
|---|---|---|---|---|
| data | 0x2 | nvs | 0x9000 | 16.0KB |
| data | 0x0 | otadata | 0xd000 | 8.0KB |
| data | 0x1 | phy_init | 0xf000 | 4.0KB |
| data | 0x82 | model | 0x10000 | 960.0KB |
| app | 0x10 | ota_0 | 0x100000 | 4.0MB |
| app | 0x11 | ota_1 | 0x500000 | 4.0MB |
| data | 0x82 | assets | 0x900000 | 7.0MB |

## 固件身份（从备份解析 esp_app_desc）
- 版本：**2.0.1**　项目名：`xiaozhi`
- 编译时间：Jan 16 2026 16:42:50
- ESP-IDF：v5.5.1　运行分区：0x100000
