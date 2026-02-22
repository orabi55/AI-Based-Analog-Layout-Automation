class Netlist:
    """
    Holds devices and connectivity
    """

    def __init__(self):
        self.devices = {}   # name -> Device
        self.nets = {}      # net -> list[(device,pin)]

    def add_device(self, device):
        self.devices[device.name] = device

    def build_connectivity(self):
        """
        Build net -> device pin mapping
        """
        for dev in self.devices.values():
            for pin, net in dev.pins.items():
                if net not in self.nets:
                    self.nets[net] = []
                self.nets[net].append((dev.name, pin))
