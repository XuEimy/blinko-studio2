import sys
sys.path.append('/app')
import time
from bbl.servos import ServosController
C={"angleChannel":1,"speed2Channel":2,"speed3Channel":3,"actions":[{"servo":1,"angle":90,"durationMs":0},{"servo":2,"speed":50,"durationMs":1000}]}
S=ServosController()
for a in C['actions']:
    if a['servo']==1:
        ch=C['angleChannel']
        S.set_angle(ch,a['angle'])
    else:
        ch=C['speed2Channel'] if a['servo']==2 else C['speed3Channel']
        S.set_speed(ch,a['speed'])
    if a['durationMs']>0:
        end=time.ticks_add(time.ticks_ms(),a['durationMs'])
        while time.ticks_diff(end,time.ticks_ms())>0:
            time.sleep(0.02)
    if a['servo']!=1:
        S.set_speed(ch,0)
while True:
    time.sleep(1)
