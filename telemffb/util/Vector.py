import math


class Vector:
    def __init__(self, x, y=0.0, z=0.0):
        self.x : float
        self.y : float
        self.z : float
        if isinstance(x, list):
            self.x, self.y, self.z = x
        else:
            self.x = x
            self.y = y
            self.z = z

    def __eq__(self, p):
        return self.x == p.x and self.y == p.y and self.z == p.z

    def __add__(self, p):
        return Vector(self.x + p.x, self.y + p.y, self.z + p.z)

    def __sub__(self, p):
        return Vector(self.x - p.x, self.y - p.y, self.z - p.z)

    def __unm__(self):
        return Vector(-self.x, -self.y, -self.z)

    def __iter__(self):
        return iter((self.x, self.y, self.z))

    def __mul__(self, s):
        if isinstance(s, Vector):
            return self.x * s.x + self.y * s.y + self.z * s.z
        elif isinstance(s, (int, float)):
            return Vector(self.x * s, self.y * s, self.z * s)

    def __div__(self, s):
        if isinstance(s, (int, float)):
            return Vector(self.x / s, self.y / s, self.z / s)

    def __concat__(self, p):
        return self.x * p.x + self.y * p.y + self.z * p.z

    def __pow__(self, p):
        return Vector(
            self.y * p.z - self.z * p.y,
            self.z * p.x - self.x * p.z,
            self.x * p.y - self.y * p.x
        )

    def ort(self):
        l = self.length()
        if l > 0:
            return Vector(self.x / l, self.y / l, self.z / l)
        else:
            return self

    def normalize(self):
        l = self.length()
        if l > 0:
            self.x /= l
            self.y /= l
            self.z /= l

    def set(self, xx, yy, zz):
        self.x = xx
        self.y = yy
        self.z = zz

    def translate(self, dx, dy, dz):
        return Vector(self.x + dx, self.y + dy, self.z + dz)

    def __str__(self):
        return f'({self.x},{self.y},{self.z})'

    def length(self):
        return math.sqrt(self.x * self.x + self.y * self.y + self.z * self.z)

    def rotZ(self, ang):
        sina = math.sin(ang)
        cosa = math.cos(ang)
        return Vector(self.x * cosa - self.y * sina, self.x * sina + self.y * cosa, self.z)

    def rotX(self, ang):
        sina = math.sin(ang)
        cosa = math.cos(ang)
        return Vector(self.x, self.y * cosa - self.z * sina, self.y * sina + self.z * cosa)

    def rotY(self, ang):
        sina = math.sin(ang)
        cosa = math.cos(ang)
        return Vector(self.z * sina + self.x * cosa, self.y, self.z * cosa - self.x * sina)

    def rotAxis(self, axis, ang):
        ax = axis.ort()
        cosa = math.cos(ang)
        sina = math.sin(ang)
        versa = 1.0 - cosa
        xy = ax.x * ax.y
        yz = ax.y * ax.z
        zx = ax.z * ax.x
        sinx = ax.x * sina
        siny = ax.y * sina
        sinz = ax.z * sina
        m10 = ax.x * ax.x * versa + cosa
        m11 = xy * versa + sinz
        m12 = zx * versa - siny
        m20 = xy * versa - sinz
        m21 = ax.y * ax.y * versa + cosa
        m22 = yz * versa + sinx
        m30 = zx * versa + siny
        m31 = yz * versa - sinx
        m32 = ax.z * ax.z * versa + cosa
        return Vector(
            m10 * self.x + m20 * self.y + m30 * self.z,
            m11 * self.x + m21 * self.y + m31 * self.z,
            m12 * self.x + m22 * self.y + m32 * self.z
        )


class Vector2D:
    def __init__(self, x, y):
        self.x = x
        self.y = y

    def __str__(self):
        return f"Vector2D({self.x}, {self.y})"

    def __repr__(self):
        return self.__str__()

    def __add__(self, other):
        return Vector2D(self.x + other.x, self.y + other.y)

    def __sub__(self, other):
        return Vector2D(self.x - other.x, self.y - other.y)

    def __mul__(self, scalar):
        return Vector2D(self.x * scalar, self.y * scalar)

    def __rmul__(self, scalar):
        return self.__mul__(scalar)

    def __truediv__(self, scalar):
        return Vector2D(self.x / scalar, self.y / scalar)

    def magnitude(self):
        return math.sqrt(self.x ** 2 + self.y ** 2)

    def dot(self, other):
        return self.x * other.x + self.y * other.y

    def cross(self, other):
        return self.x * other.y - self.y * other.x

    def to_polar(self):
        r = self.magnitude()
        theta_radians = math.atan2(self.y, self.x)
        return r, theta_radians

    def normalize(self):
        magnitude = self.magnitude()
        if magnitude == 0:
            raise ValueError("Cannot normalize a zero-length vector.")
        return Vector2D(self.x / magnitude, self.y / magnitude)