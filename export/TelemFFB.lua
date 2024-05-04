local JSON = loadfile("Scripts\\JSON.lua")()
package.path = package.path .. ";.\\LuaSocket\\?.lua;Scripts\\?.lua;"
package.cpath = package.cpath .. ";.\\LuaSocket\\?.dll"
local socket = require("socket")
local calc_damage = 0
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

function getDamage(draw_vars)
    local sum = 0
    for _, var in ipairs(draw_vars) do
        if type(var) == "number" then
            -- Handle individual values
            local value = LoGetAircraftDrawArgumentValue(var)
            sum = sum + value
        elseif type(var) == "string" then
            -- Handle ranges
            local start, finish = var:match("(%d+)-(%d+)")
            if start and finish then
                for i = tonumber(start), tonumber(finish) do
                    local value = LoGetAircraftDrawArgumentValue(i)
                    sum = sum + value
                end
            end
        end
    end
    return sum
end
function enableGetDamage(flag)
  if flag == 1 then
    log.info("TELEMFFB Enabling Damage model retrieval")
    calc_damage = 1
  elseif flag == 0 then
    log.info("TELEMFFB Disabling Damage model retrieval")
    calc_damage = 0

  end
end

function calculate_wind_direction_2d(wind_vector)
    local deg = math.fmod((180 / math.pi) * math.atan2(wind_vector.z, wind_vector.x), 360)
    if deg < 0 then
        deg = deg + 360
    end
    --local wind_direction_rad = math.atan2(wind_vector.z, wind_vector.x)
    --local wind_direction_deg = math.deg(wind_direction_rad)
    ----if wind_direction_deg < 0 then
    ----    wind_direction_deg = wind_direction_deg + 360  -- Convert negative angles to positive
    ----end
    ------ Adjust for non-standard orientation
    ----wind_direction_deg = (wind_direction_deg + 180) % 360
    return deg
end

function get_indicated_airspeed(TAS, density)
    local Ki = 0
    local a0 = 290.07 -- speed of sound at sea level in ISA conditions m/s
    local P0 = 1013.25 -- static pressure at sea level in ISA conditions Pa
    local part1 = (0.5 * density * TAS^2 / P0) + 1
    local part2 = ((part1)^(2/7) - 1) * 5
    local IAS = a0 * math.sqrt(part2) + Ki
    return IAS
end

