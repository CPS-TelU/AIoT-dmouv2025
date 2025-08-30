import os
import random

MQTT_BROKER =  os.environ['MQTT_USERNAME']
MQTT_PORT = 8883  # TLS/SSL port
MQTT_USERNAME = os.environ['MQTT_USERNAME'] 
MQTT_PASSWORD = os.environ['MQTT_PASSWORD']  
SYSTEM_NAME = os.environ['SYSTEM_NAME']
CLIENT_ID = f'python-mqtt-{random.randint(0, 1000)}'

# MQTT Topics
STATUS_TOPIC = f"iot/{SYSTEM_NAME}/status"
SENSOR_TOPIC = f"iot/{SYSTEM_NAME}/sensor"
ACTION_TOPIC = f"iot/{SYSTEM_NAME}/action"
SETTINGS_UPDATE_TOPIC = f"iot/{SYSTEM_NAME}/settings/update"


