import sys
sys.path.append("/app")

from bbl.leds import LEDController

# LED2 的第一个 LED 常亮红色。
led2 = LEDController("LED2")
led2.set_led_effect(0, 0, 255, 0b0001, 0xCB3F3F)
led2.timing_proc()