function getShellCount(payloadInfo, id)
    local totalShellCount = 0

    for _, station in ipairs(payloadInfo.Stations) do
        weapon_id = string.format( "%d.%d.%d.%d", station.weapon.level1, station.weapon.level2, station.weapon.level3, station.weapon.level4)
        if weapon_id == id then
            totalShellCount = totalShellCount + station.count
        end
    end

    return totalShellCount
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

    self.dbg_recv_cmds = ""
    self.prev_command_t = socket.gettime()

    self.queued_commands = {}

    self.host = "127.0.0.1"
    self.port = 34380

    self.sock_udp = socket.try(socket.udp())
    self.sock_rcv = socket.try(socket.udp())
    socket.try(self.sock_rcv:setsockname("127.0.0.1", 34381))
    socket.try(self.sock_rcv:settimeout(.001))

    socket.try(self.sock_udp:settimeout(.001))
    socket.try(self.sock_udp:setoption('broadcast', true))
    socket.try(self.sock_udp:setpeername("127.255.255.255", 34380))

    socket.try(self.sock_udp:send("Ev=Start"))

  end,
  BeforeNextFrame = function(self)
    --LoSetCommand(2001, 0.25)

    if self.sock_rcv then
      while sock_readable(self.sock_rcv) do
        local data, addr, src_port = self.sock_rcv:receivefrom()
        -- Update the time of the last command reception
        self.prev_command_t = socket.gettime()

        if data == nil then
          data = ""
        else
          -- Store command along with its source port and reception time
          self.queued_commands[src_port] = {
            command = data,
            time = socket.gettime()
          }
        end
      end

      -- Remove commands older than 100ms
      for port, command_info in pairs(self.queued_commands) do
        if socket.gettime() - command_info.time > 0.1 then
          self.queued_commands[port] = nil
        end
      end
      self.dbg_recv_cmds = ""
      -- Execute commands from the command table
      for _, command_info in pairs(self.queued_commands) do
        self.dbg_recv_cmds = self.dbg_recv_cmds .. "\n" .. command_info.command
        local f = loadstring(command_info.command)
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
          local engine = LoGetEngineInfo()

          local MCP = LoGetMCPState()
          local hydraulicPressureSim = string.format("%.3f~%.3f", engine.HydraulicPressure.left, engine.HydraulicPressure.right)

          local damage = "not enabled"
          local damage_vars = "not supported"
          local hydraulicPressure = "n/a"

          local AB = string.format("%.2f~%.2f", LoGetAircraftDrawArgumentValue(28), LoGetAircraftDrawArgumentValue(29))

          local WoW = string.format("%.2f~%.2f~%.2f", LeftGear, NoseGear, RightGear)
          local tot_wow = tonumber(string.format("%2f",LeftGear+NoseGear+RightGear))
          local on_ground = tot_wow > 0.00

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

          local vertical_speed = LoGetVerticalVelocity()

          local wind = LoGetVectorWindVelocity()
          local wind_vec = Vector(wind.x, wind.y, wind.z)
          local wind_velocity = math.sqrt(wind.x^2 + wind.y^2 + wind.z^2)
          local wind_direction = calculate_wind_direction_2d(wind)

          local velocityVectors = string.format("%.2f~%.2f~%.2f", vectorVel.x, vectorVel.y, vectorVel.z)

          local incidence_vec = Vector(vectorVel.x, vectorVel.y, vectorVel.z)
          incidence_vec = incidence_vec - wind_vec
          incidence_vec = incidence_vec:rotY(-(2.0 * math.pi - obj.Heading))
          incidence_vec = incidence_vec:rotZ(-obj.Pitch)
          incidence_vec = incidence_vec:rotX(-obj.Bank)
          local incidence = string.format("%.3f~%.3f~%.3f", incidence_vec.x, incidence_vec.y, incidence_vec.z)
          local calculated_TAS = math.sqrt(incidence_vec.x^2 + incidence_vec.y^2)

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

          local calculated_IAS = get_indicated_airspeed(calculated_TAS, AirDensity)

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
            mech["speedbrakes"]["value"] = 0.0

            local mainRotorRPM = MainPanel:get_argument_value(42) * 220
            local IAS_L = MainPanel:get_argument_value(24)
            damage_vars = {
              81,115,116,117,118,119,120,121,122,123,124,125,126,127,128,129,134,135,136,137,138,146,147,147,148,
              149,150,151,152,153,154,155,156,157,158,160,161,166,167,188,189,215,225,234,236,243,247,251,252,252,
              297,452,663,664,665,1100
            }

            hydraulicPressure = string.format("%.3f~%.3f", MainPanel:get_argument_value(404), MainPanel:get_argument_value(405))


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
            damage_vars = {
              81,142,146,152,153,154,157,158,159,163,167,169,233,235,244,255,256,257,258,265,297,298,429,430,431,432,
              433,434,435,436,453,454,455,456,457,458,459,460,461,462,465,466,530,898,899
            }

            local hyd_state = MainPanel:get_argument_value(104)
            if hyd_state == 0 then
              hydraulicPressure = "true"
            else
              hydraulicPressure = "false"
            end

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
            local LeftGear = LoGetAircraftDrawArgumentValue(104)
            local NoseGear = LoGetAircraftDrawArgumentValue(104)
            local RightGear = LoGetAircraftDrawArgumentValue(104)
            WoW = string.format("%.2f~%.2f~%.2f", LeftGear, NoseGear, RightGear)
            tot_wow = tonumber(string.format("%2f",LeftGear+NoseGear+RightGear))
            on_ground = tot_wow > 0

            -- UH1 places canon shell data only in payload block
            -- pull it out and write to CannonShells for proper effect generation
            local uh1payload = LoGetPayloadInfo()
            CannonShells = getShellCount(uh1payload, "4.6.10.0")
            stations = LoGetPayloadInfo().Stations
            PayloadInfo = "empty"
            temparray = {}

            for i_st, st in pairs(stations) do
              -- we want to re-write the payload block without the "unknown" data which contains the cannon rounds.  This is so
              -- the "payload release" effect does not play while canons are firing.
              local name = LoGetNameByType(st.weapon.level1,st.weapon.level2,st.weapon.level3,st.weapon.level4);
              if not string.find(name, "UNKNOWN") then
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
            end
            stringToSend =
              string.format(
              "RotorRPM=%.0f;PanShake=%s;LDoor=%.2f;RDoor=%.2f;deadPilot=%.2f",
              mainRotorRPM,
              PanelShake,
              leftDoor,
              rightDoor,
              deadPilot
            )

          elseif string.find(obj.Name, "OH-6A", 0, true) then

              -- Rotor RPM gauge is panel arg # 308
              local mainRotorGauge = MainPanel:get_argument_value(308)
              -- Rotor RPM reads 500 when gauge data is 0.745.. scale based on these values
              local mainRotorRPM = math.floor(scale(mainRotorGauge, 0, .745, 0, 500))
              local oh6payload = LoGetPayloadInfo()
              -- OH-6A, like the UH1 only has cannon info in payload block.  use getShellCount to pull out the applicable data and write it into the CannonShells telemetry
              CannonShells = getShellCount(oh6payload, "4.6.6.0")
              stations = LoGetPayloadInfo().Stations
              PayloadInfo = "empty"
              temparray = {}

              for i_st, st in pairs(stations) do
                  -- we want to re-write the payload block without the canon data which contains the cannon rounds.  This is so
                  -- the "payload release" effect does not play while canons are firing.
                  local name = LoGetNameByType(st.weapon.level1,st.weapon.level2,st.weapon.level3,st.weapon.level4);
                  id = string.format( "%d.%d.%d.%d", st.weapon.level1, st.weapon.level2, st.weapon.level3, st.weapon.level4)
                  if id ~= "4.6.6.0" and id ~= "0.0.0.0" then
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
              end

              stringToSend =
                string.format(
              "RotorRPM=%.0f",
                mainRotorRPM
              )


          elseif string.find(obj.Name, "Ka-50", 0, true) then
            -------------------------------------------------------------------------------------------------------------------------------------------------------
            -- calculate gauge percentage reading from gauge deflection value
            local mainRotorPercent = scale(MainPanel:get_argument_value(52), 0.000, 1.000, 0, 1.100)
            -- calculate RotorRPM from gauge percentage (max = 350RPM per internet sources)
            local mainRotorRPM = math.floor(scale(mainRotorPercent, 0, 1.000, 0, 350))
            damage_vars = {
              81,115,116,117,118,119,120,121,122,123,124,125,126,127,128,129,130,131,132,134,
              135,136,144,146,147,148,149,150,152,153,154,156,157,158,159,161,167,188,189,213,214,223,224,
              233,235,241,244,247,249,250,265,266,267,296,297,298,299,301,302,302,303,304,305
            }

            hydraulicPressure = string.format("%.3f~%.3f", MainPanel:get_argument_value(471), MainPanel:get_argument_value(472))

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
            damage_vars = {
              61,62,65,81,115,116,117,118,119,120,121,122,123,124,125,126,127,128,129,134,136,137,
              146,147,148,149,150,151,152,153,154,155,156,157,158,159,160,161,166,167,183,185,213,215,223,
              225,233,234,235,236,242,243,245,246,265,266,267,296,297,298,299,300,301,302,303,303,459,460,
              461,462
            }
            hydraulicPressure = string.format("%.3f~%.3f", MainPanel:get_argument_value(23), MainPanel:get_argument_value(24))

            mech["speedbrakes"]["value"] = 0.0

            -- Mi-24  sends to TelemFFB
            stringToSend =
              string.format(
              "RotorRPM=%.3f",
              mainRotorRPM
            )

           elseif obj.Name == "AH-64D_BLK_II" then
            damage_vars = {
              61,62,63,65,81,82,116,117,119,120,122,123,125,126,146,148,149,150,151,152,153,154,155,156,157,158,160,
              166,214,215,224,225,226,227,238,242,245,255,256,257,259,264,300,610
            }

          elseif obj.Name == "SA342M" or obj.Name == "SA342L" or obj.Name == "SA342Mistral" or obj.Name == "SA342Minigun"
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
            damage_vars = {
              145,146,147,148,149,150,151,152,153,154,155,156,157,158,159,160,161,162,163,164,165,166,167,168,169,170,
              175,177,178,179,180,181,204,205
            }

            local hyd_state = MainPanel:get_argument_value(11)
            if hyd_state == 0 then
              hydraulicPressure = "true"
            else
              hydraulicPressure = "false"
            end

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
          elseif obj.Name == "UH-60L" then
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
            damage_vars = {
              53,65,81,119,134,135,136,147,148,149,150,152,153,154,157,158,213,214,215,216,217,223,224,225,226,227,
              233,234,235,236,238,240,242,242,243,247,380,381,382,383,429,430,431
            }
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

          elseif obj.Name == "FW-190D9" then
            -------------------------------------------------------------------------------------------------------------------------------------------------------
            local Manifold_Pressure = MainPanel:get_argument_value(46)
            -- Calculate Engine RPM from redline value and engine.RPM value
            local engine_redline_reference = 2700
            local engPercent = string.format("%.3f", math.max(engine.RPM.left, engine.RPM.right))
            local actualRPM = math.floor(engine_redline_reference * (engPercent / 100))
            damage_vars = {
              64,65,66,67,81,134,135,136,151,152,153,154,157,158,162,168,213,214,215,216,217,223,224,225,226,227,233,
              234,235,236,237,239,241,243,247,255,259,273,296,297,298,299,300,301,302,303,304,305,380,381,382,429,
              430,431
            }

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

          elseif obj.Name == "FW-190A8" then
            -------------------------------------------------------------------------------------------------------------------------------------------------------
            local Manifold_Pressure = MainPanel:get_argument_value(46)
            -- Calculate Engine RPM from redline value and engine.RPM value
            local engine_redline_reference = 2700
            local engPercent = string.format("%.3f", math.max(engine.RPM.left, engine.RPM.right))
            local actualRPM = math.floor(engine_redline_reference * (engPercent / 100))
            damage_vars = {
              81,135,136,146,148,149,150,152,153,154,157,158,213,214,215,216,223,224,225,226,234,236,238,240,242,247,
              296,380,381,382,429,430,431
            }

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
            damage_vars = {
              81,147,148,149,150,151,152,153,154,156,157,158,213,214,215,216,217,218,219,220,223,224,225,226,227,228,
              229,230,233,234,235,236,237,239,247,243
            }
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
            local spit_ldg_right = LoGetAircraftDrawArgumentValue(3)
            mech["gear"]["value"] = spit_ldg_right
            damage_vars = {
              65,81,119,147,148,149,150,151,152,153,154,156,157,158,213,214,215,216,217,223,224,225,226,227,233,234,
              235,236,238,240,241,242,247,380,381,382,383,429,430,431
            }

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

          elseif string.find(obj.Name, "P-47", 0, true) then
            -------------------------------------------------------------------------------------------------------------------------------------------------------
            -- Calculate Engine RPM from redline value and engine.RPM value
            local engine_redline_reference = 2700
            local engPercent = string.format("%.3f", math.max(engine.RPM.left, engine.RPM.right))
            local actualRPM = math.floor(engine_redline_reference * (engPercent / 100))
            local p47_dive_flap_right = LoGetAircraftDrawArgumentValue(182)
            mech["speedbrakes"]["value"] = p47_dive_flap_right
            damage_vars = {
              65,81,119,146,147,148,149,150,151,152,153,154,155,156,157,158,159,161,162,163,164,213,214,215,216,217,
              223,224,225,226,227,233,234,235,236,238,240,242,243,247,265,266,267,270,271,272,273,296,297,298,299,
              429,430,431,459
            }
            -- P47 sends to TelemFFB
            stringToSend =
              string.format(
              "ActualRPM=%s",
              actualRPM
              )

          elseif string.find(obj.Name, "I-16", 0, true) then
            -------------------------------------------------------------------------------------------------------------------------------------------------------
            -- Calculate Engine RPM from redline value and engine.RPM value
            local engine_redline_reference = 2200
            local engPercent = string.format("%.3f", math.max(engine.RPM.left, engine.RPM.right))
            local actualRPM = math.floor(engine_redline_reference * (engPercent / 100))
            -- I-16 sends to TelemFFB
            damage_vars = {
              55,56,65,81,135,136,146,152,156,157,158,159,162,213,214,215,216,223,224,225,226,234,236,238,240,242,247,
              264,266,267,296,297,298,301,380,381,429,430
            }
            stringToSend =
              string.format(
              "ActualRPM=%s",
              actualRPM
            )

            elseif string.find(obj.Name, "MosquitoFBMkVI", 0, true) then
            -------------------------------------------------------------------------------------------------------------------------------------------------------
            -- Calculate Engine RPM from redline value and engine.RPM value
            local engine_redline_reference = 3000
            local engPercent = string.format("%.3f", math.max(engine.RPM.left, engine.RPM.right))
            local actualRPM = math.floor(engine_redline_reference * (engPercent / 100))
            damage_vars = {
              53,54,55,56,58,59,60,61,65,147,148,151,159,160,161,162,163,164,165,166,167,168,169,170,171,213,216,217,
              218,223,226,227,228,233,234,235,236,237,238,239,240,241,242,243,247,248,249,266,267,379,380,381,382,
              383,384,385
            }
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
            damage_vars = {
              65,81,134,135,136,144,146,147,148,150,153,154,161,167,213,214,215,216,217,219,223,224,225,226,227,229,
              233,235,237,239,241,244,247,248,249,266,267
            }

            hydraulicPressure = string.format("%.3f~%.3f", MainPanel:get_argument_value(647), MainPanel:get_argument_value(648))


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
            damage_vars = {
              65,81,134,135,136,144,146,147,148,150,153,154,161,167,213,214,215,216,217,219,223,224,225,226,227,229,
              233,235,237,239,241,244,247,248,249,266,267
            }

            hydraulicPressure = string.format("%.3f~%.3f", MainPanel:get_argument_value(647), MainPanel:get_argument_value(648))


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
            damage_vars = {
              115,116,117,134,135,136,144,145,145,146,147,148,149,150,151,152,153,154,156,157,158,160,162,162,166,167,
              168,168,169,169,170,170,183,185,187,213,214,215,216,217,218,219,223,224,225,226,227,228,229,233,234,235,
              236,244,245,246,249,253,254,254,255,256,256,257,257,259,260,260,261,261,264,265,266,267,268,289,290,291,
              292,296,298,299,300,301,301,302,303,304,305,306
            }
            -- MiG-21Bis sends to SimShaker
            stringToSend =
              string.format(
              "Flaps=%.2f;Canopy=%.1f;SPS=%.1f",
              Flaps3d,
              CanopyWarnLight,
              SPS
            )
          elseif obj.Name == "MiG-19P" then
            -------------------------------------------------------------------------------------------------------------------------------------------------------
            local stickY = -MainPanel:get_argument_value(420)
            local stickX = MainPanel:get_argument_value(421)
            damage_vars = {
              65,146,148,151,152,153,154,156,157,158,159,160,161,167,183,185,187,213,214,215,216,217,223,224,225,226,
              227,238,240,242,242,242,247,253,255,259,265,266,267,270,272,296,297,298,299,300
            }
            stringToSend =
              string.format(
              "StickX=%.3f;StickY=%.3f",
              stickX,
              stickY
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
              damage_vars = {
                65,82,134,135,136,148,149,150,152,153,154,155,156,157,158,159,183,185,213,214,214,215,216,220,222,223,
                224,224,225,226,230,232,238,240,244,245,246,248,271,296,298,299,300,301,302
              }
            -- M-2000C sends to SimShaker
            stringToSend =
              string.format(
              "PanShake=%s", PanelShake
            )
          elseif obj.Name == "AV8BNA" then
            -------------------------------------------------------------------------------------------------------------------------------------------------------
            -- AV8BNA sends to SimShaker
            damage_vars = {
              65,82,134,135,136,137,144,147,148,149,150,151,153,154,155,156,157,158,159,160,161,162,166,167,168,171,
              177,183,213,214,215,216,217,218,220,221,223,224,225,226,227,228,230,231,237,239,244,245,246,248,250,
              252,253,255,259,263,265,266,267,268,269,270,272,273,303,304,305
            }

            local AileronTrim = MainPanel:get_argument_value(473) * 0.3
            local RudderTrim = MainPanel:get_argument_value(474)
            local stickY = MainPanel:get_argument_value(701)
            local stickX = MainPanel:get_argument_value(702)

            local hp1_1 = math.floor(MainPanel:get_argument_value(553) * 10) / 10
            local hp1_2 = math.floor(MainPanel:get_argument_value(554) * 10) / 100
            local hp1_3 = math.floor(MainPanel:get_argument_value(555) * 10) / 1000
            local hp1 = hp1_1 + hp1_2 + hp1_3


            local hp2_1 =  math.floor(MainPanel:get_argument_value(556) * 10) / 10
            local hp2_2 =  math.floor(MainPanel:get_argument_value(557) * 10) / 100
            local hp2_3 =  math.floor(MainPanel:get_argument_value(558) * 10) / 1000
            local hp2 = hp2_1 + hp2_2 + hp2_3

            hydraulicPressure = string.format("%.3f~%.3f", hp1, hp2)


            -- local flt = GetDevice(28)
            -- local sflt = (dump(flt) .. dump(getmetatable(flt)) )
            -- if flt ~= nil then
            --   flt:update_arguments()
            -- end

            stringToSend =
              string.format(
              "AileronTrim=%.3f;RudderTrim=%.3f;StickX=%.3f;StickY=%.3f;dbg_recv_cmds=%s",
              AileronTrim,
              RudderTrim,
              stickX,
              stickY,
              self.dbg_recv_cmds
            )

          elseif string.find(obj.Name, "F-14", 0, true) then
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
              damage_vars = {
                65,510,513,514,515,516,517,518,519,520,521,522,524,525,526,527,530,531,532,533,534,535,536,537,
                538,539,540,541,542,543,544,545,546,547,548,549,550,551,552,553,554,555,556,557,558,559,560,561,
                562,563,564,565,566,567,568,569,570,571,574,575,576,577,578,586,587,588,589,590,591,592,593,594,
                595,596,597
              }

            hydraulicPressure = string.format("%.3f", MainPanel:get_argument_value(1063))


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
          elseif string.find(obj.Name, "FA-18", 0, true) then
            damage_vars = {
              65,135,136,137,146,148,149,150,152,152,153,154,156,157,158,160,166,183,213,214,215,216,217,220,222,223,
              224,225,226,227,230,232,233,235,241,242,242,244,245,245,247,248,265,266,267,298,299
            }
            local lr = mech.controlsurfaces.rudder.left
            local rr = mech.controlsurfaces.rudder.right
            local ar = (lr + rr) / 2
            mech["controlsurfaces"]["rudder"]["right"] = ar


          elseif string.find(obj.Name, "F-16", 0, true) then
             damage_vars = {
              65,135,136,137,146,148,152,152,153,154,156,157,158,160,183,185,213,214,215,216,220,223,224,225,226,230,
              237,238,239,240,241,242,243,247,298,299
            }
          elseif string.find(obj.Name, "F-15ESE", 0, true) then
            damage_vars = {
              65,134,136,137,146,148,149,150,152,153,154,157,158,160,161,162,163,166,167,168,169,183,213,214,215,216,
              217,223,224,225,226,227,238,240,241,243,244,246,247,248,253,255,259,265,266,267,268,298,299,428,428,
              428,428
            }
            hydraulicPressure = string.format("%.3f~%.3f", MainPanel:get_argument_value(389), MainPanel:get_argument_value(390))

          elseif string.find(obj.Name, "F-5", 0, true) then
            damage_vars = {
              65,134,135,136,146,148,152,153,154,156,157,158,159,160,161,162,163,166,167,168,169,183,185,213,214,215,
              216,217,220,223,224,225,226,227,230,238,240,242,247,265,266,267,270,272,296,297,298,299
            }
          elseif string.find(obj.Name, "F-86", 0, true) then
            damage_vars = {
              65,147,148,149,150,152,153,154,156,157,158,183,185,213,214,215,216,217,223,224,225,226,227,233,234,235,
              236,237,238,239,240,241,242,243,247,308,309,310,311,312,313,314,315
            }
          elseif string.find(obj.Name, "C-101", 0, true) then
            damage_vars = {
              65,110,134,135,136,146,150,151,167,221,223,224,225,226,227,228,229,230,231,232,233,234,235,236,237,238,
              239,241,242,243,244,249,250,252,253,254,255,256,257,261,262,263,264,265,267,269,270,271,273,400,401,402
            }
          elseif string.find(obj.Name, "MiG-15", 0, true) then
            damage_vars = {
              65,134,135,136,146,148,149,150,152,153,154,156,157,158,167,183,185,213,214,215,216,217,223,224,225,226,
              227,234,236,238,239,241,242,247,248,265,266,267
            }
          elseif string.find(obj.Name, "Mirage-F1", 0, true) then
            damage_vars = {
              65,82,134,135,136,144,145,146,147,148,150,151,152,153,154,155,156,157,158,170,171,180,181,183,185,213,
              214,214,215,216,217,218,219,220,221,222,223,224,224,225,226,227,230,231,232,237,238,239,240,244,245,
              246,248,249,250,251,265,266,267,271,296,297,298,299,400,401
            }
          elseif string.find(obj.Name, "JF-17", 0, true) then
            damage_vars = {
              65,82,134,135,136,147,148,149,150,151,152,153,154,155,156,157,158,159,160,162,166,168,183,185,187,189,
              213,214,215,216,217,218,219,220,221,222,223,224,225,226,227,228,229,230,231,232,237,238,239,240,241,
              242,243,246,247,265,266,267,271,296,298,299,300,301,302,303
            }
          elseif string.find(obj.Name, "MB-339", 0, true) then
            damage_vars = {
              65,134,135,136,146,148,149,150,152,153,154,156,157,158,159,183,213,214,215,216,217,223,224,225,226,227,
              233,235,237,238,239,240,242,246,248,265,266,267,297,298,299,663,664
            }
          elseif string.find(obj.Name, "AJS37", 0, true) then
            damage_vars = {
              65,134,135,136,148,153,154,159,216,223,225,226,227,242,246,248,271,700,701,800,801,997,998,999
            }



-------------------------------------------------------------------------------------------------------------------------------------------------------
          else -- FC3 Planes
            if obj.Name == "MiG-29A" or obj.Name == "MiG-29S" or obj.Name == "MiG-29G" then
              damage_vars = {
                146,296,297,65,298,301,265,154,153,167,161,169,163,267,266,168,162,183,185,230,220,223,213,226,216,
                224,214,231,221,225,215,228,218,244,241,243,242,240,238,248,247,159,156,148,152,134,136,135,663
              }
            elseif obj.Name == "Su-27" then
              damage_vars = {
                146,296,297,65,298,301,249,265,154,153,167,161,169,163,267,266,168,162,183,223,213,231,221,224,214,
                225,215,228,218,244,241,243,242,240,238,248,247,159,156,148,152,134,136,135
              }
            elseif obj.Name == "Su-33" then
              damage_vars = {
                146,296,297,65,298,301,249,265,154,153,167,161,169,163,267,266,168,162,183,252,250,223,213,
                226,216,231,221,224,214,229,219,230,220,225,215,228,218,244,241,243,242,235,233,236,234,
                239,237,240,238,248,247,159,156,148,152,134,136,135,135
              }
            elseif obj.Name == "Su-25" then
              damage_vars = {
                146,296,297,65,134,153,167,161,266,135,267,136,226,216,225,215,228,218,242,243,236,234,240,247,248,81
              }
            elseif obj.Name == "Su-25t" then
              damage_vars = {
                146,296,297,298,299,65,147,150,149,265,134,154,153,303,167,161,169,163,266,135,267,136,168,162,187,183,
                230,220,223,213,226,216,231,221,224,214,227,217,232,222,225,215,228,218,241,242,243,235,233,236,234,
                239,237,240,238,248,247,81
              }
            elseif obj.Name == "A-10A" then
              damage_vars = {
                146,65 ,150,147,249,154,153,167,161,267,266,223,213,226,216,224,214,229,219,225,215,227,217,244,241,
                235,233,239,237,248,247,81,148,144,134,136,135
              }
            elseif obj.Name == "F-15C" or obj.Name == "F-15E" or obj.Name == "F-15" then
              damage_vars = {
                146,296,297,65 ,298,301,249,265,154,153,167,161,169,163,267,266,168,162,183,223,213,226,216,224,214,225,
                215,228,218,244,241,243,242,240,238,248,247,158,157,148,147,152
              }

            end

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
            log.info("TELEMFFB TREATING THIS AS AN FC3 AIRCRAFT: >" .. obj.Name .. "<")
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
          if calc_damage == 1 and damage_vars ~= "not supported" then
            damage = getDamage(damage_vars)
          end
          local items = {
            {"T", "%.3f", t},
            {"N", "%s", obj.Name},
            {"src", "%s", "DCS"},
            {"SelfData", "%s", myselfData},
            {"EngRPM", "%s", engineRPM},
            {"HydSys", "%s", hydraulicPressure},
            {"HydPress", "%s", hydraulicPressureSim},
            {"ACCs", "%s", AccelerationUnits},
            {"Gun", "%s", CannonShells},
            {"Wind", "%s", windVelocityVectors},
            {"VlctVectors", "%s", velocityVectors},
            {"VerticalSpeed", "%s", vertical_speed},
            {"altASL", "%.2f", altAsl},
            {"altAgl", "%.2f", altAgl},
            {"AoA", "%.2f", aoa},
            {"IAS", "%.2f", IAS},
            {"IAS_kt", "%.2f", IAS * 1.944},
            {"TAS_raw", "%.2f", tas},
            {"TAS", "%.2f", calculated_TAS},
            {"TAS_incidence", "%.2f", calculated_TAS},
            {"TAS_raw_kt", "%.2f", tas * 1.944},
            {"TAS_incidence_kt", "%.2f", calculated_TAS * 1.944},
            {"WeightOnWheels", "%s", WoW},
            {"SimOnGround", "%s", tostring(on_ground)},
            {"Flares", "%s", CM.flare},
            {"Chaff", "%s", CM.chaff},
            {"PayloadInfo", "%s", PayloadInfo},
            {"Mach", "%.4f", M_number},
            {"MechInfo", "%s", JSON:encode(mech):gsub("\n", "")},
            {"Afterburner", "%s", AB},
            {"DynamicPressure", "%.3f", DynamicPressure},
            {"AirDensity", "%.3f", AirDensity},
            {"Incidence", "%s", incidence},  -- relative airstream in body frame
            {"CAlpha", "%.3f", calc_alpha},
            {"CBeta", "%.3f", calc_beta}, -- sideslip angle deg
            {"RelWind", "%s", rel_wind}, --wind in body frame
            {"Damage", "%s", damage},
            {"Wind_Kts", "%.2f", wind_velocity * 1.944},
            {"Wind_direction", "%.0f", wind_direction},
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

          socket.try(self.sock_udp:send(stringToSend))
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
          socket.try(self.sock_udp:send("Ev=Stop"))
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
    log.info("TELEMFFB Export Script starting...")

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
    log.info("TELEMFFB Export Script stopping...")
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