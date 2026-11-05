#include <Arduino.h>
#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <math.h>

// Simulator Wokwi Wi-Fi. Use only non-sensitive demo data on this public broker.
static const char* BROKER = "broker.hivemq.com";
static const char* TOPIC_IN = "mg/v1/machineguard-f3cfa8ca05/motor01/sensor/temperature";
static const char* TOPIC_OUT = "mg/v1/machineguard-f3cfa8ca05/motor01/feature/temperature";
static const char* PROJECT_ID = "machineguard-f3cfa8ca05";
static const char* MACHINE_ID = "motor01";
WiFiClient wifi;
PubSubClient mqtt(wifi);
float values[10] = {0};
int countSamples = 0;
int writePos = 0;
int lastSeq = -1;
String currentRun = "";
unsigned long lastReconnect = 0;

void resetWindow() {
  countSamples = 0;
  writePos = 0;
}

void onMqtt(char* topic, byte* payload, unsigned int length) {
  if (length > 900) return;
  StaticJsonDocument<1024> input;
  if (deserializeJson(input, payload, length)) return;
  if (input["schema_version"] != 1 || input["msg_type"] != "temperature_sample") return;
  if (input["project_id"] != PROJECT_ID || input["machine_id"] != MACHINE_ID) return;
  if (input["device_id"] != "plant") return;
  if (!input["seq"].is<int>() || !(input["sim_time_s"].is<float>() || input["sim_time_s"].is<double>() || input["sim_time_s"].is<int>())) return;
  JsonObject d = input["payload"].as<JsonObject>();
  if (d.isNull() || !(d["temp_c"].is<float>() || d["temp_c"].is<double>() || d["temp_c"].is<int>()) || !(d["sample_sim_time_s"].is<float>() || d["sample_sim_time_s"].is<double>() || d["sample_sim_time_s"].is<int>())) return;
  float value = d["temp_c"].as<float>();
  float simTime = input["sim_time_s"].as<float>();
  if (!isfinite(value) || !isfinite(simTime) || fabs(d["sample_sim_time_s"].as<float>()-simTime)>0.001f) return;
  int seq = input["seq"].as<int>();
  String run = input["run_id"].as<String>();
  if (run.length()==0 || seq<0) return;
  if (run != currentRun) { currentRun = run; lastSeq = -1; resetWindow(); }
  if (seq <= lastSeq) return;
  if (lastSeq >= 0 && seq != lastSeq + 1) resetWindow();
  lastSeq = seq;
  values[writePos] = value;
  writePos = (writePos+1)%10;
  if (countSamples < 10) countSamples++;
  Serial.printf("seq %4d | suhu %6.2f C | buffer %2d/10\r\n", seq, value, countSamples);
  if (countSamples < 10) return;
  float sum=0; for (float x : values) sum+=x;
  float avg=sum/10.0f;
  StaticJsonDocument<512> output;
  output["schema_version"] = 1;
  output["msg_type"] = "temperature_feature";
  output["project_id"] = PROJECT_ID;
  output["machine_id"] = MACHINE_ID;
  output["device_id"] = "esp32";
  output["run_id"] = currentRun;
  output["seq"] = seq;
  output["ts_wall_ms"] = 0; // No RTC; run-local virtual time used to align samples.
  output["sim_time_s"] = simTime;
  JsonObject f = output.createNestedObject("payload");
  f["temp_ma10_c"] = roundf(avg*1000)/1000;
  f["window_n"] = 10;
  f["last_sample_sim_time_s"] = simTime;
  char msg[600]; size_t size=serializeJson(output,msg,sizeof(msg));
  if (size && mqtt.publish(TOPIC_OUT, (const uint8_t*)msg, size)) {
    Serial.printf("seq %4d | MA10 %6.2f C | ke laptop\r\n", seq, avg);
  }
}

void connectMqtt(){
  if (mqtt.connected() || WiFi.status()!=WL_CONNECTED) return;
  if (millis() - lastReconnect < 3000) return;
  lastReconnect=millis();
  String id = "machineguard-esp32-" + String((uint32_t)ESP.getEfuseMac(), HEX);
  if (mqtt.connect(id.c_str())) {
    mqtt.subscribe(TOPIC_IN, 1);
    Serial.println("ESP32 MQTT SIAP | HiveMQ");
  } else Serial.printf("MQTT gagal, rc=%d\r\n", mqtt.state());
}

void setup() {
  Serial.begin(115200);
  WiFi.begin("Wokwi-GUEST", "", 6);
  mqtt.setServer(BROKER,1883); // Unencrypted public demo: no credentials or personal info.
  mqtt.setCallback(onMqtt);
  mqtt.setBufferSize(1024);
  Serial.println("MACHINEGUARD ESP32 mulai...");
}
void loop(){
  connectMqtt();
  mqtt.loop();
  delay(20);
}
