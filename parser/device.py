class Device:
    """
    Represents one circuit device (transistor, resistor, capacitor...)
    """

    def __init__(self, name, dtype, pins, params):
        self.name = name            # instance name (M1, R3...)
        self.type = dtype.lower()   # nmos / pmos / cap / res
        self.pins = pins            # dict: {pin_name : net}
        self.params = params        # dict: parameters

    def __repr__(self):
        return f"<Device {self.name} type={self.type}>"
