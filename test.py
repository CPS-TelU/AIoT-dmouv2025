import cv2
import os
import ssl
import time
import json
import numpy as np
import paho.mqtt.client as mqtt
from gpiozero import LED
from ultralytics import YOLO
from datetime import datetime
from collections import deque
from typing import Dict, List, Optional, Tuple, Any

class MQTTConfig:
    BROKER = "w916a671.ala.asia-southeast1.emqxsl.com"
    PORT = 8883
    USERNAME = "cpsmagang"
    PASSWORD = "cpsjaya123"
    DEVICE_IP = "dmouv"
    
    STATUS_TOPIC = f"iot/{DEVICE_IP}/status"
    SENSOR_TOPIC = f"iot/{DEVICE_IP}/sensor"
    ACTION_TOPIC = f"iot/{DEVICE_IP}/action"
    SETTINGS_UPDATE_TOPIC = f"iot/{DEVICE_IP}/settings/update"

class CameraConfig:
    SOURCE = "usb0"
    RESOLUTION_WIDTH = 640
    RESOLUTION_HEIGHT = 480
    FPS_BUFFER_SIZE = 50

class MotionDetectionConfig:
    ENABLED = True
    DETECTION_DURATION = 1.0
    MOVEMENT_THRESHOLD = 85.0
    POSITION_BUFFER_SIZE = 15
    CONFIDENCE_THRESHOLD = 0.5
    STABLE_DETECTION_FRAMES = 10
    MOTION_COOLDOWN = 1.0
    MIN_MOVEMENT_POINTS = 3
    RELATIVE_MOVEMENT_THRESHOLD = 0.15
    KEYPOINT_STABILITY_THRESHOLD = 0.05
    MIN_STABLE_KEYPOINTS = 5
    AUTO_OFF_DELAY = 10.0

class DeviceConfig:
    LAMP_PIN = 26
    FAN_PIN = 19

