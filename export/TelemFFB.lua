local JSON = loadfile("Scripts\\JSON.lua")()
package.path = package.path .. ";.\\LuaSocket\\?.lua;Scripts\\?.lua;"
package.cpath = package.cpath .. ";.\\LuaSocket\\?.dll"
local socket = require("socket")

require("Vector")

local function scale(x, in_min, in_max, out_min, out_max)
    return (x - in_min) * (out_max - out_min) / (in_max - in_min) + out_min
end

-- Return if socket has data to read
local function sock_readable(s)
  local ret = socket.select({s}, {}, 0)
  if ret[s] then
    return true
  end
  return false
end


-- Function to calculate air density based on altitude
function calculateAirDensity(altitude)
  -- Constants for the barometric formula
  local seaLevelPressure = 1013.25 -- Sea level pressure in hPa
  local temperatureLapseRate = 0.0065 -- Temperature lapse rate in K/m
  local gravitationalConstant = 9.80665 -- Gravitational constant in m/s^2
  local molarGasConstant = 8.31446261815324 -- Molar gas constant in J/(mol K)
  local molarMassOfDryAir = 0.0289644 -- Molar mass of dry air in kg/mol

  -- Calculate temperature at the given altitude
  local temperature = 288.15 - temperatureLapseRate * altitude

  -- Calculate pressure at the given altitude
  local pressure = seaLevelPressure * math.pow(1 - (temperatureLapseRate * altitude) / 288.15, 
    gravitationalConstant * molarMassOfDryAir / (temperatureLapseRate * molarGasConstant))

  -- Calculate air density based on the ideal gas law
  local airDensity = pressure * molarMassOfDryAir / (temperature * molarGasConstant)

  return airDensity*100
end

-- The ExportScript.Tools.dump function show the content of the specified variable.
-- ExportScript.Tools.dump is similar to PHP function dump and show variables from type
-- "nil, "number", "string", "boolean, "table", "function", "thread" and "userdata"
local function dump(var, depth)
  depth = depth or 0
  if type(var) == "string" then
      return 'string: "' .. var .. '"\n'
  elseif type(var) == "nil" then
      return 'nil\n'
  elseif type(var) == "number" then
      return 'number: "' .. var .. '"\n'
  elseif type(var) == "boolean" then
      return 'boolean: "' .. tostring(var) .. '"\n'
  elseif type(var) == "function" then
      if debug and debug.getinfo then
          fcnname = tostring(var)
          local info = debug.getinfo(var, "S")
          if info.what == "C" then
              return string.format('%q', fcnname .. ', C function') .. '\n'
          else
              if (string.sub(info.source, 1, 2) == [[./]]) then
                  return string.format('%q', fcnname .. ', defined in (' .. info.linedefined .. '-' .. info.lastlinedefined .. ')' .. info.source) ..'\n'
              else
                  return string.format('%q', fcnname .. ', defined in (' .. info.linedefined .. '-' .. info.lastlinedefined .. ')') ..'\n'
              end
          end
      else
          return 'a function\n'
      end
  elseif type(var) == "thread" then
      return 'thread\n'
  elseif type(var) == "userdata" then
      return tostring(var)..'\n'
  elseif type(var) == "table" then
          depth = depth + 1
          out = "{\n"
          for k,v in pairs(var) do
                  out = out .. (" "):rep(depth*4).. "["..k.."] :: " .. dump(v, depth)
          end
          return out .. (" "):rep((depth-1)*4) .. "}\n"
  else
          return tostring(var) .. "\n"
  end
end

