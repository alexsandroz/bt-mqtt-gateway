import time

from mqtt import MqttMessage, MqttConfigMessage, MqttClient

from workers.base import BaseWorker
from utils import booleanize
import logger

REQUIREMENTS = ["bluepy"]
_LOGGER = logger.get(__name__)


class BleDeviceStatus:
    def __init__(
        self,
        worker,
        mac: str,
        name: str,
        available: bool = False,
        last_status_time: float = None,
        message_sent: bool = True,
        haas_config_sent: bool = False,
    ):
        if last_status_time is None:
            last_status_time = time.time()

        self.worker = worker  # type: BlescanmultiWorker
        self.mac = mac.lower()
        self.name = name
        self.available = available
        self.last_status_time = last_status_time
        self.message_sent = message_sent
        self.hass_config_sent = haas_config_sent

    def set_status(self, scanEntry):
        self.available = scanEntry is not None
        if self.available:
            self.last_status_time = time.time()
            self.message_sent = False

    def _timeout(self):
        if self.available:
            return self.worker.available_timeout
        else:
            return self.worker.unavailable_timeout

    def has_time_elapsed(self):
        elapsed = time.time() - self.last_status_time
        return elapsed > self._timeout()

    def payload(self):
        if self.available:
            return self.worker.available_payload
        else:
            return self.worker.unavailable_payload

    def payload_hass_config_device_tracker(self, device):
        ret =  '{'
        ret += '"dev":{'
        ret += '"ids":["{}"],'.format(self.name)
        ret += '"cns":[["mac","{}"]],'.format(self.mac)
        ret += '"name":"{}"'.format(self.name)
        #ret += '"sw": "",'
        #ret += '"mf": "",'
        #ret += '"mdl": "",'
        #ret += '"cu": ""'
        ret += '},'
        ret += '"name":"tracker",'
        ret += '"~":"{}/{}/presence/{}",'.format(self.worker.global_topic_prefix, self.worker.topic_prefix, self.name)
        ret += '"uniq_id":"{}_tracker",'.format(self.name)
        ret += '"qos":1,'
        ret += '"stat_t":"~",'
        ret += '"avty_t": "~/LTW",'
        ret += '"source_type":"bluetooth_le"'
        ret += '}'
        return ret

    def payload_hass_config_rssi(self, device):
        ret =  '{'
        ret += '"dev":{'
        ret += '"ids":["{}"],'.format(self.name)
        ret += '"cns":[["mac","{}"]],'.format(self.mac)
        ret += '"name":"{}"'.format(self.name)
        #ret += '"sw": "",'
        #ret += '"mf": "",'
        #ret += '"mdl": "",'
        #ret += '"cu": ""'
        ret += '},'
        ret += '"name":"rssi",'
        ret += '"~":"{}/{}/presence/{}",'.format(self.worker.global_topic_prefix, self.worker.topic_prefix, self.name)
        ret += '"uniq_id":"{}_rssi",'.format(self.name)
        ret += '"qos":1,'
        ret += '"dev_cla":"signal_strength",'
        ret += '"stat_t":"~/rssi",'
        ret += '"unit_of_meas":"dBm",'
        ret += '"entity_category":"diagnostic",'
        ret += '"avty_t": "~/LTW",'
        ret += '"stat_cla":"measurement",'
        ret += '"source_type":"bluetooth_le"'
        ret += '}'
        return ret

    def generate_messages(self, device):
        messages = []
        if not self.hass_config_sent and self.available:
            self.hass_config_sent = True
            #MqttClient.mqttc.will_set(
            #            topic=self.worker.format_topic("presence/{}".format(self.name)),
            #            payload=MqttClient.LWT_ONLINE, retain=True)
            messages.append(
                MqttConfigMessage("homeassistant", "sensor/{}_rssi".format(self.name),
                    payload=self.payload_hass_config_rssi(device), retain=True)
            )
            messages.append(
                MqttConfigMessage("homeassistant", "device_tracker/{}".format(self.name),
                    payload=self.payload_hass_config_device_tracker(device), retain=True)
            )
        if not self.message_sent and self.has_time_elapsed():
            self.message_sent = True
            messages.append(
                MqttMessage(
                    topic=self.worker.format_topic("presence/{}".format(self.name)),
                    payload=self.payload(),
                )
            )
            if self.available:
                messages.append(
                    MqttMessage(
                        topic=self.worker.format_topic(
                            "presence/{}/rssi".format(self.name)
                        ),
                        payload=device.rssi,
                    )
                )            
                messages.append(
                    MqttMessage(
                        topic=self.worker.format_topic(
                            "presence/{}/{}".format(self.name, "LTW")
                        ),
                        payload="online", retain=True,
                    )
                )            
        return messages
        

class BlescanmultiWorker(BaseWorker):
    # Default values
    devices = {}
    # Payload that should be send when device is available
    available_payload = "home"  # type: str
    # Payload that should be send when device is unavailable
    unavailable_payload = "not_home"  # type: str
    # After what time (in seconds) we should inform that device is available (default: 0 seconds)
    available_timeout = 0  # type: float
    # After what time (in seconds) we should inform that device is unavailable (default: 60 seconds)
    unavailable_timeout = 60  # type: float
    scan_timeout = 10.0  # type: float
    scan_passive = True  # type: str or bool

    def __init__(self, *args, **kwargs):
        from bluepy.btle import Scanner, DefaultDelegate

        class ScanDelegate(DefaultDelegate):
            def __init__(self):
                DefaultDelegate.__init__(self)

            def handleDiscovery(self, dev, isNewDev, isNewData):
                if isNewDev:
                    _LOGGER.debug("Discovered new device: %s" % dev.addr)

        super(BlescanmultiWorker, self).__init__(*args, **kwargs)
        self.scanner = Scanner().withDelegate(ScanDelegate())
        self.last_status = [
            BleDeviceStatus(self, mac, name) for name, mac in self.devices.items()
        ]
        _LOGGER.info("Adding %d %s devices", len(self.devices), repr(self))

    def status_update(self):
        from bluepy import btle

        _LOGGER.info("Updating %d %s devices", len(self.devices), repr(self))

        ret = []

        try:
            scanEntries = self.scanner.scan(
                float(self.scan_timeout), passive=booleanize(self.scan_passive)
            )
            mac_addresses = {scanEntry.addr: scanEntry for scanEntry in scanEntries}

            for status in self.last_status:
                scanEntry = mac_addresses.get(status.mac, None)
                status.set_status(scanEntry)
                ret += status.generate_messages(scanEntry)

        except btle.BTLEException as e:
            logger.log_exception(
                _LOGGER,
                "Error during update (%s)",
                repr(self),
                type(e).__name__,
                suppress=True,
            )

        return ret
