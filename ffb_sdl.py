# 
# This file is part of the TelemFFB distribution (https://github.com/walmis/TelemFFB).
# Copyright (c) 2023 Valmantas Palikša.
# 
# This program is free software: you can redistribute it and/or modify  
# it under the terms of the GNU General Public License as published by  
# the Free Software Foundation, version 3.
#
# This program is distributed in the hope that it will be useful, but 
# WITHOUT ANY WARRANTY; without even the implied warranty of 
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU 
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License 
# along with this program. If not, see <http://www.gnu.org/licenses/>.
#

import sdl2
from sdl2 import *
import sdl2.ext
import time
import random
import time 
from time import monotonic
import utils

import logging
import sys

from utils import LowPassFilter, RandomDirectionModulator

#SDL_Init(SDL_INIT_JOYSTICK)
SDL_Init(SDL_INIT_HAPTIC)


#js = find_haptic_joystick()
# assert(js is not None)

# # Try first joystick
# joystick = SDL_JoystickOpen(js)
# assert(joystick)
# logging.info(f"Opened: {SDL_JoystickName(joystick)}")

print(SDL_HapticName(0))

g_haptic = SDL_HapticOpen(0)



# effect = SDL_HapticEffect()
# effect.type = SDL_HAPTIC_CONSTANT;
# effect.constant.level = int(32767*0.1)
# effect.constant.direction.type = SDL_HAPTIC_POLAR; #// Using cartesian direction encoding.
# effect.constant.direction.dir[0] = 12000; #// X position

# effect_const = effect
# fconstant_id = SDL_HapticNewEffect( haptic, effect );

# SDL_HapticRunEffect( haptic, fconstant_id, 1 );

# effect = SDL_HapticEffect()
# effect.type = SDL_HAPTIC_SINE;
# effect.periodic.direction.type = SDL_HAPTIC_POLAR; # Polar coordinates
# effect.periodic.direction.dir[0] = 18000; # Force comes from south
# effect.periodic.period = 1000; # 1000 ms
# effect.periodic.magnitude = int(32767*0.5); # 20000/32767 strength
# effect.periodic.length = 5000; # 5 seconds long
# effect.periodic.attack_length = 0; #milliseconds
# effect.periodic.fade_length = 0; #milliseconds

# effect_id = SDL_HapticNewEffect( haptic, effect );

# print("Created effect", effect_id)
# SDL_HapticRunEffect( haptic, effect_id, 1 );


class HapticEffectSDL:
    id : int = None
    effect : SDL_HapticEffect= None
    started : bool = False
    modulator = None

    def _create_effect(self):
        self.id = SDL_HapticNewEffect( g_haptic, self.effect )
        if self.id < 0:
            logging.error(f"{SDL_GetError()}")
            self.id = None
        else:
            logging.info(f"Created effect {self.id}")

    def _update_effect(self):
        if self.status < 0: 
            self.id = None

        if self.id is None:
            self._create_effect()
        else:
            if SDL_HapticUpdateEffect(g_haptic, self.id, self.effect) < 0:
                logging.info(f"Effect {self.id} lost, restarting")
                self._create_effect()

    def periodic(self, frequency, magnitude:float, direction_deg:float, type=SDL_HAPTIC_SINE):
        if self.effect is None:
            self.effect = SDL_HapticEffect()

        if frequency == 0:
            return self

        self.effect.type = type;
        self.effect.periodic.direction.type = SDL_HAPTIC_POLAR # Polar coordinates
        self.effect.periodic.direction.dir[0] = int(direction_deg*100) # 18000; # Force comes from south
        self.effect.periodic.period = int(1000/frequency) # 1000 ms
        self.effect.periodic.magnitude = int(32767*magnitude) # x/32767 strength
        self.effect.periodic.length = 0 # milliseconds
        self.effect.periodic.attack_length = 0 #milliseconds
        self.effect.periodic.fade_length = 0 #milliseconds
        self._update_effect()
        return self

    def constant(self, magnitude:float, direction:float, *args, **kwargs):
        """Create and manage CF FFB effect

        :param magnitude: Effect strength from 0.0 .. 1.0
        :type magnitude: float
        :param direction_deg: Angle in degrees
        :type direction_deg: float
        """
        if self.effect is None:
            self.effect = SDL_HapticEffect()

        if type(direction) == type and issubclass(direction, utils.DirectionModulator):
            if not self.modulator:
                self.modulator = direction(*args, **kwargs)
            direction = self.modulator.update()      

        self.effect.type = SDL_HAPTIC_CONSTANT
        self.effect.constant.direction.type = SDL_HAPTIC_POLAR # Polar coordinates
        self.effect.constant.direction.dir[0] = int(direction*100) # 18000; # Force comes from south
        self.effect.constant.level = int(32767*magnitude) # 20000/32767 strength
        self.effect.constant.length = 0 # x seconds long
        self.effect.constant.attack_length = 0 #milliseconds
        self.effect.constant.fade_length = 0 #milliseconds
        self._update_effect()
        return self

    @property
    def status(self) -> int:
        if self.id is None: return -1
        return SDL_HapticGetEffectStatus(g_haptic, self.id)

    def start(self):
        if self.id is not None and not self.started:
            logging.info(f"Start effect {self.id}")
            if SDL_HapticRunEffect( g_haptic, self.id, 1 ) < 0:
                logging.error(f"Failed to start effect {self.id}")
            self.started = True
    
    def stop(self):
        if self.id is not None and self.started:
            logging.info(f"Stop effect {self.id}")
            SDL_HapticStopEffect( g_haptic, self.id)  
            self.started = False

    def __del__(self):
        if self.id is not None:
            logging.info(f"Destroying effect {self.id}")
            SDL_HapticDestroyEffect( g_haptic, self.id) 
            self.id = None
            self.started = False


if __name__ == "__main__":
    m = RandomDirectionModulator()
    h = HapticEffectSDL()

    while True:
        m.update()
        print(m.value)

        h.constant(0.1, m.value).start()

        time.sleep(0.1)

# h = HapticEffect()
# h.periodic(10, 0.1, 180).start()

# h2 = HapticEffect()
# h2.periodic(12, 0.1, 90).start()


#while True:
    #SDL_PumpEvents()
#     print ("X",SDL_JoystickGetAxis(joystick,0), "Y",SDL_JoystickGetAxis(joystick,1))

#     #check status, update effect if all good
#     status = SDL_HapticGetEffectStatus(haptic, fconstant_id)
#     if status >= 0:
#         #ret = status
#         #increase dir angle on every iteration
#         effect_const.constant.direction.dir[0] += 10
#         effect_const.constant.direction.dir[0] %= 36000
#         ret = SDL_HapticUpdateEffect(haptic, fconstant_id, effect_const)
#     else:
#         #effect lost, todo: reinitialize effect
#         print("status", status)

    #time.sleep(0.01)