local f_telemFFB = {
  Start = function(self)

    self.recv_data = ""
    self.prev_command_t = socket.gettime()

    self.host = "127.0.0.1"
    self.port = 34380

    self.sock_udp = socket.try(socket.udp())
    self.sock_rcv = socket.try(socket.udp())
    socket.try(self.sock_rcv:setsockname("127.0.0.1", 34381))
    socket.try(self.sock_rcv:settimeout(.001))

    socket.try(self.sock_udp:settimeout(.001))
    --socket.try(self.sock_udp:setoption('broadcast', true))
    --socket.try(self.sock_udp:setpeername("127.0.0.1", 34380))

    socket.try(self.sock_udp:sendto("CONNECT", self.host, self.port))

  end,
  BeforeNextFrame = function(self)
    --LoSetCommand(2001, 0.25)
    if self.sock_udp then
      while sock_readable(self.sock_udp)
      do
        local data, addr = self.sock_udp:receivefrom()
        self.prev_command_t = socket.gettime()
        if data == nil then
          data = ""
        else
          self.recv_data = data
        end
      end

      -- if no commands are received in 100ms, do not repeat last commands
      if socket.gettime() - self.prev_command_t > 0.1 then
        self.recv_data = ""
      end

      if self.recv_data ~= "" then
        local f = loadstring(self.recv_data)
        if f then
          f()
        end
      end
  

    end
    
  end,
  AfterNextFrame = function(self)
    local data_send =
      socket.protect(
      function()
        if self.sock_udp then
          local stringToSend = ""

          local t = LoGetModelTime()
          local altAsl = LoGetAltitudeAboveSeaLevel()
          local altAgl = LoGetAltitudeAboveGroundLevel()
          local pitch, bank, yaw = LoGetADIPitchBankYaw()
          local aoa = LoGetAngleOfAttack()
          local acceleration = LoGetAccelerationUnits()
          local AccelerationUnits = "0.00~0.00~0.00"
          local IAS = LoGetIndicatedAirSpeed() -- m/s
          local M_number = LoGetMachNumber()
          local AirPressure = LoGetBasicAtmospherePressure() -- * 13.60 -- mmHg to kg/m2
          local f14SpeedbrakePos = LoGetAircraftDrawArgumentValue(400)
          local LeftGear = LoGetAircraftDrawArgumentValue(6)
          local NoseGear = LoGetAircraftDrawArgumentValue(1)
          local RightGear = LoGetAircraftDrawArgumentValue(4)
          local drawGearPos = LoGetAircraftDrawArgumentValue(3)
          local drawFlapsPos1 = LoGetAircraftDrawArgumentValue(9)
          local drawFlapsPos2 = LoGetAircraftDrawArgumentValue(10)
          local drawSpeedBrake = LoGetAircraftDrawArgumentValue(21)
          local drawRefuelBoom = LoGetAircraftDrawArgumentValue(22)

          local AB = string.format("%.2f~%.2f", LoGetAircraftDrawArgumentValue(28), LoGetAircraftDrawArgumentValue(29))

          local WoW = string.format("%.2f~%.2f~%.2f", LeftGear, NoseGear, RightGear)

          local mech = LoGetMechInfo()

          if acceleration then
            AccelerationUnits = string.format("%.2f~%.2f~%.2f", acceleration.x, acceleration.y, acceleration.z)
          end

          local obj = LoGetSelfData()
          local myselfData

          if obj then
            myselfData = string.format("%.2f~%.2f~%.2f", math.deg(obj.Heading), math.deg(obj.Pitch), math.deg(obj.Bank))
          end

          local vectorVel = LoGetVectorVelocity()
          if type(vectorVel) == "function" then
            do
              return
            end
          end

          local wind = LoGetVectorWindVelocity()
          local wind_vec = Vector(wind.x, wind.y, wind.z)

          local velocityVectors = string.format("%.2f~%.2f~%.2f", vectorVel.x, vectorVel.y, vectorVel.z)

          local incidence_vec = Vector(vectorVel.x, vectorVel.y, vectorVel.z)
          incidence_vec = incidence_vec - wind_vec
          incidence_vec = incidence_vec:rotY(-(2.0 * math.pi - obj.Heading))
          incidence_vec = incidence_vec:rotZ(-obj.Pitch)
          incidence_vec = incidence_vec:rotX(-obj.Bank)
          local incidence = string.format("%.3f~%.3f~%.3f", incidence_vec.x, incidence_vec.y, incidence_vec.z)

          -- calculate relative wind in body frame
          local rel_wind = Vector(wind.x, wind.y, wind.z)
          rel_wind = wind_vec
          rel_wind = rel_wind:rotY(-(2.0 * math.pi - obj.Heading))
          rel_wind = rel_wind:rotZ(-obj.Pitch)
          rel_wind = rel_wind:rotX(-obj.Bank)
          rel_wind = string.format("%.3f~%.3f~%.3f", rel_wind.x, rel_wind.y, rel_wind.z)

          local tas = LoGetTrueAirSpeed() --ms^2
          local calc_alpha = 0
          local calc_beta = 0
          if tas > 0 then
            calc_alpha = math.deg(math.atan(-incidence_vec.y / incidence_vec.x))
            calc_beta = math.deg(math.atan(incidence_vec.z / math.sqrt(incidence_vec.y^2 + incidence_vec.x^2)))
          end


          local windVelocityVectors =
            string.format(
            "%.2f~%.2f~%.2f",
            wind.x,
            wind.y,
            wind.z
          )
          local CM = LoGetSnares()
          local MainPanel = GetDevice(0)

          local AirDensity = calculateAirDensity(altAsl)
          local DynamicPressure = 0.5 * AirDensity * tas^2 -- kg/ms^2

          if MainPanel ~= nil then
            MainPanel:update_arguments()
          end

          local engine = LoGetEngineInfo()
          local engineRPM = string.format("%.3f~%.3f", engine.RPM.left, engine.RPM.right)

          local CannonShells = LoGetPayloadInfo().Cannon.shells
          local stations = LoGetPayloadInfo().Stations
          local PayloadInfo = "empty"
          local temparray = {}

          for i_st, st in pairs(stations) do
            local name = LoGetNameByType(st.weapon.level1,st.weapon.level2,st.weapon.level3,st.weapon.level4);
            temparray[#temparray + 1] =
              string.format(
              "%s-%d.%d.%d.%d*%d",
              name,
              st.weapon.level1,
              st.weapon.level2,
              st.weapon.level3,
              st.weapon.level4,
              st.count
              )
            PayloadInfo = table.concat(temparray, "~")
          end
          -------------------------------------------------------------------------------------------------------------------------------------------------------
          if obj.Name == "Mi-8MT" then
            -------------------------------------------------------------------------------------------------------------------------------------------------------
            -- Mi-8 Relevent Info (from mainpanel_init.lua):
            -- RotorRPM.input				= {0.0, 110.0}
            -- RotorRPM.output				= {0.0, 1.0}
            -- Mi-8 raw data is in percentage of gauge
            -- Per internet sources, max rotor RPM on the Mi-8 is aprox 220.  Cruise at 192rpm
            -- Multiply received value by 220 to get (approximate) actual RPM

            local mainRotorRPM = MainPanel:get_argument_value(42) * 220
            local IAS_L = MainPanel:get_argument_value(24)

            local PanelShake =
              string.format(
              "%.2f~%.2f~%.2f",
              MainPanel:get_argument_value(264),
              MainPanel:get_argument_value(265),
              MainPanel:get_argument_value(282)
              )

            -- Mi-8MTV2  sends to SimShaker
            stringToSend =
              string.format(
              "RotorRPM=%.0f;PanShake=%s",
              mainRotorRPM,
              PanelShake
            )

          elseif obj.Name == "UH-1H" then
            -------------------------------------------------------------------------------------------------------------------------------------------------------
            -- UH1 Relevent Info (from mainpanel_init.lua):
            -- RotorTach.input				= {0.0, 360.0}--{0.0, 300.0, 320.0, 339.0}
            -- RotorTach.output			= {0.0, 1.0}--{0.0, 0.83, 0.94, 1.0}
            -- UH-1 raw data is in RPM
            -- Multiply received value by 360 to get actual RPM
            local mainRotorRPM = MainPanel:get_argument_value(123) * 360
            local PanelShake =
              string.format(
              "%.2f~%.2f~%.2f",
              MainPanel:get_argument_value(264),
              MainPanel:get_argument_value(265),
              MainPanel:get_argument_value(282)
            )
            local leftDoor = MainPanel:get_argument_value(420)
            local rightDoor = MainPanel:get_argument_value(422)
            --local doors = string.format("%.2f~%.2f", MainPanel:get_argument_value(420), MainPanel:get_argument_value(422))
            local deadPilot = MainPanel:get_argument_value(248)
            -- UH-1H  sends to SimShaker
            stringToSend =
              string.format(
              "RotorRPM=%.0f;PanShake=%s;LDoor=%.2f;RDoor=%.2f;deadPilot=%.2f",
              mainRotorRPM,
              PanelShake,
              leftDoor,
              rightDoor,
              deadPilot
            )

          elseif obj.Name == "Ka-50" then
            -------------------------------------------------------------------------------------------------------------------------------------------------------
            -- calculate gauge percentage reading from gauge deflection value
            local mainRotorPercent = scale(MainPanel:get_argument_value(52), 0.000, 1.000, 0, 1.100)
            -- calculate RotorRPM from gauge percentage (max = 350RPM per internet sources)
            local mainRotorRPM = math.floor(scale(mainRotorPercent, 0, 1.000, 0, 350))

            local GunTrigger = MainPanel:get_argument_value(615)
            local APUoilP = MainPanel:get_argument_value(168)
            local APUvalve = MainPanel:get_argument_value(162)
            local APU = string.format("%.1f~%.1f", APUvalve, APUoilP)
            -- Ka-50  sends to SimShaker
            stringToSend =
              string.format(
              "RotorRPM=%.0f;APU=%s",
              mainRotorRPM,
              APU
            )

          elseif obj.Name == "Mi-24P" then
            -------------------------------------------------------------------------------------------------------------------------------------------------------
            -- calculate gauge percentage reading from gauge deflection value
            local mainRotorPercent = scale(MainPanel:get_argument_value(42), 0.000, 1.000, 0, 1.100)
            -- calculate RotorRPM from gauge percentage (nominal %95 = 240RPM per internet sources)
            local mainRotorRPM = math.floor(scale(mainRotorPercent, 0, 0.95, 0, 240))

            -- Mi-24  sends to TelemFFB
            stringToSend =
              string.format(
              "RotorRPM=%.3f",
              mainRotorRPM
            )

--           elseif obj.Name == "AH-64D_BLK_II" then
--             log.info("This is the Apache")
-- --             -- There is nothing of value to export from AH64 currently, placeholder only
-- --             -- Apache rotor RPM is max 265, send static 245 in telemetry
-- --             local mainRotorRPM = "245.000"
--
--
--             -- AH64  sends to TelemFFB
--             stringToSend =
--               string.format(
--               "RotorRPM=%s",
--               mainRotorRPM
--             )

          elseif
            obj.Name == "SA342M" or obj.Name == "SA342L" or obj.Name == "SA342Mistral" or obj.Name == "SA342Minigun"
           then -- Gazelle
            -- SA342 Relevent Info (from mainpanel_init.lua):
            -- Rotor_RPM.input				= {0,		50,		100,	150,	200,	250,	262,	316.29,	361.05,	387,	400,	450}
            -- Rotor_RPM.output			= {0.096,	0.191,	0.283,	0.374,	0.461,	0.549,	0.57,	0.665,	0.748,	0.802,	0.811,	0.904}
            -- directly access rotor rpm with get_param_handle call
            local mainRotorRPM = get_param_handle("Rotor_Rpm"):get()
            --log.info("gazelleInfo:"..RRPM)
            local RAltimeterMeter = MainPanel:get_argument_value(94) * 1000
            local RAltimeterOnOff = MainPanel:get_argument_value(91)
            local RAltimeterFlagPanne = MainPanel:get_argument_value(98)
            local RAltimeterFlagMA = MainPanel:get_argument_value(999)
            local RAltimeterTest = MainPanel:get_argument_value(100)
            local StatusString =
              RAltimeterOnOff .. "~" .. RAltimeterFlagPanne .. "~" .. RAltimeterFlagMA .. "~" .. RAltimeterTest
            -- Gazelle  sends to SimShaker
            stringToSend =
              string.format(
              "RotorRPM=%.0f;RadarAltimeterMeter=%.2f;RAltimeterStatus=%s",
              mainRotorRPM,
              RAltimeterMeter,
              StatusString
            )
          elseif
            obj.Name == "UH-60L" then
            -- directly access rotor rpm percent with get_param_handle call
            -- according to internet sources, nominal RPM of the UH60 is 258 RPM
            local mainRotorPercent = get_param_handle("RRPM"):get()
            local mainRotorRPM = scale(mainRotorPercent, 0, 100, 0, 258)
            -- UH60  sends to TelemFFB
            stringToSend =
              string.format(
              "RotorRPM=%.0f",
              mainRotorRPM
            )

-------------------------------------------------------------------------------------------------------------------------------------------------------
          elseif obj.Name == "P-51D" or obj.Name == "P-51D-30-NA" or obj.Name == "TF-51D" then
            local AirspeedNeedle = MainPanel:get_argument_value(11)*1000*1.852
            local Altimeter_10000_footPtr = MainPanel:get_argument_value(96)*100000
            local Altimeter_1000_footPtr = MainPanel:get_argument_value(24)*10000
            local Altimeter_100_footPtr = MainPanel:get_argument_value(25)*1000
            local Variometer = MainPanel:get_argument_value(29)
            local TurnNeedle = MainPanel:get_argument_value(27) * math.rad(3)
            local Landing_Gear_Handle = MainPanel:get_argument_value(150)
            local Manifold_Pressure = MainPanel:get_argument_value(10) * 65 + 10
            local AHorizon_Pitch = MainPanel:get_argument_value(15) * math.pi / 3.0
            local AHorizon_Bank = MainPanel:get_argument_value(14) * math.pi
            local AHorizon_PitchShift = MainPanel:get_argument_value(16) * 10.0 * math.pi / 180.0
            local GyroHeading = MainPanel:get_argument_value(12) * 2.0 * math.pi
            local Oil_Temperature = MainPanel:get_argument_value(30) * 100
            local Oil_Pressure = MainPanel:get_argument_value(31) * 200
            local Fuel_Pressure = MainPanel:get_argument_value(32) * 25
            -- Calculate Engine RPM from redline value and engine.RPM value
            local engine_redline_reference = 3000
            local engPercent = string.format("%.3f", math.max(engine.RPM.left, engine.RPM.right))
            local actualRPM = math.floor(engine_redline_reference * (engPercent / 100))

            --local myselfData = string.format("%.2f~%.2f~%.2f", obj.Heading, obj.Pitch, obj.Bank)
            local PanelShake =
              string.format(
              "%.2f~%.2f~%.2f",
              MainPanel:get_argument_value(181),
              MainPanel:get_argument_value(180),
              MainPanel:get_argument_value(189)
            )
            local LandingGearGreenLight = MainPanel:get_argument_value(80)
            local WEPwire = MainPanel:get_argument_value(190)
            -- P-51D sends to SimShaker
            stringToSend =
              string.format(
              "PanShake=%s;GreenLight=%.1f;MP-WEP=%.2f~%.2f;ActualRPM=%s",
              PanelShake,
              LandingGearGreenLight,
              Manifold_Pressure,
              WEPwire,
              actualRPM
            )
--           elseif obj.Name == "TF-51D" then
--             -------------------------------------------------------------------------------------------------------------------------------------------------------
--             local AirspeedNeedle = MainPanel:get_argument_value(11) * 1000 * 1.852
--             local Altimeter_10000_footPtr = MainPanel:get_argument_value(96) * 100000
--             local Altimeter_1000_footPtr = MainPanel:get_argument_value(24) * 10000
--             local Altimeter_100_footPtr = MainPanel:get_argument_value(25) * 1000
--             local Variometer = MainPanel:get_argument_value(29)
--             local TurnNeedle = MainPanel:get_argument_value(27) * math.rad(3)
--             local Landing_Gear_Handle = MainPanel:get_argument_value(150)
--             local Manifold_Pressure = MainPanel:get_argument_value(10) * 65 + 10
--             local Engine_RPM = MainPanel:get_argument_value(23) * 4500
--             local AHorizon_Pitch = MainPanel:get_argument_value(15) * math.pi / 3.0
--             local AHorizon_Bank = MainPanel:get_argument_value(14) * math.pi
--             local AHorizon_PitchShift = MainPanel:get_argument_value(16) * 10.0 * math.pi / 180.0
--             local GyroHeading = MainPanel:get_argument_value(12) * 2.0 * math.pi
--             local Oil_Temperature = MainPanel:get_argument_value(30) * 100
--             local Oil_Pressure = MainPanel:get_argument_value(31) * 200
--             local Fuel_Pressure = MainPanel:get_argument_value(32) * 25
--             local Coolant_Temperature = MainPanel:get_argument_value(22) * 230 - 80
--             local Carb_Temperature = MainPanel:get_argument_value(21) * 230 - 80
--             local LandingGearGreenLight = MainPanel:get_argument_value(80)
--             local LandingGearRedLight = MainPanel:get_argument_value(82)
--             local Vacuum_Suction = MainPanel:get_argument_value(9) * 10
--             --local myselfData = string.format("%.2f~%.2f~%.2f", obj.Heading, obj.Pitch, obj.Bank)
--             local PanelShake =
--               string.format(
--               "%.2f~%.2f~%.2f",
--               MainPanel:get_argument_value(181),
--               MainPanel:get_argument_value(180),
--               MainPanel:get_argument_value(189)
--             )
--             local LandingGearGreenLight = MainPanel:get_argument_value(80)
--             local WEPwire = MainPanel:get_argument_value(190)
--             local extModelArguments =
--               string.format(
--               "%.2f~%.2f~%.2f~%.2f",
--               LoGetAircraftDrawArgumentValue(0),
--               LoGetAircraftDrawArgumentValue(1),
--               LoGetAircraftDrawArgumentValue(5),
--               LoGetAircraftDrawArgumentValue(6)
--             )
--             -- TF-51 sends to SimShaker
--             stringToSend =
--               string.format(
--               "PanShake=%s",
--               PanelShake
--             )
          elseif obj.Name == "FW-190D9" or obj.Name == "FW-190A8" then
            -------------------------------------------------------------------------------------------------------------------------------------------------------
            local Manifold_Pressure = MainPanel:get_argument_value(46)
            -- Calculate Engine RPM from redline value and engine.RPM value
            local engine_redline_reference = 2700
            local engPercent = string.format("%.3f", math.max(engine.RPM.left, engine.RPM.right))
            local actualRPM = math.floor(engine_redline_reference * (engPercent / 100))

            local PanelShake =
              string.format(
              "%.2f~%.2f~%.2f",
              MainPanel:get_argument_value(205),
              MainPanel:get_argument_value(204),
              MainPanel:get_argument_value(206)
            )
            local MW = MainPanel:get_argument_value(106)
            -- FW-190D9 sends to SimShaker
            stringToSend =
              string.format(
              "PanShake=%s;MP-MW=%.2f~%.2f;ActualRPM=%s",
              PanelShake,
              Manifold_Pressure,
              MW,
              actualRPM
            )
          elseif obj.Name == "Bf-109K-4" then
            -------------------------------------------------------------------------------------------------------------------------------------------------------
            local Manifold_Pressure = MainPanel:get_argument_value(32)
            local myselfData = string.format("%.2f~%.2f~%.2f", obj.Heading, obj.Pitch, obj.Bank)
            -- Calculate Engine RPM from redline value and engine.RPM value
            local engine_redline_reference = 2800
            local engPercent = string.format("%.3f", math.max(engine.RPM.left, engine.RPM.right))
            local actualRPM = math.floor(engine_redline_reference * (engPercent / 100))
            local PanelShake =
              string.format(
              "%.2f~%.2f~%.2f",
              MainPanel:get_argument_value(146),
              MainPanel:get_argument_value(147),
              MainPanel:get_argument_value(1489)
            )
            local MW = MainPanel:get_argument_value(1)
            -- Bf-109K-4 sends to SimShaker
            stringToSend =
              string.format(
              "PanelShake=%s;MP-MW=%.2f~%.2f;ActualRPM=%s",
              PanelShake,
              Manifold_Pressure,
              MW,
              actualRPM
            )
          elseif obj.Name == "SpitfireLFMkIX" or obj.Name == "SpitfireLFMkIXCW" then
            -------------------------------------------------------------------------------------------------------------------------------------------------------
            -- Calculate Engine RPM from redline value and engine.RPM value
            local engine_redline_reference = 3000
            local engPercent = string.format("%.3f", math.max(engine.RPM.left, engine.RPM.right))
            local actualRPM = math.floor(engine_redline_reference * (engPercent / 100))

            local PanelShake =
              string.format(
              "%.2f~%.2f~%.2f",
              MainPanel:get_argument_value(144),
              MainPanel:get_argument_value(143),
              MainPanel:get_argument_value(142)
            )
            -- SPITFIRE sends to SimShaker
            stringToSend =
              string.format(
              "PanShake=%s;ActualRPM=%s",
              PanelShake,
              actualRPM
              )
          elseif string.find(obj.Name, "P-47D") then
            -------------------------------------------------------------------------------------------------------------------------------------------------------
            -- Calculate Engine RPM from redline value and engine.RPM value
            local engine_redline_reference = 2700
            local engPercent = string.format("%.3f", math.max(engine.RPM.left, engine.RPM.right))
            local actualRPM = math.floor(engine_redline_reference * (engPercent / 100))
            -- P47 sends to TelemFFB
            stringToSend =
              string.format(
              "ActualRPM=%s",
              actualRPM
              )
          elseif string.find(obj.Name, "I-16") then
            -------------------------------------------------------------------------------------------------------------------------------------------------------
            -- Calculate Engine RPM from redline value and engine.RPM value
            local engine_redline_reference = 2200
            local engPercent = string.format("%.3f", math.max(engine.RPM.left, engine.RPM.right))
            local actualRPM = math.floor(engine_redline_reference * (engPercent / 100))
            -- I-16 sends to TelemFFB
            stringToSend =
              string.format(
              "ActualRPM=%s",
              actualRPM
            )
            elseif string.find(obj.Name, "MosquitoFBMkVI") then
            -------------------------------------------------------------------------------------------------------------------------------------------------------
            -- Calculate Engine RPM from redline value and engine.RPM value
            local engine_redline_reference = 3000
            local engPercent = string.format("%.3f", math.max(engine.RPM.left, engine.RPM.right))
            local actualRPM = math.floor(engine_redline_reference * (engPercent / 100))
            -- Mosquito sends to TelemFFB
            stringToSend =
              string.format(
              "ActualRPM=%s",
              actualRPM
            )

          elseif obj.Name == "A-10C" then
            -------------------------------------------------------------------------------------------------------------------------------------------------------
            local FlapsPos = MainPanel:get_argument_value(653)
            local Canopy = MainPanel:get_argument_value(7)
            local APU = MainPanel:get_argument_value(13)
            -- A-10C  sends to SimShaker
            stringToSend =
              string.format(
              "Flaps=%.2f;Canopy=%.2f;APU=%.2f",
              FlapsPos,
              Canopy,
              APU
            )
            elseif obj.Name == "A-10C_2" then
            -------------------------------------------------------------------------------------------------------------------------------------------------------
            local FlapsPos = MainPanel:get_argument_value(653)
            local Canopy = MainPanel:get_argument_value(7)
            local APU = MainPanel:get_argument_value(13)
            -- A-10C  sends to SimShaker
            stringToSend =
              string.format(
              "Flaps=%.2f;Canopy=%.2f;APU=%.2f",
              FlapsPos,
              Canopy,
              APU
            )
          elseif obj.Name == "MiG-21Bis" then
            -------------------------------------------------------------------------------------------------------------------------------------------------------
            local Voltmeter = MainPanel:get_argument_value(124) * 30

            local AirBrake3d = MainPanel:get_argument_value(7)
            local Flaps3d = MainPanel:get_argument_value(910)
            local Afterburner1 = MainPanel:get_argument_value(507)
            local Afterburner2 = MainPanel:get_argument_value(508)
            local LampCheck = MainPanel:get_argument_value(407)
            local AB12 = string.format("%.1f~%.1f~%.1f", Afterburner1, Afterburner2, LampCheck)
            local SPS = MainPanel:get_argument_value(624)
            local CanopyWarnLight = MainPanel:get_argument_value(541)
            -- MiG-21Bis sends to SimShaker
            stringToSend =
              string.format(
              "Flaps=%.2f;Canopy=%.1f;SPS=%.1f",
              Flaps3d,
              CanopyWarnLight,
              SPS
            )


          elseif obj.Name == "L-39C" or obj.Name == "L-39ZA" then
            -------------------------------------------------------------------------------------------------------------------------------------------------------
            local TestBtn = MainPanel:get_argument_value(203) -- not presented in mainpanel_init.lua
            local TestBtn2 = MainPanel:get_argument_value(538) -- not presented inmainpanel_init.lua

            local Canopy1 = MainPanel:get_argument_value(139)
            local Canopy2 = MainPanel:get_argument_value(140)

            stringToSend =
              string.format(
              "Canopy1=%.2f;Canopy2=%.2f",
              Canopy1,
              Canopy2
            )
          elseif obj.Name == "M-2000C" then
            -------------------------------------------------------------------------------------------------------------------------------------------------------
            local PanelShake =
              string.format(
              "%.2f~%.2f~%.2f",
              MainPanel:get_argument_value(181),
              MainPanel:get_argument_value(180),
              MainPanel:get_argument_value(189)
            )
            -- M-2000C sends to SimShaker
            stringToSend =
              string.format(
              "PanShake=%s", PanelShake
            )
          elseif obj.Name == "AV8BNA" then
            -------------------------------------------------------------------------------------------------------------------------------------------------------
            -- AV8BNA sends to SimShaker

            local AileronTrim = MainPanel:get_argument_value(473) * 0.3
            local RudderTrim = MainPanel:get_argument_value(474)
            local stickY = MainPanel:get_argument_value(701)
            local stickX = MainPanel:get_argument_value(702)

            -- local flt = GetDevice(28)
            -- local sflt = (dump(flt) .. dump(getmetatable(flt)) )
            -- if flt ~= nil then
            --   flt:update_arguments()
            -- end

            stringToSend =
              string.format(
              "AileronTrim=%.3f;RudderTrim=%.3f;StickX=%.3f;StickY=%.3f;vv=%s",
              AileronTrim,
              RudderTrim,
              stickX,
              stickY,
              self.recv_data
            )

              elseif string.find(obj.Name, "F-14") then
                -------------------------------------------------------------------------------------------------------------------------------------------------------
                --local sensor_data = obj.get_base_data()
                --log.info("TELEMFFB FOUND AIRCRAFT: "..obj.Name)
                local f14SpeedbrakePos = LoGetAircraftDrawArgumentValue(400)
                mech["speedbrakes"]["value"] = f14SpeedbrakePos
                mech["speedbrakes"]["status"] = f14SpeedbrakePos >= 0.9999
                local f14_Splr_L_Outer = LoGetAircraftDrawArgumentValue(1010)
                local f14_Splr_L_Inner = LoGetAircraftDrawArgumentValue(1011)
                local f14_Splr_R_Inner = LoGetAircraftDrawArgumentValue(1012)
                local f14_Splr_R_Outer = LoGetAircraftDrawArgumentValue(1013)
                local f14_DLC_Spoiler = string.format("%.2f~%.2f~%.2f~%.2f", f14_Splr_L_Outer, f14_Splr_L_Inner, f14_Splr_R_Inner, f14_Splr_R_Outer)
                local REngine_RPM = "0"
                local LEngine_RPM = "0"
                if getEngineRightRPM then
                  REngine_RPM = sensor_data.getEngineRightRPM()
                end
                if getEngineLeftRPM then
                  LEngine_RPM = sensor_data.getEngineLeftRPM()
                end

                local RPM = REngine_RPM .. "~" .. LEngine_RPM

                if f14 == nil or f14 == false then
                  setupF14Export()
                end
                local additionalData = ""

                if f14 == true then
                  -- usual case after first time
                  local epoxy = GetDevice(6)
                  if epoxy ~= nil and type(epoxy) ~= "number" and f14_i2n ~= nil then
                    local values = epoxy:get_values()
                    for i, v in ipairs(values) do
                      f14_variables[f14_i2n[i]] = v
                      additionalData = additionalData .. "f14_" .. f14_i2n[i] .. "=" .. v .. ";" -- add f14_ prefix to mark these values
                    end
                  end
                  -- log.info("additionalData:"..additionalData)
                end

                -- F-14 sends to SimShaker
                stringToSend =
                  string.format(
                  "%s;Spoilers=%s",
                  additionalData,
                  f14_DLC_Spoiler
                  )

-------------------------------------------------------------------------------------------------------------------------------------------------------
          else -- FC3 Planes
            local engine = LoGetEngineInfo()

            local LandingGearState = LoGetMechInfo().gear.value
            local SpeedBrakePos = LoGetMechInfo().speedbrakes.value
            local CanopyPos = LoGetMechInfo().canopy.value
            local FlapsPos = LoGetMechInfo().flaps.value
            local WingsPos = 0 --LoGetMechInfo().wing.value
            local DragChuteState = LoGetMechInfo().parachute.value
            local MechState = string.format("%.2f-%.2f", DragChuteState, 100 * LoGetMechInfo().gear.value)
            local MCP = LoGetMCPState()

            local engineRPM = string.format("%.0f~%.0f", LoGetEngineInfo().RPM.left, LoGetEngineInfo().RPM.right)
            local MCPState =
              tostring(MCP.LeftEngineFailure) .. "~" .. tostring(MCP.RightEngineFailure) .. "~" .. tostring(MCP.HydraulicsFailure) ..
                      "~" .. tostring(MCP.ACSFailure) .. "~" .. tostring(MCP.AutopilotFailure) .. "~" .. tostring(MCP.MasterWarning) ..
                      "~" .. tostring(MCP.LeftTailPlaneFailure) .. "~" .. tostring(MCP.RightTailPlaneFailure) .. "~" .. tostring(MCP.LeftAileronFailure) ..
                      "~" .. tostring(MCP.RightAileronFailure) .. "~" .. tostring(MCP.CannonFailure) .. "~" .. tostring(MCP.StallSignalization) ..
                      "~" .. tostring(MCP.LeftMainPumpFailure) .. "~" .. tostring(MCP.RightMainPumpFailure) .. "~" .. tostring(MCP.LeftWingPumpFailure) ..
                      "~" .. tostring(MCP.RightWingPumpFailure) .. "~" .. tostring(MCP.RadarFailure) .. "~" .. tostring(MCP.EOSFailure) ..
                      "~" .. tostring(MCP.MLWSFailure) .. "~" .. tostring(MCP.RWSFailure) .. "~" .. tostring(MCP.ECMFailure) ..
                      "~" .. tostring(MCP.GearFailure) .. "~" .. tostring(MCP.MFDFailure) .. "~" ..tostring(MCP.HUDFailure) ..
                      "~" .. tostring(MCP.HelmetFailure) .. "~" .. tostring(MCP.FuelTankDamage)
            log.info("TELEMFFB TREATING THIS AS AN FC3 AIRCRAFT: "..obj.Name)
            -- FC3 Plane sends to SimShaker
            stringToSend =
              string.format(
              "MCPState=%s;DragChute=%.2f;Flaps=%.2f;Canopy=%.2f;Wings=%.2f",
              MCPState,
              DragChuteState,
              FlapsPos,
              CanopyPos,
              WingsPos
            )
          end      

          local items = {
            {"T", "%.3f", t},
            {"N", "%s", obj.Name},
            {"SelfData", "%s", myselfData},
            {"EngRPM", "%s", engineRPM},
            {"ACCs", "%s", AccelerationUnits},
            {"Gun", "%s", CannonShells},
            {"Wind", "%s", windVelocityVectors},
            {"VlctVectors", "%s", velocityVectors},
            {"altASL", "%.2f", altAsl},
            {"altAgl", "%.2f", altAgl},
            {"AoA", "%.2f", aoa},
            {"IAS", "%.2f", IAS},
            {"TAS", "%.2f", tas},
            {"WeightOnWheels", "%s", WoW},
            {"Flares", "%s", CM.flare},
            {"Chaff", "%s", CM.chaff},
            {"PayloadInfo", "%s", PayloadInfo},
            {"Mach", "%.4f", M_number},
            {"MechInfo", "%s", JSON:encode(mech):gsub("\n", "")},
            {"Afterburner", "%s", AB},
            {"DynamicPressure", "%.3f", DynamicPressure},
            {"Incidence", "%s", incidence},  -- relative airstream in body frame
            {"AirDensity", "%.3f", AirDensity},
            {"CAlpha", "%.3f", calc_alpha},
            {"CBeta", "%.3f", calc_beta}, -- sideslip angle deg
            {"RelWind", "%s", rel_wind}, --wind in body frame
          }
          
          local formattedValues = {}
          for _, item in ipairs(items) do
            local value = item[3]
            if value ~= nil then
              local formattedValue = string.format(item[2], value)
              table.insert(formattedValues, item[1] .. "=" .. formattedValue)
            end

          end
          
          stringToSend = stringToSend .. ";" .. table.concat(formattedValues, ";")
          
          -- Common variables
          -- stringToSend = string.format("T=%.3f;N=%s;SelfData=%s;%s;EngRPM=%s;ACCs=%s;Gun=%s;Wind=%s;VlctVectors=%s;altASL=%.2f;altAgl=%.2f;AoA=%.2f;IAS=%.2f;TAS=%.2f;WeightOnWheels=%s;Flares=%s;Chaff=%s;PayloadInfo=%s;Mach=%.4f;MechInfo=%s;Afterburner=%s",               
          --   t,
          --   obj.Name,
          --   myselfData,
          --   stringToSend,
          --   engineRPM,
          --   AccelerationUnits,
          --   CannonShells,
          --   windVelocityVectors,
          --   velocityVectors, altAsl, altAgl, aoa, IAS, tas, WoW, CM.flare, CM.chaff, PayloadInfo, M_number, mech, AB)

          socket.try(self.sock_udp:sendto(stringToSend, self.host, self.port))
        end
      end
    )
    data_send()
  end,
  Stop = function(self)
    local connection_close =
      socket.protect(
      function()
        if self.sock_udp then
          socket.try(self.sock_udp:sendto("DISCONNECT", self.host, self.port))
          self.sock_udp:close()
          self.sock_udp = nil

        end
      end
    )
    connection_close()
  end
}

----------------------------------------------------------------------------------------------------
--http://forums.eagle.ru/showpost.php?p=2431726&postcount=5
-- Works before mission start
do
  local SimLuaExportStart = LuaExportStart
  LuaExportStart = function()
    f_telemFFB:Start()
    if SimLuaExportStart then
      SimLuaExportStart()
    end
  end
end

-- Works after every simulation frame
do
  local SimLuaExportAfterNextFrame = LuaExportAfterNextFrame
  LuaExportAfterNextFrame = function()
    f_telemFFB:AfterNextFrame()
    if SimLuaExportAfterNextFrame then
      SimLuaExportAfterNextFrame()
    end
  end
end

-- Works after mission stop
do
  local SimLuaExportStop = LuaExportStop
  LuaExportStop = function()
    f_telemFFB:Stop()
    if SimLuaExportStop then
      SimLuaExportStop()
    end
  end
end

do
  local SimLuaExportBeforeNextFrame = LuaExportBeforeNextFrame
  LuaExportBeforeNextFrame = function()
    f_telemFFB:BeforeNextFrame()
    if SimLuaExportBeforeNextFrame then
      SimLuaExportBeforeNextFrame()
    end
  end
end

function parse_indication(indicator_id) -- Thanks to [FSF]Ian for this function code
  local ret = {}
  local li = list_indication(indicator_id)
  if li == "" then
    return nil
  end
  local m = li:gmatch("-----------------------------------------\n([^\n]+)\n([^\n]*)\n")
  while true do
    local name, value = m()
    if not name then
      break
    end
    ret[name] = value
  end
  return ret
end

function check(indicator)
  if indicator == nil then
    return " "
  else
    return indicator
  end
end

function setupF14Export()
  local epoxy = GetDevice(6)
  if epoxy then
    -- check functions
    local meta = getmetatable(epoxy)
    f14 = false
    if meta then
      local ind = getmetatable(epoxy)["__index"]
      if ind then
        if ind["get_version"] ~= nil and ind["get_variable_names"] ~= nil and ind["get_values"] ~= nil then
          f14 = true
          --log.info("Found F-14 exports")
          f14_n2i = {}
          f14_i2n = {}
          f14_variables = {}
          names = epoxy:get_variable_names()
          for i, v in ipairs(names) do
            f14_n2i[v] = i
            f14_i2n[i] = v
            --log.debug(tostring(v).."->"..tostring(i))
          end
        end
      end
    end
  end
end