# -*- coding: utf-8 -*-
"""填入火山引擎语音合成凭据并切换到「湾湾小何」。

用法：
  python tools/set_volc_tts.py <appid> <access_token>              # 填凭据 + 切成火山流式TTS
  python tools/set_volc_tts.py <appid> <access_token> --speaker X  # 指定别的音色
  python tools/set_volc_tts.py <appid> <access_token> --only-fill  # 只填凭据，不改模块选择
  python tools/set_volc_tts.py --revert                            # 回滚到 EdgeTTS

凭据获取：https://console.volcengine.com/speech/app  → 创建应用 → APPID / AccessToken
音色开通：https://console.volcengine.com/speech/service/10007（湾湾小何）
"""
import sys
import re
import io

CONFIG = "server/xiaozhi-server/data/.config.yaml"
DEFAULT_SPEAKER = "zh_female_wanwanxiaohe_moon_bigtts"   # 湾湾小何 V1


def main():
    args = [a for a in sys.argv[1:]]
    speaker = DEFAULT_SPEAKER
    only_fill = "--only-fill" in args
    revert = "--revert" in args
    if "--speaker" in args:
        i = args.index("--speaker")
        if i + 1 < len(args):
            speaker = args[i + 1]
    pos = [a for a in args if not a.startswith("--") and a not in (speaker,)]

    with io.open(CONFIG, "r", encoding="utf-8") as f:
        text = f.read()

    if revert:
        text = re.sub(r"^(\s*TTS:\s*)(HuoshanDoubleStreamTTS|DoubaoTTS)\s*$",
                      r"\1EdgeTTS", text, flags=re.M)
        with io.open(CONFIG, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        print("已回滚到 EdgeTTS（微软在线合成，无需 Key）")
        return

    if len(pos) < 2:
        print(__doc__)
        sys.exit(1)
    appid, token = pos[0], pos[1]

    text = text.replace("__VOLC_APPID__", appid).replace("__VOLC_TOKEN__", token)
    text = re.sub(r"(speaker|voice):\s*\S*wanwanxiaohe\S*", rf"\1: {speaker}", text)
    if not only_fill:
        text = re.sub(r"^(\s*TTS:\s*)(EdgeTTS|HuoshanDoubleStreamTTS|DoubaoTTS)\s*$",
                      r"\1HuoshanDoubleStreamTTS", text, flags=re.M)

    with io.open(CONFIG, "w", encoding="utf-8", newline="") as f:
        f.write(text)

    print(f"OK：凭据已写入 {CONFIG}")
    print(f"    音色 speaker = {speaker}")
    print("    selected_module.TTS -> " + ("HuoshanDoubleStreamTTS" if not only_fill else "未改动"))
    print()
    print("下一步：重启服务端生效")
    print('  python tools/start_services.py --stop')
    print('  python tools/start_services.py --explorer --wait 30')


if __name__ == "__main__":
    main()