class MotionTracker:    
    def __init__(self):
        self.person_positions = deque(maxlen=MotionDetectionConfig.POSITION_BUFFER_SIZE)
        self.position_timestamps = deque(maxlen=MotionDetectionConfig.POSITION_BUFFER_SIZE)
        self.keypoint_history = deque(maxlen=MotionDetectionConfig.POSITION_BUFFER_SIZE)
        self.is_motion_detected = True
        self.motion_start_time = None
        self.last_motion_time = None
        self.person_detected = True
        self.motion_triggered = True
        self.stable_pose_count = 3
        self.reference_keypoints = None

    def get_stable_keypoints(self, keypoints: Optional[np.ndarray]) -> Optional[np.ndarray]:
        if keypoints is None or len(keypoints) == 0:
            return None
        
        keypoint_data = keypoints[0]
        stable_keypoints = []
        
        for i in range(len(keypoint_data)):
            if (len(keypoint_data[i]) >= 3 and 
                keypoint_data[i][2] > MotionDetectionConfig.CONFIDENCE_THRESHOLD):
                stable_keypoints.append([
                    keypoint_data[i][0], 
                    keypoint_data[i][1], 
                    keypoint_data[i][2]
                ])
        
        return (np.array(stable_keypoints) 
                if len(stable_keypoints) >= MotionDetectionConfig.MIN_STABLE_KEYPOINTS 
                else None)

    def calculate_pose_center(self, stable_keypoints: Optional[np.ndarray]) -> Optional[Tuple[float, float]]:
        if stable_keypoints is None or len(stable_keypoints) == 0:
            return None
            
        center_x = np.mean(stable_keypoints[:, 0])
        center_y = np.mean(stable_keypoints[:, 1])
        
        return (center_x, center_y)

    def calculate_relative_movement(self, 
                                  current_keypoints: Optional[np.ndarray], 
                                  reference_keypoints: Optional[np.ndarray]) -> float:
        if current_keypoints is None or reference_keypoints is None:
            return 0.0
        
        if len(current_keypoints) != len(reference_keypoints):
            return 0.0
        
        total_relative_movement = 0.0
        valid_comparisons = 0
        
        for i in range(len(current_keypoints)):
            current_point = current_keypoints[i][:2]
            reference_point = reference_keypoints[i][:2]
            
            distance = np.sqrt(np.sum((current_point - reference_point) ** 2))
            
            reference_distance = np.sqrt(reference_point[0]**2 + reference_point[1]**2)
            if reference_distance > 0:
                relative_distance = distance / reference_distance
                total_relative_movement += relative_distance
                valid_comparisons += 1
        
        return total_relative_movement / valid_comparisons if valid_comparisons > 0 else 0.0

    def is_keypoints_stable(self) -> bool:
        if len(self.keypoint_history) < 3:
            return False
        
        recent_keypoints = list(self.keypoint_history)[-3:]
        
        for i in range(1, len(recent_keypoints)):
            if recent_keypoints[i] is None or recent_keypoints[i-1] is None:
                return False
            
            if len(recent_keypoints[i]) != len(recent_keypoints[i-1]):
                return False
            
            movement = self.calculate_relative_movement(
                recent_keypoints[i], 
                recent_keypoints[i-1]
            )
            if movement > MotionDetectionConfig.KEYPOINT_STABILITY_THRESHOLD:
                return False
        
        return True

    def detect_skeleton_motion(self) -> bool:
        if (not MotionDetectionConfig.ENABLED or 
            len(self.person_positions) < MotionDetectionConfig.MIN_MOVEMENT_POINTS):
            return False
        
        positions = list(self.person_positions)
        timestamps = list(self.position_timestamps)
        keypoint_history = list(self.keypoint_history)
        
        if len(keypoint_history) < 2:
            return False
        
        significant_movements = 0
        total_duration = 0
        
        for i in range(len(positions) - 1):
            if keypoint_history[i] is None or keypoint_history[i+1] is None:
                continue
                
            time_difference = timestamps[i+1] - timestamps[i]
            if time_difference <= 0 or time_difference > MotionDetectionConfig.DETECTION_DURATION:
                continue
            
            relative_movement = self.calculate_relative_movement(
                keypoint_history[i+1], 
                keypoint_history[i]
            )
            
            position_distance = np.sqrt(
                (positions[i+1][0] - positions[i][0])**2 + 
                (positions[i+1][1] - positions[i][1])**2
            )
            
            if (relative_movement > MotionDetectionConfig.RELATIVE_MOVEMENT_THRESHOLD and 
                position_distance > MotionDetectionConfig.MOVEMENT_THRESHOLD):
                significant_movements += 1
                total_duration += time_difference
        
        return (significant_movements >= MotionDetectionConfig.MIN_MOVEMENT_POINTS and 
                total_duration >= MotionDetectionConfig.DETECTION_DURATION)

    def update_motion_detection(self, keypoints: Optional[np.ndarray]) -> None:
        current_time = time.time()
        stable_keypoints = self.get_stable_keypoints(keypoints)
        
        self.keypoint_history.append(stable_keypoints)
        
        if stable_keypoints is not None:
            self.person_detected = True
            
            if self.is_keypoints_stable():
                self.stable_pose_count += 1
            else:
                self.stable_pose_count = 0
            
            center_point = self.calculate_pose_center(stable_keypoints)
            if center_point is not None:
                self.person_positions.append(center_point)
                self.position_timestamps.append(current_time)
            
            if self.stable_pose_count >= 5:
                if self.detect_skeleton_motion():
                    if not self.is_motion_detected:
                        self.motion_start_time = current_time
                        self.is_motion_detected = True
                    
                    self.last_motion_time = current_time
                    
                    if ((self.motion_start_time is not None) and 
                        (current_time - self.motion_start_time) >= MotionDetectionConfig.DETECTION_DURATION):
                        self.motion_triggered = True
        
        else:
            self.person_detected = False
            self.stable_pose_count = 0
            
            if (self.last_motion_time and 
                current_time - self.last_motion_time > MotionDetectionConfig.MOTION_COOLDOWN):
                self.is_motion_detected = False
                self.motion_triggered = False
                self.motion_start_time = None

