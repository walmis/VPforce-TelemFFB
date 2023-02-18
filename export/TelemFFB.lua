local JSON = loadfile("Scripts\\JSON.lua")()

local f_telemFFB = {
  Start = function(self)
    package.path = package.path .. ";.\\LuaSocket\\?.lua"
    package.cpath = package.cpath .. ";.\\LuaSocket\\?.dll"
    socket = require("socket")
    
    local connect_init =
      socket.protect(
      function()
        --host = "127.0.0.1"
        --port1=29375
        self.sock_udp = socket.try(socket.udp())
        socket.try(self.sock_udp:settimeout(.001))
        socket.try(self.sock_udp:setoption('broadcast', true))
        socket.try(self.sock_udp:setpeername("127.255.255.255", 34380))
        socket.try(self.sock_udp:send("CONNECT"))
      end
    )
    connect_init()
  end,
  AfterNextFrame = function(self)
    local data_send =
      socket.protect(
      function()
        if self.sock_udp then
          local t = LoGetModelTime()
          local altAsl = LoGetAltitudeAboveSeaLevel()
          local altAgl = LoGetAltitudeAboveGroundLevel()
          local pitch, bank, yaw = LoGetADIPitchBankYaw()
          local aoa = LoGetAngleOfAttack()
          local acceleration = LoGetAccelerationUnits()
          local AccelerationUnits = "0.00~0.00~0.00"
          local IAS = LoGetIndicatedAirSpeed() -- m/s
          local M_number = LoGetMachNumber()

          local LeftGear = LoGetAircraftDrawArgumentValue(6)
          local NoseGear = LoGetAircraftDrawArgumentValue(1)
          local RightGear = LoGetAircraftDrawArgumentValue(4)

          local WoW = string.format("%.2f~%.2f~%.2f", LeftGear, NoseGear, RightGear)

          local mech = JSON:encode(LoGetMechInfo()):gsub("\n", "")


          if acceleration then
            AccelerationUnits = string.format("%.2f~%.2f~%.2f", acceleration.x, acceleration.y, acceleration.z)
          end

          local obj = LoGetSelfData()
          local myselfData

          if obj then
            myselfData = string.format("%.2f~%.2f~%.2f", obj.Heading, obj.Pitch, obj.Bank)
          end

          local vectorVel = LoGetVectorVelocity()
          if type(vectorVel) == "function" then
            do
              return
            end
          end

          local velocityVectors = string.format("%.2f~%.2f~%.2f", vectorVel.x, vectorVel.y, vectorVel.z)
          local wind = LoGetVectorWindVelocity()
          local windVelocityVectors =
            string.format(
            "%.2f~%.2f~%.2f",
            wind.x,
            wind.y,
            wind.z
          )
          local tas = LoGetTrueAirSpeed()
          local CM = LoGetSnares()
          local MainPanel = GetDevice(0)

          if MainPanel ~= nil then
            MainPanel:update_arguments()
          end

          local engine = LoGetEngineInfo()
          local CannonShells = LoGetPayloadInfo().Cannon.shells
          local stations = LoGetPayloadInfo().Stations
          local PayloadInfo = "empty"
          local temparray = {}

          for i_st, st in pairs(stations) do
            temparray[#temparray + 1] =
              string.format(
              "%d%d%d%d*%d",
              st.weapon.level1,
              st.weapon.level2,
              st.weapon.level3,
              st.weapon.level4,
              st.count
            )
            PayloadInfo = table.concat(temparray, "~")
          end

          local stringToSend

          -------------------------------------------------------------------------------------------------------------------------------------------------------
          if obj.Name == "Mi-8MT" then
            -------------------------------------------------------------------------------------------------------------------------------------------------------
            local mainRotorRPM = MainPanel:get_argument_value(42) * 100
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
              "T=%.3f;N=%s;SelfData=%s;RotorRPM=%.0f;VlctVectors=%s;altAgl=%.2f;PanShake=%s;d=0;ACCs=%s;d=0;IAS=%.2f;Wind=%s;altASL=%.2f;14d=0;Payload=%s;d=0;d=0;d=0;d=0;d=0;TAS=%.2f;Flares=%s",
              t,
              obj.Name,
              myselfData,
              mainRotorRPM,
              velocityVectors,
              altAgl,
              PanelShake,
              AccelerationUnits,
              IAS,
              windVelocityVectors,
              altAsl,
              PayloadInfo,
              tas,
              CM.flare
            )
          elseif obj.Name == "UH-1H" then
            -------------------------------------------------------------------------------------------------------------------------------------------------------
            local mainRotorRPM = MainPanel:get_argument_value(123) * 100
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
              "T=%.3f;N=%s;SelfData=%s;RotorRPM=%.0f;VlctVectors=%s;altAgl=%.2f;PanShake=%s;Shells=%i;ACCs=%s;IAS=%.2f;d=0;Wind=%s;altASL=%.2f;14d=0;PayloadInfo=%s;16d=0;17d=0;18_LDoor=%.2f;19_RDoor=%.2f;20_deadPilot=%.2f;TAS=%.2f;Flares=%s",
              t,
              obj.Name,
              myselfData,
              mainRotorRPM,
              velocityVectors,
              altAgl,
              PanelShake,
              CannonShells,
              AccelerationUnits,
              IAS,
              windVelocityVectors,
              altAsl,
              PayloadInfo,
              leftDoor,
              rightDoor,
              deadPilot,
              tas,
              CM.flare
            )
          elseif obj.Name == "Ka-50" then
            -------------------------------------------------------------------------------------------------------------------------------------------------------
            local mainRotorRPM = MainPanel:get_argument_value(52) * 100

            local GunTrigger = MainPanel:get_argument_value(615)
            local APUoilP = MainPanel:get_argument_value(168)
            local APUvalve = MainPanel:get_argument_value(162)
            local APU = string.format("%.1f~%.1f", APUvalve, APUoilP)
            -- Ka-50  sends to SimShaker
            stringToSend =
              string.format(
              "T=%.3f;N=%s;SelfData=%s;RotorRPM=%.0f;VlctVectors=%s;altAgl=%.2f;PanShake=%s;Gun=%s;ACCs=%s;IAS=%.2f;d=0;Wind=%s;13_altASL=%.2f;14d=0;PayloadInfo=%s;16d=0;17d=0;18d=0;19d=0;20_APU=%s;TAS=%.2f;Flares=%s;Chaff=%s",
              t,
              obj.Name,
              myselfData,
              mainRotorRPM,
              velocityVectors,
              altAgl,
              "000",
              CannonShells,
              -- GunTrigger,
              AccelerationUnits,
              IAS,
              windVelocityVectors,
              altAsl,
              PayloadInfo,
              APU,
              tas,
              CM.flare,
              CM.chaff
            )
          elseif
            obj.Name == "SA342M" or obj.Name == "SA342L" or obj.Name == "SA342Mistral" or obj.Name == "SA342Minigun"
           then -- Gazelle
            -------------------------------------------------------------------------------------------------------------------------------------------------------
            local mainRotorRPM = MainPanel:get_argument_value(52) * 100
            local RAltimeterMeter = MainPanel:get_argument_value(94) * 1000
            local RAltimeterOnOff = MainPanel:get_argument_value(91)
            local RAltimeterFlagPanne = MainPanel:get_argument_value(98)
            local RAltimeterFlagMA = MainPanel:get_argument_value(999)
            local RAltimeterTest = MainPanel:get_argument_value(100)
            local StatusString =
              RAltimeterOnOff .. "~" .. RAltimeterFlagPanne .. "~" .. RAltimeterFlagMA .. "~" .. RAltimeterTest
            -- Gazelle  sends to SimShaker
            stringToSend=string.format("T=%.3f;N=%s;SelfData=%s;RotorRPM=%.0f;VlctVectors=%s;altAgl=%.2f;PanShake=%s;Shells=%i;ACCs=%s;IAS=%.2f;d=0;Wind=%s;altASL=%.2f;14_RadarAltimeterMeter=%.2f;PayloadInfo=%s;16_RAltimeterStatus=%s;17d=0;18d=0;19d=0;20d=0;21_TAS=%.2f;Flares=%s",t,obj.Name,myselfData,mainRotorRPM,velocityVectors,altAgl,"000",CannonShells,AccelerationUnits,IAS,windVelocityVectors,altAsl,RAltimeterMeter,PayloadInfo, StatusString,tas, CM.flare)

-------------------------------------------------------------------------------------------------------------------------------------------------------
          elseif obj.Name == "P-51D" or obj.Name == "P-51D-30-NA" then
            
            local AirspeedNeedle = MainPanel:get_argument_value(11)*1000*1.852
            local Altimeter_10000_footPtr = MainPanel:get_argument_value(96)*100000
            local Altimeter_1000_footPtr = MainPanel:get_argument_value(24)*10000
            local Altimeter_100_footPtr = MainPanel:get_argument_value(25)*1000
            local Variometer = MainPanel:get_argument_value(29)
            local TurnNeedle = MainPanel:get_argument_value(27) * math.rad(3)
            local Landing_Gear_Handle = MainPanel:get_argument_value(150)
            local Manifold_Pressure = MainPanel:get_argument_value(10) * 65 + 10
            local Engine_RPM = MainPanel:get_argument_value(23) * 4500
            local AHorizon_Pitch = MainPanel:get_argument_value(15) * math.pi / 3.0
            local AHorizon_Bank = MainPanel:get_argument_value(14) * math.pi
            local AHorizon_PitchShift = MainPanel:get_argument_value(16) * 10.0 * math.pi / 180.0
            local GyroHeading = MainPanel:get_argument_value(12) * 2.0 * math.pi
            local Oil_Temperature = MainPanel:get_argument_value(30) * 100
            local Oil_Pressure = MainPanel:get_argument_value(31) * 200
            local Fuel_Pressure = MainPanel:get_argument_value(32) * 25

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
              "T=%.3f;N=%s;SelfData=%s;EngRPM=%.0f;VlctVectors=%s;altAgl=%.2f;PanShake=%s;CannonShells=%.0f;ACCs=%s;GreenLight=%.1f;11_MP-WEP=%.2f~%.2f;Wind=%s;altASL=%.2f;AoA=%.1f;PayloadInfo=%s;16d=0;17d=0;18d=0;19d=0;20d=0;TAS=%.2f",
              t,
              obj.Name,
              myselfData,
              Engine_RPM,
              velocityVectors,
              altAgl,
              PanelShake,
              CannonShells,
              AccelerationUnits,
              LandingGearGreenLight,
              Manifold_Pressure,
              WEPwire,
              windVelocityVectors,
              altAsl,
              aoa,
              PayloadInfo,
              tas
            )
          elseif obj.Name == "TF-51D" then
            -------------------------------------------------------------------------------------------------------------------------------------------------------
            local AirspeedNeedle = MainPanel:get_argument_value(11) * 1000 * 1.852
            local Altimeter_10000_footPtr = MainPanel:get_argument_value(96) * 100000
            local Altimeter_1000_footPtr = MainPanel:get_argument_value(24) * 10000
            local Altimeter_100_footPtr = MainPanel:get_argument_value(25) * 1000
            local Variometer = MainPanel:get_argument_value(29)
            local TurnNeedle = MainPanel:get_argument_value(27) * math.rad(3)
            local Landing_Gear_Handle = MainPanel:get_argument_value(150)
            local Manifold_Pressure = MainPanel:get_argument_value(10) * 65 + 10
            local Engine_RPM = MainPanel:get_argument_value(23) * 4500
            local AHorizon_Pitch = MainPanel:get_argument_value(15) * math.pi / 3.0
            local AHorizon_Bank = MainPanel:get_argument_value(14) * math.pi
            local AHorizon_PitchShift = MainPanel:get_argument_value(16) * 10.0 * math.pi / 180.0
            local GyroHeading = MainPanel:get_argument_value(12) * 2.0 * math.pi
            local Oil_Temperature = MainPanel:get_argument_value(30) * 100
            local Oil_Pressure = MainPanel:get_argument_value(31) * 200
            local Fuel_Pressure = MainPanel:get_argument_value(32) * 25
            local Coolant_Temperature = MainPanel:get_argument_value(22) * 230 - 80
            local Carb_Temperature = MainPanel:get_argument_value(21) * 230 - 80
            local LandingGearGreenLight = MainPanel:get_argument_value(80)
            local LandingGearRedLight = MainPanel:get_argument_value(82)
            local Vacuum_Suction = MainPanel:get_argument_value(9) * 10
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
            local extModelArguments =
              string.format(
              "%.2f~%.2f~%.2f~%.2f",
              LoGetAircraftDrawArgumentValue(0),
              LoGetAircraftDrawArgumentValue(1),
              LoGetAircraftDrawArgumentValue(5),
              LoGetAircraftDrawArgumentValue(6)
            )
            -- TF-51 sends to SimShaker
            stringToSend =
              string.format(
              "T=%.3f;N=%s;SelfData=%s;EngRPM=%.0f;VlctVectors=%s;altAgl=%.2f;PanShake=%s;Gun=%s;ACCs=%s;GreenLight=%.1f;MP-WEP=%.2f~%.2f;Wind=%s;altASL=%.2f;AoA=%.1f;15d=0;16d=0;17d=0;18d=0;19d=0;20d=0;TAS=%.2f",
              t,
              obj.Name,
              myselfData,
              Engine_RPM,
              velocityVectors,
              altAgl,
              PanelShake,
              "000",
              AccelerationUnits,
              LandingGearGreenLight,
              Manifold_Pressure,
              WEPwire,
              windVelocityVectors,
              altAsl,
              aoa,
              tas
            )
          elseif obj.Name == "FW-190D9" then
            -------------------------------------------------------------------------------------------------------------------------------------------------------
            local Manifold_Pressure = MainPanel:get_argument_value(46)
            local Engine_RPM = MainPanel:get_argument_value(47)

            local PanelShake =
              string.format(
              "%.2f~%.2f~%.2f",
              MainPanel:get_argument_value(205),
              MainPanel:get_argument_value(204),
              MainPanel:get_argument_value(206)
            )
            local GunFireData =
              string.format(
              "%.2f~%.2f~%.2f~%.2f",
              MainPanel:get_argument_value(50),
              MainPanel:get_argument_value(164),
              MainPanel:get_argument_value(165),
              MainPanel:get_argument_value(166)
            )
            local MW = MainPanel:get_argument_value(106)
            -- FW-190D9 sends to SimShaker
            stringToSend =
              string.format(
              "T=%.3f;N=%s;SelfData=%s;EngineRPM=%.2f;VlctVectors=%s;altAgl=%.2f;PanShake=%s;Gun=%s;ACCs=%s;Grn-Red-Lights=0;MP-MW=%.2f~%.2f;Wind=%s;altASL=%.2f;14_AoA=%.1f;PayloadInfo=%s;16d=0;17d=0;18d=0;19d=0;20d=0;TAS=%.2f",
              t,
              obj.Name,
              myselfData,
              Engine_RPM,
              velocityVectors,
              altAgl,
              PanelShake,
              GunFireData,
              AccelerationUnits,
              Manifold_Pressure,
              MW,
              windVelocityVectors,
              altAsl,
              aoa,
              PayloadInfo,
              tas
            )
          elseif obj.Name == "Bf-109K-4" then
            -------------------------------------------------------------------------------------------------------------------------------------------------------
            local Manifold_Pressure = MainPanel:get_argument_value(32)
            local Engine_RPM = MainPanel:get_argument_value(29)

            local myselfData = string.format("%.2f~%.2f~%.2f", obj.Heading, obj.Pitch, obj.Bank)
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
              "T=%.3f;N=%s;SelfData=%s;EngineRPM=%.2f;VlctVectors=%s;altAgl=%.2f;PanShake=%s;Gun=%i;9_ACCs=%s;10_Grn-Red-Lights=0;11_MP-MW=%.2f~%.2f;Wind=%s;altASL=%.2f;14_AoA=%.1f;PayloadInfo=%s;16d=0;17d=0;18d=0;19d=0;20d=0;TAS=%.2f",
              t,
              obj.Name,
              myselfData,
              Engine_RPM,
              velocityVectors,
              altAgl,
              PanelShake,
              CannonShells,
              AccelerationUnits,
              Manifold_Pressure,
              MW,
              windVelocityVectors,
              altAsl,
              aoa,
              PayloadInfo,
              tas
            )
          elseif obj.Name == "SpitfireLFMkIX" or obj.Name == "SpitfireLFMkIXCW" then
            -------------------------------------------------------------------------------------------------------------------------------------------------------
            local Engine_RPM = MainPanel:get_argument_value(37)
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
              "T=%.3f;N=%s;SelfData=%s;EngRPM=%.0f;VlctVectors=%s;altAgl=%.2f;PanShake=%s;CannonShells=%.0f;ACCs=%s;d10=0;d11=0~0;Wind=%s;altASL=%.2f;AoA=%.1f;PayloadInfo=%s;16d=0;17d=0;18d=0;19d=0;20d=0;TAS=%.2f",
              t,
              obj.Name,
              myselfData,
              Engine_RPM,
              velocityVectors,
              altAgl,
              PanelShake,
              CannonShells,
              AccelerationUnits,
              windVelocityVectors,
              altAsl,
              aoa,
              PayloadInfo,
              tas
            )
          elseif obj.Name == "F-86F Sabre" then
            ------------------------------------------------------------------------------------------------------------------------
            local Engine_RPM = MainPanel:get_argument_value(16) * 100
            -- F-86F sends to SimShaker
            stringToSend =
              string.format(
              "T=%.3f;N=%s;SelfData=%s;EngineRPM=%.0f;VlctVectors=%s;altAgl=%.2f;PanShake=%s;Gun=%s;ACCs=%s;Gr-RedLight=0;11d=0;Wind=%s;altASL=%.2f;AoA=%.2f;PayloadInfo=%s;16d=0;17d=0;18d=0;19d=0;20d=0;TAS=%.2f",
              t,
              obj.Name,
              myselfData,
              Engine_RPM,
              velocityVectors,
              altAgl,
              "000",
              CannonShells,
              AccelerationUnits,
              windVelocityVectors,
              altAsl,
              aoa,
              PayloadInfo,
              tas
            )
          elseif obj.Name == "A-10C" then
            -------------------------------------------------------------------------------------------------------------------------------------------------------
            local FlapsPos = MainPanel:get_argument_value(653)
            local Canopy = MainPanel:get_argument_value(7)
            local Engine_RPM_left = string.format("%.0f", MainPanel:get_argument_value(78) * 100)
            local Engine_RPM_right = string.format("%.0f", MainPanel:get_argument_value(80) * 100)
            local APU = MainPanel:get_argument_value(13)
            -- A-10C  sends to SimShaker
            stringToSend =
              string.format(
              "T=%.3f;N=%s;SelfData=%s;EngRPM=%s;VlctVectors=%s;altAgl=%.2f;PanShake=%s;GunShells=%.0f;ACCs=%s;LG=%s;DragChute=%s;Wind=%s;altASL=%.2f;AoA=%.2f;Payload=%s;Flaps=%.2f;d=0;18_Canopy=%.2f;19d=0;APU=%.2f;TAS=%.2f;Flares=%s;Chaff=%s",
              t,
              obj.Name,
              myselfData,
              Engine_RPM_left .. "~" .. Engine_RPM_right,
              velocityVectors,
              altAgl,
              "000",
              CannonShells,
              AccelerationUnits,
              "0",
              "000",
              windVelocityVectors,
              altAsl,
              aoa,
              PayloadInfo,
              FlapsPos,
              Canopy,
              APU,
              tas,
              CM.flare,
              CM.chaff
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
            local Engine_RPM = string.format("%.0f", MainPanel:get_argument_value(50) * 100)
            -- MiG-21Bis sends to SimShaker
            stringToSend =
              string.format(
              "T=%.3f;N=%s;SelfData=%s;EngRPM=%s;VlctVectors=%s;altAgl=%.2f;PanShake=%s;Gun=%s;ACCs=%s;11d=0;d=0;Wind=%s;altASL=%.2f;AoA=%.2f;Payload=%s;Flaps=%.2f;d=0;Canopy=%.1f;19_SPS=%.1f;20d=0;TAS=%.2f;Flares=%s;Chaff=%s",
              t,
              obj.Name,
              myselfData,
              Engine_RPM,
              velocityVectors,
              altAgl,
              "000",
              CannonShells,
              AccelerationUnits,
              windVelocityVectors,
              altAsl,
              aoa,
              PayloadInfo,
              Flaps3d,
              CanopyWarnLight,
              SPS,
              tas,
              CM.flare,
              CM.chaff
            )
          elseif obj.Name == "MiG-15bis" then
            -------------------------------------------------------------------------------------------------------------------------------------------------------
            local VoltAmperMeter = MainPanel:get_argument_value(83) * 30
            local GenOff = MainPanel:get_argument_value(57)
            local TestBtn = MainPanel:get_argument_value(72) -- not presented in mainpanel_init.lua

            local Engine_RPM = string.format("%.0f", MainPanel:get_argument_value(42) * 100)
            local SpeedBrakeLamp = MainPanel:get_argument_value(124)
            local Canopy = MainPanel:get_argument_value(225)
            -- MiG-15Bis sends to SimShaker
            stringToSend =
              string.format(
              "T=%.3f;N=%s;SelfData=%s;EngineRPM=%i;VlctVectors=%s;altAgl=%.2f;PanShake=0;Gun=%s;ACCs=%s;Gr-RedLight=%s;d=0;Wind=%s;altASL=%.2f;AoA=%.2f;Payload=%s;Flaps=0;17d=0;18_Canopy=%.1f;19d=0;20d=0;TAS=%.2f",
              t,
              obj.Name,
              myselfData,
              Engine_RPM,
              velocityVectors,
              altAgl,
              CannonShells,
              AccelerationUnits,
              "0",
              windVelocityVectors,
              altAsl,
              aoa,
              PayloadInfo,
              Canopy,
              tas
            )
          elseif obj.Name == "Hawk" then
            -------------------------------------------------------------------------------------------------------------------------------------------------------
            -- Hawk  sends to SimShaker

            stringToSend =
              string.format(
              "T=%.3f;N=%s;SelfData=%s;EngRPM=%s;VlctVectors=%s;altAgl=%.2f;PanShake=%s;Gun=%s;ACCs=%s;10_Gr-RedLight=%s;d=0;Wind=%s;d=0;AoA=%.2f;PayloadInfo=%s;d=0;d=0;d=0;d=0;d=0;TAS=%.2f",
              t,
              obj.Name,
              myselfData,
              "Engine_RPM=00",
              velocityVectors,
              altAgl,
              "000",
              "CannonShells=000",
              AccelerationUnits,
              "Gr-RedLight=0",
              windVelocityVectors,
              aoa,
              PayloadInfo,
              tas
            )
          elseif obj.Name == "C-101EB" then
            -------------------------------------------------------------------------------------------------------------------------------------------------------
            -- C-101EB  sends to SimShaker

            stringToSend =
              string.format(
              "T=%.3f;N=%s;SelfData=%s;EngRPM=%s;VlctVectors=%s;altAgl=%.2f;PanShake=%s;Gun=%s;ACCs=%s;10_Gr-RedLight=%s;d=0;Wind=%s;d=0;AoA=%.2f;PayloadInfo=%s;d=0;d=0;d=0;d=0;d=0;TAS=%.2f",
              t,
              obj.Name,
              myselfData,
              "Engine_RPM=00",
              velocityVectors,
              altAgl,
              "000",
              "CannonShells=000",
              AccelerationUnits,
              "Gr-RedLight=0",
              windVelocityVectors,
              aoa,
              PayloadInfo,
              tas
            )
          elseif obj.Name == "L-39C" or obj.Name == "L-39ZA" then
            -------------------------------------------------------------------------------------------------------------------------------------------------------
            local TestBtn = MainPanel:get_argument_value(203) -- not presented in mainpanel_init.lua
            local TestBtn2 = MainPanel:get_argument_value(538) -- not presented inmainpanel_init.lua

            local Canopy1 = MainPanel:get_argument_value(139)
            local Canopy2 = MainPanel:get_argument_value(140)

            local Engine_RPM = string.format("%.0f", MainPanel:get_argument_value(84) * 100)
            stringToSend =
              string.format(
              "T=%.3f;N=%s;SelfData=%s;EngRPM=%s;VlctVectors=%s;altAgl=%.2f;PanShake=%s;Gun=%s;ACCs=%s;Gr-RedLight=0;d=0;Wind=%s;altASL=%.2f;AoA=%.2f;PayloadInfo=%s;16d=0;17d=0;18Canopy1=%.2f;19Canopy2=%.2f;20d=0;TAS=%.2f",
              t,
              obj.Name,
              myselfData,
              Engine_RPM,
              velocityVectors,
              altAgl,
              "000",
              CannonShells,
              AccelerationUnits,
              windVelocityVectors,
              altAsl,
              aoa,
              PayloadInfo,
              Canopy1,
              Canopy2,
              tas
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
              "T=%.3f;N=%s;SelfData=%s;d=0.0f;VlctVectors=%s;altAgl=%.2f;PanShake=%s;Gun=%s;ACCs=%s;d10=0;11d=0;Wind=%s;altASL=%.2f;AoA=%.2f;PayloadInfo=%s;16d=0;M=%.4f;18d=0;19d=0;20d=0;TAS=%.2f;Flares=%s;Chaff=%s",
              t,
              obj.Name,
              myselfData,
              velocityVectors,
              altAgl,
              PanelShake,
              CannonShells,
              AccelerationUnits,
              windVelocityVectors,
              altAsl,
              aoa,
              PayloadInfo,
              M_number,
              tas,
              CM.flare,
              CM.chaff
            )
          elseif obj.Name == "F-5E" or obj.Name == "F-5E-3" then
            -------------------------------------------------------------------------------------------------------------------------------------------------------
            local Engine_RPM_left = MainPanel:get_argument_value(16) * 100
            local Engine_RPM_right = MainPanel:get_argument_value(17) * 100
            -- F-5E sends to SimShaker
            stringToSend =
              string.format(
              "T=%.3f;N=%s;SelfData=%s;Engine_RPM=%s;VlctVectors=%s;altAgl=%.2f;PanShake=%s;Gun=%s;ACCs=%s;d10=0;11d=0;Wind=%s;altASL=%.2f;AoA=%.2f;PayloadInfo=%s;16d=0;M=%.4f;18d=0;19d=0;20d=0;TAS=%.2f;Flares=%s;Chaff=%s",
              t,
              obj.Name,
              myselfData,
              Engine_RPM_left .. "~" .. Engine_RPM_right,
              velocityVectors,
              altAgl,
              "000",
              CannonShells,
              AccelerationUnits,
              windVelocityVectors,
              altAsl,
              aoa,
              PayloadInfo,
              M_number,
              tas,
              CM.flare,
              CM.chaff
            )
          elseif obj.Name == "AJS37" then
            -------------------------------------------------------------------------------------------------------------------------------------------------------
            local Engine_RPM = MainPanel:get_argument_value(139) * 100
            -- AJS37 sends to SimShaker
            stringToSend =
              string.format(
              "T=%.3f;N=%s;SelfData=%s;Engine_RPM=%.2f;VlctVectors=%s;altAgl=%.2f;PanShake=%s;Gun=%s;ACCs=%s;d10=0;11d=0;Wind=%s;altASL=%.2f;AoA=%.2f;PayloadInfo=%s;16d=0;M=%.4f;18d=0;19d=0;20d=0;TAS=%.2f;Flares=%s;Chaff=%s",
              t,
              obj.Name,
              myselfData,
              Engine_RPM,
              velocityVectors,
              altAgl,
              "000",
              CannonShells,
              AccelerationUnits,
              windVelocityVectors,
              altAsl,
              aoa,
              PayloadInfo,
              M_number,
              tas,
              CM.flare,
              CM.chaff
            )
          elseif obj.Name == "AV8BNA" then
            -------------------------------------------------------------------------------------------------------------------------------------------------------
            -- AV8BNA sends to SimShaker
            stringToSend =
              string.format(
              "T=%.3f;N=%s;SelfData=%s;Engine_RPM=%.2f;VlctVectors=%s;altAgl=%.2f;PanShake=%s;Gun=%s;ACCs=%s;d10=0;IAS=%.2f;Wind=%s;altASL=%.2f;AoA=%.2f;PayloadInfo=%s;16d=0;M=%.4f;18d=0;19d=0;20d=0;TAS=%.2f;Flares=%s;Chaff=%s",
              t,
              obj.Name,
              myselfData,
              "0",
              velocityVectors,
              altAgl,
              "000",
              CannonShells,
              AccelerationUnits,
              IAS,
              windVelocityVectors,
              altAsl,
              aoa,
              PayloadInfo,
              M_number,
              tas,
              CM.flare,
              CM.chaff
            )
          elseif obj.Name == "FA-18C_hornet" then
            -------------------------------------------------------------------------------------------------------------------------------------------------------
            -- let's try some dcs bios magic
            local li = parse_indication(5)
      if RPM == nil then 
        RPM = "0~0"
      end
      
			if li then
              local LEngine_RPM = check(li.txt_RPM_L)
              local REngine_RPM = check(li.txt_RPM_R)
              if (string.len(LEngine_RPM) >=  2) and (string.len(REngine_RPM) >=  2)  then
                RPM = LEngine_RPM.."~"..REngine_RPM
              end
			end
			
            -- FA-18C sends to SimShaker
            stringToSend=string.format("T=%.3f;N=%s;SelfData=%s;Engine_RPM=%s;VlctVectors=%s;altAgl=%.2f;PanShake=%s;Gun=%s;ACCs=%s;d10=0;IAS=%.2f;Wind=%s;altASL=%.2f;AoA=%.2f;PayloadInfo=%s;16d=0;M=%.4f;18d=0;19d=0;20d=0;TAS=%.2f;Flares=%s;Chaff=%s", t, obj.Name, myselfData, RPM,  velocityVectors, altAgl, "000", CannonShells, AccelerationUnits, IAS, windVelocityVectors, altAsl, aoa, PayloadInfo, M_number, tas, CM.flare, CM.chaff)

            -- FA-18C sends to SimShaker
            stringToSend =
              string.format(
              "T=%.3f;N=%s;SelfData=%s;Engine_RPM=%s;VlctVectors=%s;altAgl=%.2f;PanShake=%s;Gun=%s;ACCs=%s;d10=0;IAS=%.2f;Wind=%s;altASL=%.2f;AoA=%.2f;PayloadInfo=%s;16d=0;M=%.4f;18d=0;19d=0;20d=0;TAS=%.2f;Flares=%s;Chaff=%s",
              t,
              obj.Name,
              myselfData,
              RPM,
              velocityVectors,
              altAgl,
              "000",
              CannonShells,
              AccelerationUnits,
              IAS,
              windVelocityVectors,
              altAsl,
              aoa,
              PayloadInfo,
              M_number,
              tas,
              CM.flare,
              CM.chaff
            )
          elseif string.find(obj.Name, "F-14") then
            -------------------------------------------------------------------------------------------------------------------------------------------------------
            --local sensor_data = obj.get_base_data()
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
			if f14 == true then
              -- usual case after first time
              additionalData=""
              local epoxy = GetDevice(6)
              if epoxy ~= nil and type(epoxy) ~= "number" and f14_i2n ~= nil then
                local values = epoxy:get_values()
                for i, v in ipairs(values) do
                  f14_variables[f14_i2n[i]] = v
                  additionalData = additionalData .. f14_i2n[i] .. "=" .. v .. ";"
                end
              end
             -- log.info("additionalData:"..additionalData)
	        else
              additionalData=""  -- prevent nil error in string.format below at least
            end

            -- F-14 sends to SimShaker
            stringToSend =
              string.format(
              "T=%.3f;N=%s;SelfData=%s;Engine_RPM=%s;VlctVectors=%s;altAgl=%.2f;PanShake=%s;Gun=%s;ACCs=%s;d10=0;IAS=%.2f;Wind=%s;altASL=%.2f;AoA=%.2f;PayloadInfo=%s;16d=0;M=%.4f;18d=0;19d=0;20d=0;TAS=%.2f;Flares=%s;Chaff=%s;%s",
              t,
              obj.Name,
              myselfData,
              RPM,
              velocityVectors,
              altAgl,
              "000",
              CannonShells,
              AccelerationUnits,
              IAS,
              windVelocityVectors,
              altAsl,
              aoa,
              PayloadInfo,
              M_number,
              tas,
              CM.flare,
              CM.chaff,
              additionalData
            )
          elseif obj.Name == "Yak-52" then
            -------------------------------------------------------------------------------------------------------------------------------------------------------
            -- YAK-52 sends to SimShaker
            stringToSend=string.format("T=%.3f;N=%s;SelfData=%s;Engine_RPM=%.2f;VlctVectors=%s;altAgl=%.2f;PanShake=%s;Gun=%s;ACCs=%s;d10=0;IAS=%.2f;Wind=%s;altASL=%.2f;AoA=%.2f;PayloadInfo=%s;16d=0;M=%.4f;18d=0;19d=0;20d=0;TAS=%.2f;Flares=%s;Chaff=%s", t, obj.Name, myselfData, "0",  velocityVectors, altAgl, "000", CannonShells, AccelerationUnits, IAS, windVelocityVectors, altAsl, aoa, PayloadInfo, M_number, tas, CM.flare, CM.chaff)

-------------------------------------------------------------------------------------------------------------------------------------------------------
          elseif obj.Name == "Christen Eagle II" then

          -- Christen Eagle II sends to SimShaker
          stringToSend=string.format("T=%.3f;N=%s;SelfData=%s;Engine_RPM=%.2f;VlctVectors=%s;altAgl=%.2f;PanShake=%s;Gun=%s;ACCs=%s;d10=0;IAS=%.2f;Wind=%s;altASL=%.2f;AoA=%.2f;PayloadInfo=%s;16d=0;M=%.4f;18d=0;19d=0;20d=0;TAS=%.2f;Flares=%s;Chaff=%s", t, obj.Name, myselfData, "0",  velocityVectors, altAgl, "000", CannonShells, AccelerationUnits, IAS, windVelocityVectors, altAsl, aoa, PayloadInfo, M_number, tas, CM.flare, CM.chaff)

-------------------------------------------------------------------------------------------------------------------------------------------------------
          elseif obj.Name == "MiG-19P" then

          -- MiG-19P sends to SimShaker
          stringToSend=string.format("T=%.3f;N=%s;SelfData=%s;Engine_RPM=%.2f;VlctVectors=%s;altAgl=%.2f;PanShake=%s;Gun=%s;ACCs=%s;d10=0;IAS=%.2f;Wind=%s;altASL=%.2f;AoA=%.2f;PayloadInfo=%s;16d=0;M=%.4f;18d=0;19d=0;20d=0;TAS=%.2f;Flares=%s;Chaff=%s", t, obj.Name, myselfData, "0",  velocityVectors, altAgl, "000", CannonShells, AccelerationUnits, IAS, windVelocityVectors, altAsl, aoa, PayloadInfo, M_number, tas, CM.flare, CM.chaff)

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
            -- FC3 Plane sends to SimShaker
            stringToSend =
              string.format(
              "T=%.3f;N=%s;SelfData=%s;EngRPM=%s;VlctVectors=%s;altAgl=%.2f;MCPState=%s;CannonShells=%.0f;ACCs=%s;LG=%.2f;DragChute=%.2f;Wind=%s;altASL=%.2f;AoA=%.2f;PayloadInfo=%s;Flaps=%.2f;M=%.2f;Canopy=%.2f;Wings=%.2f;20d=0;TAS=%.2f;Flares=%s;Chaff=%s",
              t,
              obj.Name,
              myselfData,
              engineRPM,
              velocityVectors,
              altAgl,
              MCPState,
              CannonShells,
              AccelerationUnits,
              LandingGearState,
              DragChuteState,
              windVelocityVectors,
              altAsl,
              aoa,
              PayloadInfo,
              FlapsPos,
              M_number,
              CanopyPos,
              WingsPos,
              tas,
              CM.flare,
              CM.chaff
            )
          end

          stringToSend = string.format("%s;WeightOnWheels=%s;MechInfo=%s", stringToSend, WoW, mech)

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
          socket.try(self.sock_udp:send("DISCONNECT"))
          self.sock_udp:close()
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
