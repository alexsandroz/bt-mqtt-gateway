import binascii
import time
from mqtt import MqttMessage, MqttConfigMessage

from workers.base import BaseWorker, retry
from bluepy import btle
import logger

REQUIREMENTS = ["bluepy"]
_LOGGER = logger.get(__name__)

NAME_SERVICE_UUID =  "00001800-0000-1000-8000-00805f9b34fb"
NAME_CHARACTERISTIC_UUID = "00002a00-0000-1000-8000-00805f9b34fb"

SWVER_SERVICE_UUID =  "0000180a-0000-1000-8000-00805f9b34fb"
SWVER_CHARACTERISTIC_UUID = "00002a28-0000-1000-8000-00805f9b34fb"

BATTERY_SERVICE_UUID =  "0000180f-0000-1000-8000-00805f9b34fb"
BATTERY_CHARACTERISTIC_UUID = "00002a19-0000-1000-8000-00805f9b34fb"


class MibandDeviceStatus:
    def __init__(
        self,
        worker,
        mac: str,
        name: str,
        model: str = None,
        version: str = None,
        battery: int = 0,
        last_status_time: float = 0,
        has_config_message: bool = False,
    ):
        if last_status_time is None:
            last_status_time = 0
            
        self.worker = worker  # type: MibandWorker
        self.mac = mac.lower()
        self.name = name
        self.model = model
        self.version = version
        self.battery = battery
        self.last_status_time = last_status_time
        self.has_config_message = has_config_message

    def _timeout(self):
        if self.battery > -1:
            return self.worker.available_timeout
        else:
            return self.worker.unavailable_timeout

    def has_time_elapsed(self):
        elapsed = time.time() - self.last_status_time
        return elapsed > self._timeout()
    
    def battery_payload(self):
        if self.battery > -1:
            return self.battery
        else:
            return "unavailable"

    def payload_hass_config_battery(self):
        ret =  '{'
        ret += '"dev":{'
        ret += '"ids":["{}"],'.format(self.name)
        ret += '"cns":[["mac","{}"]],'.format(self.mac)
        ret += '"name":"{}",'.format(self.name)
        if self.model is not None: ret += '"mdl":"{}",'.format(self.model) 
        if self.version is not None: ret += '"sw":"{}",'.format(self.version) 
        ret += '"mf": "Xiaomi"'
        ret += '},'
        ret += '"name":"battery",'
        ret += '"~":"{}/{}",'.format(self.worker.global_topic_prefix, self.worker.format_topic(self.name))
        ret += '"uniq_id":"{}_battery",'.format(self.name)
        ret += '"qos":1,'
        ret += '"stat_t":"~/battery",'
        ret += '"unit_of_meas":"%",'
        ret += '"dev_cla":"battery",'
        ret += '"stat_cla":"measurement",'
        ret += '"availability_mode":"all",'
        ret += '"availability":['
        ret += '{"topic":"~/online"},'
        ret += '{{"topic":"{}/LWT"}}],'.format(self.worker.global_topic_prefix)
        ret += '"source_type":"bluetooth_le"'
        ret += '}'
        return ret

    def generate_messages(self):
        messages = []
        if self.has_config_message:
            messages.append(
                MqttConfigMessage("homeassistant", "sensor/{}_battery".format(self.name),
                    payload=self.payload_hass_config_battery(), retain=True)
            )
            self.has_config_message = False
        if self.has_time_elapsed():
            messages.append(
                MqttMessage(
                    topic=self.worker.format_topic(
                        "{}/battery".format(self.name)
                    ),
                    payload=self.battery_payload()
                )
            )            
        return messages


class MibandWorker(BaseWorker):
    def _setup(self):
        self.last_status = []
        for name, mac in self.devices.items():
            _LOGGER.info("Adding %s device '%s' (%s)", repr(self), name, mac)
            self.last_status.append(MibandDeviceStatus(self, mac, name))            

    def format_topic(self, *topic_args):
        if hasattr(self, 'topic_prefix'):
            return "/".join([self.topic_prefix, *topic_args])
        else:
            return "/".join([*topic_args])

    def status_update(self):
        ret = []
        for device in self.last_status:
            _LOGGER.debug("Updating %s device '%s' (%s)", repr(self), device.name, device.mac)           
            switch_func = retry(load_device_info, retries=self.command_retries)
            try:
                switch_func(device)
            except Exception as e:
                _LOGGER.error("Error getting states on %s device '%s' (%s): %s",
                    repr(self),
                    device.name,
                    device.mac,
                    type(e).__name__,
                )
            ret += device.generate_messages()  
        return ret          
 
 
def load_device_info(device):
    from bluepy.btle import Peripheral

    try:
        device.battery = -1
        p = Peripheral()        
        p.connect(device.mac) 
        if not device.has_config_message:
            service = p.getServiceByUUID(NAME_SERVICE_UUID)        
            char = service.getCharacteristics(NAME_CHARACTERISTIC_UUID)[0]
            device.model = str(char.read(), "utf-8").rstrip('\x00')
            service = p.getServiceByUUID(SWVER_SERVICE_UUID)        
            char = service.getCharacteristics(SWVER_CHARACTERISTIC_UUID)[0]
            device.version = str(char.read(), "utf-8").rstrip('\x00')
            device.has_config_message = True
        service = p.getServiceByUUID(BATTERY_SERVICE_UUID)        
        char = service.getCharacteristics(BATTERY_CHARACTERISTIC_UUID)[0]
        device.battery = int.from_bytes(char.read(), byteorder='little', signed=True)
        device.last_status_time = time.time()
    finally:
        p.disconnect()
