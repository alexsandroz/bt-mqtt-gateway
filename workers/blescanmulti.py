import time

from mqtt import MqttMessage, MqttConfigMessage

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
        last_update: float = None,
        has_config_message: bool = True,
    ):
        if last_update is None:
            last_update = 0

        self.worker = worker  # type: BlescanmultiWorker
        self.mac = mac.lower()
        self.name = name
        self.available = available
        self.last_update = 0
        self.has_config_message = has_config_message

    def set_status(self, scanEntry):
        self.available = scanEntry is not None
        if self.available:
            self.last_update = time.time()

    def _timeout(self):
        if self.available:
            return self.worker.available_timeout
        else:
            return self.worker.unavailable_timeout

    def has_time_elapsed(self):
        elapsed = time.time() - self.last_update
        return elapsed > self._timeout()

    def online_payload(self):
        if self.available:
            return "online"
        else:
            return "offline"

    def rssi_payload(self, scanEntry):
        if self.available:
            return scanEntry.rssi
        else:
            return "offline"

    def payload_hass_config_online(self):
        ret =  '{'
        ret += '"dev":{'
        ret += '"ids":["{}"],'.format(self.name)
        ret += '"cns":[["mac","{}"]],'.format(self.mac)
        ret += '"name":"{}"'.format(self.name)
        ret += '},'
        ret += '"name":"online",'
        ret += '"~":"{}/{}",'.format(self.worker.global_topic_prefix, self.worker.format_topic(self.name))
        ret += '"uniq_id":"{}_online",'.format(self.name)
        ret += '"qos":1,'
        ret += '"stat_t":"~/online",'
        ret += '"pl_on":"online",'
        ret += '"pl_off":"offline",'
        ret += '"dev_cla":"connectivity",'
        ret += '"avty_t":"{}/LWT",'.format(self.worker.global_topic_prefix)
        ret += '"source_type":"bluetooth_le"'
        ret += '}'
        return ret

    def payload_hass_config_rssi(self):
        ret =  '{'
        ret += '"dev":{'
        ret += '"ids":["{}"],'.format(self.name)
        ret += '"cns":[["mac","{}"]],'.format(self.mac)
        ret += '"name":"{}"'.format(self.name)
        ret += '},'
        ret += '"name":"rssi",'
        ret += '"~":"{}/{}",'.format(self.worker.global_topic_prefix, self.worker.format_topic(self.name))
        ret += '"uniq_id":"{}_rssi",'.format(self.name)
        ret += '"qos":1,'
        ret += '"stat_t":"~/rssi",'
        ret += '"unit_of_meas":"dBm",'
        ret += '"dev_cla":"signal_strength",'
        ret += '"stat_cla":"measurement",'
        ret += '"entity_category":"diagnostic",'
        ret += '"availability_mode":"all",'
        ret += '"availability":['
        ret += '{"topic":"~/online"},'
        ret += '{{"topic":"{}/LWT"}}],'.format(self.worker.global_topic_prefix)
        ret += '"source_type":"bluetooth_le"'
        ret += '}'
        return ret


    def generate_messages(self, scanEntry):
        messages = []
        if self.available and self.has_config_message:
            messages.append(
                MqttConfigMessage("homeassistant", "binary_sensor/{}_online".format(self.name),
                    payload=self.payload_hass_config_online(), retain=True)
            )
            messages.append(
                MqttConfigMessage("homeassistant", "sensor/{}_rssi".format(self.name),
                    payload=self.payload_hass_config_rssi(), retain=True)
            )
            self.has_config_message = False
        if self.has_time_elapsed():
            messages.append(
                MqttMessage(
                    topic=self.worker.format_topic(
                        "{}/online".format(self.name)
                    ),
                    payload=self.online_payload()
                )
            )            
            messages.append(
                MqttMessage(
                    topic=self.worker.format_topic(
                        "{}/rssi".format(self.name)
                    ),
                    payload=self.rssi_payload(scanEntry)
                )
            )            
        return messages
        

class BlescanmultiWorker(BaseWorker):
    # Default values
    devices = {}
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
                    _LOGGER.debug("Discovered new device: %s rssi: %d", dev.addr, dev.rssi)

        super(BlescanmultiWorker, self).__init__(*args, **kwargs)
        self.scanner = Scanner().withDelegate(ScanDelegate())
        self.last_status = [
            BleDeviceStatus(self, mac, name) for name, mac in self.devices.items()
        ]
        _LOGGER.info("Adding %d %s devices", len(self.devices), repr(self))

    def format_topic(self, *topic_args):
        if hasattr(self, 'topic_prefix'):
            return "/".join([self.topic_prefix, *topic_args])
        else:
            return "/".join([*topic_args])

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
        finally:            
            self.scanner.clear()

        return ret