class SmartDevice:
    def __init__(self, name: str, gpio_pin: int):
        self.name = name
        self.instance = LED(gpio_pin)
        self.state = 0  # 0 = OFF, 1 = ON
        self.mode = "auto"  # auto, manual, scheduled
        self.schedule_on = None
        self.schedule_off = None
        self.is_person_reported = False
        self.no_motion_start_time = None

    def turn_on(self) -> None:
        self.instance.on()
        self.state = 1

    def turn_off(self) -> None:
        self.instance.off()
        self.state = 0

    def set_mode(self, mode: str) -> None:
        if mode in ["auto", "manual", "scheduled"]:
            self.mode = mode

    def set_schedule(self, on_time: str, off_time: str) -> None:
        self.schedule_on = on_time
        self.schedule_off = off_time

    def is_scheduled_active(self, current_time: datetime.time) -> bool:
        if not self.schedule_on or not self.schedule_off:
            return False
        
        try:
            on_time = datetime.strptime(self.schedule_on, "%H:%M").time()
            off_time = datetime.strptime(self.schedule_off, "%H:%M").time()
            
            if on_time < off_time:
                return on_time <= current_time < off_time
            else:
                return current_time >= on_time or current_time < off_time
                
        except (ValueError, TypeError):
            return False

    def close(self) -> None:
        self.instance.close()

class MQTTHandler:
    def __init__(self, devices: Dict[str, SmartDevice]):
        self.devices = devices
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self.client.username_pw_set(MQTTConfig.USERNAME, MQTTConfig.PASSWORD)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self._setup_ssl()
        self._setup_last_will()

    def _setup_ssl(self) -> None:
        context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        self.client.tls_set_context(context)

    def _setup_last_will(self) -> None:
        last_will_payload = json.dumps({"status": "offline"})
        self.client.will_set(
            MQTTConfig.STATUS_TOPIC, 
            payload=last_will_payload, 
            qos=1, 
            retain=True
        )

    def _on_connect(self, client, userdata, flags, rc, properties=None) -> None:
        if rc == 0:
            print(f"Successfully connected to EMQX Cloud at {MQTTConfig.BROKER}")
            
            client.subscribe(MQTTConfig.ACTION_TOPIC)
            print(f"Subscribed to action topic: {MQTTConfig.ACTION_TOPIC}")
            
            client.subscribe(MQTTConfig.SETTINGS_UPDATE_TOPIC)
            print(f"Subscribed to settings topic: {MQTTConfig.SETTINGS_UPDATE_TOPIC}")

            status_payload = json.dumps({"status": "online"})
            client.publish(MQTTConfig.STATUS_TOPIC, status_payload)
            print(f"Published ONLINE status to {MQTTConfig.STATUS_TOPIC}")
        else:
            error_messages = {
                1: "Incorrect protocol version",
                2: "Invalid client identifier", 
                3: "Server unavailable",
                4: "Bad username or password",
                5: "Not authorized"
            }
            print(f"Failed to connect to EMQX Cloud. Error code: {rc}")
            if rc in error_messages:
                print(f"Error: {error_messages[rc]}")

    def _on_message(self, client, userdata, msg) -> None:
        try:
            payload = json.loads(msg.payload.decode())
            
            if msg.topic == MQTTConfig.ACTION_TOPIC:
                self._handle_action_message(payload)
            elif msg.topic == MQTTConfig.SETTINGS_UPDATE_TOPIC:
                self._handle_settings_message(payload)
                
        except json.JSONDecodeError as e:
            print(f"Error decoding MQTT message: {e}")
        except Exception as e:
            print(f"Error handling MQTT message: {e}")

    def _handle_action_message(self, payload: Dict[str, Any]) -> None:
        device_name = payload.get("device")
        action = payload.get("action")
        
        if device_name in self.devices and action in ["turn_on", "turn_off"]:
            device = self.devices[device_name]
            device.set_mode("manual")
            
            if action == "turn_on":
                device.turn_on()
                print(f"Manual control: {device_name} turned ON")
            elif action == "turn_off":
                device.turn_off()
                print(f"Manual control: {device_name} turned OFF")

    def _handle_settings_message(self, payload: Dict[str, Any]) -> None:
        device_name = payload.get("device")
        
        if device_name in self.devices:
            device = self.devices[device_name]
            
            if "mode" in payload:
                new_mode = payload["mode"]
                device.set_mode(new_mode)
                print(f"Settings update: {device_name} mode set to {new_mode}")
            
            if "schedule_on" in payload and "schedule_off" in payload:
                device.set_schedule(
                    payload["schedule_on"], 
                    payload["schedule_off"]
                )
                print(f"Settings update: {device_name} schedule updated")

    def connect(self) -> bool:
        try:
            self.client.connect(MQTTConfig.BROKER, MQTTConfig.PORT, 60)
            self.client.loop_start()
            print("Connecting to EMQX Cloud...")
            return True
        except Exception as e:
            print(f"Error connecting to EMQX Cloud: {e}")
            return False

    def publish_sensor_data(self, device_name: str, data: Dict[str, Any]) -> None:
        payload = json.dumps({"device": device_name, **data})
        self.client.publish(MQTTConfig.SENSOR_TOPIC, payload)

    def publish_status(self, status: str) -> None:
        payload = json.dumps({"status": status})
        self.client.publish(MQTTConfig.STATUS_TOPIC, payload)

    def disconnect(self) -> None:
        self.publish_status("offline")
        time.sleep(0.5)
        self.client.loop_stop()
        self.client.disconnect()

