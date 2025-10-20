import math

deg = 180 / math.pi
slugft3 = 0.00194032  # SI to slugft3
rad = 0.0174532925
ft = 3.28084  # m to ft (multiply meters by this to get feet)

# Velocity conversions
# NOTE: names indicate direction: kt2ms = knots -> meters/second
kt2ms = 0.514444  # knots to m/s
ms2kt = 1.943844  # m/s to knots
kmh2ms = 1.0 / 3.6  # km/h to m/s
ms2kmh = 3.6  # m/s to km/h
mph2ms = 0.44704  # miles per hour to m/s
ms2mph = 2.2369362920544  # m/s to mph (approx)

# Length conversions to SI (meters)
ft2m = 0.3048
in2m = 0.0254

# Common gravity/time conversions
fpss2gs = 1 / 32.17405  # feet per second^2 to g's (approx)
mpss2gs = 1 / 9.81      # meters per second^2 to g's

vsound = 290.07  # m/s, speed of sound at sea level in ISA condition
P0 = 101325  # Pa, ISA static pressure at sealevel
std_air_pressure = 1.225  # kg/m^3

# Convenience
percent = 0.01