/**
 * @file emoji_controller.h
 * @brief 表情控制模块头文件
 * 
 * 本文件定义了表情控制模块的接口，用于控制OLED屏幕上的表情显示
 */

#pragma once

#include <lvgl.h>
#include "display/display.h"
#include "board_config.h"
#include <freertos/FreeRTOS.h>
#include <freertos/timers.h>
#include <freertos/queue.h>
#include <freertos/task.h>
#include <functional>  // 用于std::function
#include <cstring>     // 用于字符串操作
#include "servo_controller.h"  // 包含舵机控制器头文件

// 动画类型枚举
enum class AnimationType {
    BLINK,      // 眨眼
    HAPPY,      // 开心
    SAD,        // 悲伤
    ANGER,      // 愤怒
    SURPRISE,   // 惊讶
    WAKEUP,     // 唤醒
    SLEEP,      // 睡眠
    LOOK_LEFT,  // 向左看
    LOOK_RIGHT, // 向右看
    HEAD_NOD,   // 点头
    HEAD_SHAKE, // 摇头
    HEAD_ROLL,  // 头部转动
    CONFUSED,   // 困惑
    AWKWARD,    // 尴尬
    CRY,        // 哭泣
    LAUGHING,   // 大笑
    FUNNY,      // 滑稿
    LOVING,     // 喜爱
    EMBARRASSED, // 尴尬
    SHOCKED,    // 震惊
    THINKING,   // 思考
    COOL,       // 酷
    RELAXED,    // 放松
    DELICIOUS,  // 美味
    KISSY,      // 亲吻
    CONFIDENT,  // 自信
    SILLY,      // 傻乎乎
    RANDOM      // 随机动画
};

// 动画消息结构体
struct AnimationMessage {
    AnimationType type;  // 动画类型
    int param;          // 动画参数（如速度）
};

/**
 * @class EmojiController
 * @brief 表情控制类，负责管理和显示各种表情
 */
class EmojiController {
public:
    /**
     * @brief 构造函数
     * @param display 显示器对象指针
     */
    EmojiController(Display* display);
    
    /**
     * @brief 析构函数
     */
    ~EmojiController();
    
    /**
     * @brief 初始化表情控制器
     */
    void Initialize();
    
    /**
     * @brief 创建表情模式屏幕
     * @return LVGL屏幕对象
     */
    lv_obj_t* CreateEmojiScreen();
    
    /**
     * @brief 清理表情模式屏幕
     */
    void CleanupEmojiScreen();
    
    /**
     * @brief 绘制表情
     * @param is_blinking 是否处于眨眼状态
     */
    void DrawEmoji(bool is_blinking);
    
    /**
     * @brief 眼睛居中
     * @param update_display 是否更新显示
     */
    void EyeCenter(bool update_display = true);
    
    /**
     * @brief 眨眼动画
     * @param speed 眨眼速度
     */
    void EyeBlink(int speed = 12);
    
    /**
     * @brief 睡眠表情
     */
    void EyeSleep();
    
    /**
     * @brief 唤醒表情
     */
    void EyeWakeup();
    
    /**
     * @brief 开心表情
     */
    void EyeHappy();
    
    /**
     * @brief 伤心表情
     */
    void EyeSad();
    
    /**
     * @brief 愤怒表情
     */
    void EyeAnger();
    
    /**
     * @brief 惊讶表情
     */
    void EyeSurprise();
    
    /**
     * @brief 眼睛向上
     */
    void EyeUp();
    
    /**
     * @brief 眼睛向下
     */
    void EyeDown();
    
    /**
     * @brief 眼睛向右
     */
    void EyeRight();
    
    /**
     * @brief 眼睛向左
     */
    void EyeLeft();
    
    /**
     * @brief 眼睛跳动
     * @param direction_x X方向
     * @param direction_y Y方向
     */
    void Saccade(int direction_x, int direction_y);
    
    /**
     * @brief 移动眼睛
     * @param direction 方向
     */
    void MoveEye(int direction);
    
    /**
     * @brief 停止所有动画
     */
    void StopAnimation();
    
    /**
     * @brief 暂停LVGL任务，减少刷新频率，避免看门狗触发
     */
    void SuspendLVGLTask();
    
    /**
     * @brief 恢复LVGL任务
     */
    void ResumeLVGLTask();
    
    /**
     * @brief 安全执行动画的辅助函数
     * @param animation_func 动画执行函数
     * @return 是否成功执行动画
     */
    bool SafeExecuteAnimation(std::function<void()> animation_func);
    
    /**
     * @brief 播放动画
     * @param type 动画类型
     * @param param 动画参数
     */
    void PlayAnimation(AnimationType type, int param = 0);
    
    /**
     * @brief 启动眨眼定时器（兼容旧接口）
     */
    void StartBlinkTimer() {
        if (blink_timer_) {
            xTimerStart(blink_timer_, 0);
        }
    }
    
    /**
     * @brief 停止眨眼定时器（兼容旧接口）
     */
    void StopBlinkTimer() {
        if (blink_timer_) {
            xTimerStop(blink_timer_, 0);
        }
        if (emoji_timer_) {
            xTimerStop(emoji_timer_, 0);
        }
        StopAnimation();
    }
    
    /**
     * @brief 设置舵机控制器
     * @param servo_controller 舵机控制器指针
     */
    void SetServoController(class ServoController* servo_controller) {
        servo_controller_ = servo_controller;
    }
    
    /**
     * @brief 获取表情屏幕
     * @return LVGL屏幕对象
     */
    lv_obj_t* GetEmojiScreen() const { return emoji_screen_; }
    