class SmartMotionDetectionSystem:
    def __init__(self):
        self.motion_tracker = MotionTracker()
        self.consecutive_detections = 0
        self.fps_buffer = []

        self.devices = {
            "lamp": SmartDevice("lamp", DeviceConfig.LAMP_PIN),
            "fan": SmartDevice("fan", DeviceConfig.FAN_PIN)
        }
        
        self.mqtt_handler = MQTTHandler(self.devices)
        
        self._initialize_camera()
        self._initialize_model()

    def _initialize_camera(self) -> None:
        if "usb" in CameraConfig.SOURCE:
            camera_index = int(CameraConfig.SOURCE[3:])
            self.camera = cv2.VideoCapture(camera_index)
            self.camera.set(cv2.CAP_PROP_FRAME_WIDTH, CameraConfig.RESOLUTION_WIDTH)
            self.camera.set(cv2.CAP_PROP_FRAME_HEIGHT, CameraConfig.RESOLUTION_HEIGHT)
            
            if not self.camera.isOpened():
                raise RuntimeError("Failed to open camera")
        else:
            raise ValueError("Invalid camera source configuration")

    def _initialize_model(self) -> None:
        try:
            self.pose_model = YOLO("yolo11n-pose_ncnn_model", task="pose")
            print("YOLO pose model loaded successfully")
        except Exception as e:
            raise RuntimeError(f"Failed to load YOLO model: {e}")

    def _update_consecutive_detections(self, pose_found: bool) -> None:
        if pose_found:
            self.consecutive_detections = min(
                self.consecutive_detections + 1, 
                15
            )
        else:
            self.consecutive_detections = max(
                self.consecutive_detections - 1, 
                0
            )

    def _should_devices_be_active(self) -> bool:
        if MotionDetectionConfig.ENABLED:
            return (self.consecutive_detections >= MotionDetectionConfig.STABLE_DETECTION_FRAMES and 
                    self.motion_tracker.motion_triggered and
                    self.motion_tracker.stable_pose_count >= 5)
        else:
            return self.consecutive_detections >= MotionDetectionConfig.STABLE_DETECTION_FRAMES

    def _should_devices_be_inactive(self) -> bool:
        return (self.consecutive_detections <= 0 or 
                not self.motion_tracker.person_detected)

    def _control_devices_auto_mode(self, should_be_active: bool, should_be_inactive: bool) -> None:
        current_time = time.time()
        
        for device_name, device in self.devices.items():
            if device.mode != "auto":
                continue
                
            if should_be_active and device.state == 0:
                device.turn_on()
                device.no_motion_start_time = None
                print(f"Auto control: {device_name} turned ON (motion detected)")
            
            elif should_be_inactive and device.state == 1:
                if device.no_motion_start_time is None:
                    device.no_motion_start_time = current_time
                elif current_time - device.no_motion_start_time >= MotionDetectionConfig.AUTO_OFF_DELAY:
                    device.turn_off()
                    device.no_motion_start_time = None
                    print(f"Auto control: {device_name} turned OFF (no motion)")

            if should_be_active and not device.is_person_reported:
                device.is_person_reported = True
                self.mqtt_handler.publish_sensor_data(device_name, {"motion_detected": True})
            elif should_be_inactive and device.is_person_reported:
                device.is_person_reported = False
                self.mqtt_handler.publish_sensor_data(device_name, {"motion_cleared": True})

    def _control_devices_scheduled_mode(self) -> None:
        current_time = datetime.now().time()
        
        for device_name, device in self.devices.items():
            if device.mode != "scheduled":
                continue
                
            is_active_time = device.is_scheduled_active(current_time)
            
            if is_active_time and device.state == 0:
                device.turn_on()
                print(f"Scheduled control: {device_name} turned ON")
            elif not is_active_time and device.state == 1:
                device.turn_off()
                print(f"Scheduled control: {device_name} turned OFF")

    def _draw_device_status(self, frame: np.ndarray) -> None:
        y_position = 30
        
        for device_name, device in self.devices.items():
            mode_text = f"{device_name.upper()} Mode: {device.mode.upper()}"
            status_text = f"{device_name.upper()} Status: {'ON' if device.state == 1 else 'OFF'}"
            
            mode_color = (0, 255, 255)  # Yellow
            status_color = (0, 255, 0) if device.state == 1 else (0, 0, 255)  # Green/Red
            
            cv2.putText(frame, mode_text, (20, y_position), 
                       cv2.FONT_HERSHEY_SIMPLEX, 0.6, mode_color, 2)
            y_position += 30
            
            cv2.putText(frame, status_text, (20, y_position), 
                       cv2.FONT_HERSHEY_SIMPLEX, 0.6, status_color, 2)
            y_position += 40

    def _calculate_and_display_fps(self, frame: np.ndarray, processing_time: float) -> None:
        if processing_time > 0:
            current_fps = 1 / processing_time
            self.fps_buffer.append(current_fps)
            
            if len(self.fps_buffer) > CameraConfig.FPS_BUFFER_SIZE:
                self.fps_buffer.pop(0)
            
            average_fps = np.mean(self.fps_buffer)
            cv2.putText(frame, f'FPS: {average_fps:.2f}', 
                       (CameraConfig.RESOLUTION_WIDTH - 150, 30), 
                       cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 0), 2)

    def run(self) -> None:
        if not self.mqtt_handler.connect():
            print("Failed to connect to MQTT broker. Exiting...")
            return

        print("Smart Motion Detection System started successfully!")
        print("Press 'q' to quit")
        
        try:
            while True:
                start_time = time.perf_counter()
                
                ret, frame = self.camera.read()
                if not ret:
                    print("Failed to capture frame")
                    break

                results = self.pose_model.predict(frame, verbose=False)
                annotated_frame = results[0].plot()
                
                pose_found = len(results) > 0 and len(results[0].keypoints) > 0
                keypoints = results[0].keypoints.data.cpu().numpy() if pose_found else None
                
                self.motion_tracker.update_motion_detection(keypoints)
                self._update_consecutive_detections(pose_found)
                
                should_be_active = self._should_devices_be_active()
                should_be_inactive = self._should_devices_be_inactive()
                
                self._control_devices_auto_mode(should_be_active, should_be_inactive)
                self._control_devices_scheduled_mode()
                self._draw_device_status(annotated_frame)
                
                end_time = time.perf_counter()
                processing_time = end_time - start_time
                self._calculate_and_display_fps(annotated_frame, processing_time)
                
                cv2.imshow("Smart Motion Detection System", annotated_frame)
                
                if cv2.waitKey(1) & 0xFF == ord('q'):
                    print("Quit command received")
                    break
                    
        except KeyboardInterrupt:
            print("\nSystem interrupted by user")
        except Exception as e:
            print(f"System error: {e}")
        finally:
            self._cleanup()

    def _cleanup(self) -> None:
        print("Cleaning up system resources...")
        self.mqtt_handler.disconnect()
        self.camera.release()
        cv2.destroyAllWindows()
        
        for device in self.devices.values():
            device.close()
        
        print("System cleanup completed")

def main():
    try:
        system = SmartMotionDetectionSystem()
        system.run()
    except Exception as e:
        print(f"Failed to initialize system: {e}")
        return 1
    
    return 0

if __name__ == "__main__":
    exit(main())