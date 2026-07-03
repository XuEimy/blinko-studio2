import sys
sys.path.append('/app')
import time
import random
from bbl.servos import ServosController

C={"speed1Channel":1,"speed2Channel":2,"speed3Channel":3,"actions":[{"servo":3,"speed":50,"durationMs":1600}]}

S=ServosController()
CHANNELS={1:C['speed1Channel'],2:C['speed2Channel'],3:C['speed3Channel']}

def random_duration_ms():
    return 1125 + random.randint(0, 7) * 125

for a in C['actions']:
    ch=CHANNELS[a['servo']]
    S.set_speed(ch,a['speed'])
    d=random_duration_ms()
    end=time.ticks_add(time.ticks_ms(),d)
    while time.ticks_diff(end,time.ticks_ms())>0:
        time.sleep(0.02)
    S.set_speed(ch,0)

while True:
    time.sleep(1)