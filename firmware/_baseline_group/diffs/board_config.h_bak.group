/**
 * @file board_config.h
 * @brief ESP32 S3 N16R8 Emoji开发板配置
 * 
 * 本文件定义了ESP32 S3 N16R8 Emoji开发板的引脚配置和其他硬件相关参数
 */

#pragma once

#include "driver/gpio.h"  // 包含ESP-IDF的GPIO头文件，用于GPIO_NUM_xx宏定义
#include "driver/ledc.h"  // 添加包含ledc相关类型定义的头文件

// 开发板名称
#ifndef BOARD_NAME
#define BOARD_NAME "ESP32-S3N16R8-EMOJI"
#endif

// 显示屏配置
#define DISPLAY_WIDTH 128
#define DISPLAY_HEIGHT 64
#define DISPLAY_I2C_SDA_PIN GPIO_NUM_41
#define DISPLAY_I2C_SCL_PIN GPIO_NUM_42
#define DISPLAY_I2C_ADDR 0x3C

// // 手势识别模块配置
// #define GESTURE_I2C_SDA_PIN GPIO_NUM_41  // 手势识别传感器SDA引脚，与显示屏共用I2C总线
// #define GESTURE_I2C_SCL_PIN GPIO_NUM_42  // 手势识别传感器SCL引脚，与显示屏共用I2C总线
// #define GESTURE_I2C_ADDR 0x73           // PAJ7620U2传感器I2C地址
// #define GESTURE_I2C_FREQ_HZ 100000      // I2C通信频率，设置为100kHz以确保稳定性

// 舵机引脚配置
#define SERVO_HORIZONTAL_PIN GPIO_NUM_11  // 水平舵机连接到GPIO11
#define SERVO_VERTICAL_PIN GPIO_NUM_12    // 垂直舵机连接到GPIO12

// 舵机角度范围配置 - 根据boardemoji.ino的设置，但限制运动范围以保护舵机
#define SERVO_CENTER_X 90    // 水平舵机中心位置(90度)
#define SERVO_CENTER_Y 90     // 垂直舵机中心位置(90度)
#define SERVO_MIN_X (SERVO_CENTER_X - 40)  // 水平舵机最小角度(左边界) - 中心位置左侧40度
#define SERVO_MAX_X (SERVO_CENTER_X + 40)  // 水平舵机最大角度(右边界) - 中心位置右侧40度
#define SERVO_MIN_Y (SERVO_CENTER_Y - 20)  // 垂直舵机最小角度(上边界) - 中心位置上方20度
#define SERVO_MAX_Y (SERVO_CENTER_Y + 20)  // 垂直舵机最大角度(下边界) - 中心位置下方20度

// 原始boardemoji.ino中的步进和延迟值
#define SERVO_STEP 1           // Arduino中的STEP值
#define SERVO_DELAY 10         // Arduino中的SERVO_DELAY值

// 舵机PWM配置，参考Arduino原始代码设置
#define SERVO_MIN_PULSEWIDTH 500        // 最小脉宽(微秒)
#define SERVO_MAX_PULSEWIDTH 2500       // 最大脉宽(微秒)
#define LEDC_TIMER_BIT_WIDTH LEDC_TIMER_14_BIT  // 14位分辨率
#define LEDC_TIMER LEDC_TIMER_0         // 使用定时器0
#define LEDC_MODE LEDC_LOW_SPEED_MODE   // 使用低速模式
#define LEDC_FREQUENCY 50               // PWM频率50Hz

// 舵机通道配置
#define SERVO_CHANNEL_COUNT 2                      // 舵机通道数量
const ledc_channel_t servo_channels[SERVO_CHANNEL_COUNT] = {
    LEDC_CHANNEL_0,  // 水平舵机使用通道0
    LEDC_CHANNEL_1   // 垂直舵机使用通道1
};

// 舵机引脚数组配置
const uint8_t servo_pins[SERVO_CHANNEL_COUNT] = {
    SERVO_HORIZONTAL_PIN,  // 水平舵机
    SERVO_VERTICAL_PIN,    // 垂直舵机
};

// 按钮配置
#define BOOT_BUTTON_PIN GPIO_NUM_0        // BOOT按钮连接到GPIO0
#define VOLUME_UP_BUTTON_PIN GPIO_NUM_40   // 音量加按钮连接到GPIO40
#define VOLUME_DOWN_BUTTON_PIN GPIO_NUM_39 // 音量减按钮连接到GPIO39

// 音频配置 - INMP441麦克风 + MAX98357A音频放大器
// INMP441麦克风引脚连接:
// - VDD: 3.3V
// - GND: GND
// - SD: GPIO6 (I2S_DATA_IN_PIN)
// - L/R: GND (接地，工作在左声道模式)
// - WS: GPIO4 (I2S_MIC_WS_PIN)
// - SCK: GPIO5 (I2S_MIC_SCK_PIN)
//
// MAX98357A音频放大器引脚连接:
// - VIN: 3.3V
// - GND: GND
// - DIN: GPIO7 (I2S_DATA_OUT_PIN)
// - BCLK: GPIO15 (I2S_SPEAKER_BCLK_PIN)
// - LRC: GPIO16 (I2S_SPEAKER_WS_PIN)
// - GAIN: GND
// - SD: 3.3V (使能引脚，高电平启用)

// 音频时钟配置
#define I2S_SPEAKER_BCLK_PIN GPIO_NUM_15  // 音频放大器时钟(MAX98357A BCLK)
#define I2S_MIC_SCK_PIN GPIO_NUM_5       // 麦克风时钟(INMP441 SCK)

// 分别定义麦克风和扬声器的WS引脚
#define I2S_MIC_WS_PIN GPIO_NUM_4        // 麦克风WS(INMP441 WS)
#define I2S_SPEAKER_WS_PIN GPIO_NUM_16   // 音频放大器WS(MAX98357A LRC)

// 数据引脚
#define I2S_DATA_OUT_PIN GPIO_NUM_7       // I2S数据输出线，连接到GPIO7（扬声器DIN）
#define I2S_DATA_IN_PIN GPIO_NUM_6        // I2S数据输入线，连接到GPIO6（麦克风SD）

// 音频采样率配置
#define AUDIO_INPUT_SAMPLE_RATE 24000
#define AUDIO_OUTPUT_SAMPLE_RATE 24000

// LED配置
#define LED_PIN GPIO_NUM_48                // 板载LED连接到GPIO48
