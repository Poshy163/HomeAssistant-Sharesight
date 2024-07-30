from datetime import timedelta

from homeassistant.const import Platform
SCAN_INTERVAL = timedelta(minutes=5)
PLATFORMS = [Platform.SENSOR]
DOMAIN = "sharesight"
REDIRECT_URL = 'urn:ietf:wg:oauth:2.0:oob'
API_VERSION = 'v2'