    /**
     * @brief 设置眨眼状态
     * @param blinking 是否眨眼
     */
    void SetBlinking(bool blinking) { is_blinking_ = blinking; }
    
    /**
     * @brief 获取眨眼状态
     * @return 是否眨眼
     */
    bool IsBlinking() const { return is_blinking_; }
    
    /**
     * @brief 设置随机动画是否启用
     * @param enabled 是否启用随机动画
     */
    void SetRandomAnimationEnabled(bool enabled);
    
    /**
     * @brief 清空动画队列
     */
    void ClearAnimationQueue();
    
    /**
     * @brief 测试表情表达
     */
    void TestFacialExpressions();

private:
    // 参考值
    int ref_eye_width_ = 40;
    int ref_eye_height_ = 40;
    int ref_space_between_eye_ = 10;
    int ref_corner_radius_ = 10;
    
    // 当前眼睛状态
    int left_eye_height_ = ref_eye_height_;
    int left_eye_width_ = ref_eye_width_;
    int right_eye_height_ = ref_eye_height_;
    int right_eye_width_ = ref_eye_width_;
    int left_eye_x_ = 32;  // 与boardemoji.ino中一致
    int left_eye_y_ = 32;  // 与boardemoji.ino中一致
    int right_eye_x_ = 32 + ref_eye_width_ + ref_space_between_eye_;  // 与boardemoji.ino中一致
    int right_eye_y_ = 32;  // 与boardemoji.ino中一致
    
    // 原始眼睛尺寸，用于动画结束后恢复
    int original_left_width_ = ref_eye_width_;
    int original_left_height_ = ref_eye_height_;
    int original_right_width_ = ref_eye_width_;
    int original_right_height_ = ref_eye_height_;
    
    // 表情模式相关变量
    bool is_blinking_ = false;
    TimerHandle_t blink_timer_ = nullptr;
    TimerHandle_t emoji_timer_ = nullptr;
    
    // 动画队列和任务
    QueueHandle_t animation_queue_ = nullptr;
    TaskHandle_t animation_task_handle_ = nullptr;
    TaskHandle_t animation_timer_task_handle_ = nullptr;
    
    // 随机动画控制
    bool random_animation_enabled_ = true;
    TickType_t last_random_animation_time_ = 0;
    
    // 动画执行标志，用于防止多个动画同时执行
    bool is_animating_ = false;
    
    // LVGL任务控制
    bool lvgl_task_suspended_ = false;
    UBaseType_t saved_lvgl_task_priority_ = 0;
    
    // 常量定义
    static constexpr int BLINK_INTERVAL_MS = 5000;  // 眨眼间隔时间，5秒一次
    static constexpr int BLINK_DURATION_MS = 500;   // 眨眼持续时间
    static constexpr int ANIMATION_QUEUE_SIZE = 10; // 动画队列大小
    static constexpr int RANDOM_ANIMATION_INTERVAL_MS = 10000; // 随机动画间隔时间
    
    // 舵机偏移量使用servo_controller.h中定义的宏
    // SERVO_OFFSET_X 和 SERVO_OFFSET_Y 已在servo_controller.h中定义
    
    // LVGL对象
    lv_obj_t* emoji_screen_ = nullptr;  // 表情模式专用屏幕
    lv_obj_t* left_eye_ = nullptr;
    lv_obj_t* right_eye_ = nullptr;
    
    // 显示器对象
    Display* display_ = nullptr;
    
    // 舵机控制器
    class ServoController* servo_controller_ = nullptr;
    
    // 定时器回调函数
    static void BlinkTimerCallback(TimerHandle_t timer);
    static void EmojiTimerCallback(TimerHandle_t timer);
    
    // 动画任务函数
    static void AnimationTaskFunction(void* pvParameters);
    static void AnimationTimerTask(void* pvParameters);
    
    // 执行具体动画的内部方法
    void ExecuteBlinkAnimation(int speed);
    void ExecuteHappyAnimation();
    void ExecuteSadAnimation();
    void ExecuteAngerAnimation();
    void ExecuteSurpriseAnimation();
    void ExecuteWakeupAnimation();
    void ExecuteSleepAnimation();
    void ExecuteAwkwardAnimation();
    void ExecuteCryAnimation();  // 哭泣表情
    
    // 新增表情动画方法
    void ExecuteLaughingAnimation();  // 大笑表情
    void ExecuteFunnyAnimation();     // 滑稽表情
    void ExecuteLovingAnimation();    // 喜爱表情
    void ExecuteEmbarrassedAnimation(); // 尴尬表情
    void ExecuteShockedAnimation();   // 震惊表情
    void ExecuteThinkingAnimation();  // 思考表情
    void ExecuteCoolAnimation();      // 酷表情
    void ExecuteRelaxedAnimation();   // 放松表情
    void ExecuteDeliciousAnimation(); // 美味表情
    void ExecuteKissyAnimation();     // 亲吻表情
    void ExecuteConfidentAnimation(); // 自信表情
    void ExecuteSillyAnimation();     // 傻乎乎表情
    
    // 添加缺失的函数声明
    void EyeConfused();
    void ExecuteConfusedAnimation();
    void ExecuteLookLeftAnimation();
    void ExecuteLookRightAnimation();
    void ExecuteHeadNodAnimation();
    void ExecuteHeadShakeAnimation();
    void ExecuteHeadRollAnimation();
    void InitEmoji();
    static void AnimationTask(void* pvParameters);
    AnimationType SelectRandomAnimation();
    void PlayRandomAnimation();
    void ExecuteRandomAnimation();
};
