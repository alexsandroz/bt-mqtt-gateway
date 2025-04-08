from mqtt import MqttMessage, MqttConfigMessage

from workers.base import BaseWorker, retry
from bluepy import btle
import logger

REQUIREMENTS = ["bluepy"]
_LOGGER = logger.get(__name__)

BATTERY_SERVICE_UUID =  "0000180f-0000-1000-8000-00805f9b34fb"
BATTERY_CHARACTERISTIC_UUID = "00002a19-0000-1000-8000-00805f9b34fb"


class MibandDeviceStatus:
    def __init__(
        self,
        worker,
        mac: str,
        name: str,
        battery: int = 0,
        last_status_time: float = None,
        has_message: bool = False,
        has_config_message: bool = True,
    ):
        self.worker = worker  # type: MibandWorker
        self.mac = mac.lower()
        self.name = name
        self.battery = battery
        self.last_status_time = last_status_time
        self.has_message = has_message
        self.has_config_message = has_config_message

    def payload_hass_config_battery(self):
        ret =  '{'
        ret += '"dev":{'
        ret += '"ids":["{}"],'.format(self.name)
        ret += '"cns":[["mac","{}"]],'.format(self.mac)
        ret += '"name":"{}"'.format(self.name)
        ret += '},'
        ret += '"name":"battery",'
        ret += '"~":"{}/{}/{}",'.format(self.worker.global_topic_prefix, self.worker.topic_prefix, self.name)
        ret += '"uniq_id":"{}_battery",'.format(self.name)
        ret += '"qos":1,'
        ret += '"stat_t":"~/battery",'
        ret += '"unit_of_meas":"%",'
        ret += '"dev_cla":"battery",'
        ret += '"stat_cla":"measurement",'
        ret += '"avty_t":"{}/LWT",'.format(self.worker.global_topic_prefix)
        ret += '"source_type":"bluetooth_le"'
        ret += '}'
        return ret

    def generate_messages(self):
        messages = []
        if self.has_config_message and self.has_message:
            messages.append(
                MqttConfigMessage("homeassistant", "sensor/{}_battery".format(self.name),
                    payload=self.payload_hass_config_battery(), retain=True)
            )
            self.has_config_message = False
        if self.has_message:
            messages.append(
                MqttMessage(
                    topic=self.worker.format_topic(
                        "{}/battery".format(self.name)
                    ),
                    payload=self.battery
                )
            )            
            self.has_message = False
        return messages


class MibandWorker(BaseWorker):
    def _setup(self):
        self.last_status = []
        for name, mac in self.devices.items():
            _LOGGER.info("Adding %s device '%s' (%s)", repr(self), name, mac)
            self.last_status.append(MibandDeviceStatus(self, mac, name))            


    def status_update(self):
        ret = []
        for device in self.last_status:
            _LOGGER.debug("Updating %s device '%s' (%s)", repr(self), device.name, device.mac)           
            switch_func = retry(load_device_info, retries=self.command_retries)
            try:
                switch_func(device)
                ret += device.generate_messages()  
            except Exception as e:
                _LOGGER.error("Error getting states on %s device '%s' (%s): %s",
                    repr(self),
                    device.name,
                    device.mac,
                    type(e).__name__,
                )
        return ret          
 
 
def load_device_info(device):
    from bluepy.btle import Peripheral

    try:
        p = Peripheral()        
        p.connect(device.mac) 
        service = p.getServiceByUUID(BATTERY_SERVICE_UUID)        
        char = service.getCharacteristics(BATTERY_CHARACTERISTIC_UUID)[0]
        device.battery = int.from_bytes(char.read(), byteorder='little', signed=True)
        device.has_message = True
    finally:
        p.disconnect()
