#include <Arduino.h>

// Phase 3 bench firmware — SolenoidFrameV1 over USB serial.
// Replace when production boom MCU firmware is assigned.

static constexpr unsigned long SERIAL_BAUD = 115200;
static constexpr unsigned long FAILSAFE_MS = 300;
static constexpr uint8_t SECTION_COUNT = 5;
static const uint8_t SECTION_PINS[SECTION_COUNT] = {2, 3, 4, 5, 6};

static unsigned long last_valid_frame_ms = 0;
static bool in_failsafe = true;

static void all_outputs_off() {
  for (uint8_t i = 0; i < SECTION_COUNT; i++) {
    digitalWrite(SECTION_PINS[i], LOW);
  }
  digitalWrite(LED_BUILTIN, LOW);
  in_failsafe = true;
}

static const char* find_json_key(const char* line, const char* key) {
  char pattern[24];
  snprintf(pattern, sizeof(pattern), "\"%s\"", key);
  return strstr(line, pattern);
}

static bool parse_uint_field(const char* line, const char* key, unsigned long* out) {
  const char* pos = find_json_key(line, key);
  if (!pos) {
    return false;
  }
  pos = strchr(pos, ':');
  if (!pos) {
    return false;
  }
  pos++;
  while (*pos == ' ' || *pos == '\"') {
    pos++;
  }
  if (strncmp(pos, "0x", 2) == 0 || strncmp(pos, "0X", 2) == 0) {
    *out = strtoul(pos + 2, nullptr, 16);
  } else {
    *out = strtoul(pos, nullptr, 10);
  }
  return true;
}

static bool parse_duty_array(const char* line, uint8_t* duty, uint8_t count) {
  const char* pos = find_json_key(line, "duty");
  if (!pos) {
    return false;
  }
  pos = strchr(pos, '[');
  if (!pos) {
    return false;
  }
  pos++;
  for (uint8_t i = 0; i < count; i++) {
    while (*pos == ' ' || *pos == ',') {
      pos++;
    }
    duty[i] = (uint8_t)strtoul(pos, const_cast<char**>(&pos), 10);
  }
  return true;
}

static void apply_frame(unsigned long mask, const uint8_t* duty) {
  bool any_open = false;
  for (uint8_t i = 0; i < SECTION_COUNT; i++) {
    const bool open = duty[i] > 0 || ((mask >> i) & 1U);
    digitalWrite(SECTION_PINS[i], open ? HIGH : LOW);
    if (open) {
      any_open = true;
    }
  }
  digitalWrite(LED_BUILTIN, any_open ? HIGH : LOW);
  in_failsafe = false;
  last_valid_frame_ms = millis();
}

static void handle_solenoid_line(const char* line) {
  unsigned long hb = 0;
  unsigned long mask = 0;
  unsigned long seq = 0;
  uint8_t duty[SECTION_COUNT] = {0};

  if (!parse_uint_field(line, "hb", &hb) || hb != 1) {
    return;
  }
  parse_uint_field(line, "seq", &seq);
  if (!parse_uint_field(line, "mask", &mask)) {
    return;
  }
  if (!parse_duty_array(line, duty, SECTION_COUNT)) {
    for (uint8_t i = 0; i < SECTION_COUNT; i++) {
      duty[i] = ((mask >> i) & 1U) ? 100 : 0;
    }
  }

  apply_frame(mask, duty);

  Serial.print(F("ACK:{\"seq\":"));
  Serial.print(seq);
  Serial.print(F(",\"mask\":\"0x"));
  Serial.print(mask, HEX);
  Serial.print(F("\",\"safe\":0}\n"));
}

static void service_failsafe() {
  if (in_failsafe) {
    return;
  }
  if (millis() - last_valid_frame_ms > FAILSAFE_MS) {
    all_outputs_off();
    Serial.println(F("ACK:{\"seq\":-1,\"mask\":\"0x0\",\"safe\":1}"));
  }
}

void setup() {
  pinMode(LED_BUILTIN, OUTPUT);
  for (uint8_t i = 0; i < SECTION_COUNT; i++) {
    pinMode(SECTION_PINS[i], OUTPUT);
  }
  all_outputs_off();

  Serial.begin(SERIAL_BAUD);
  Serial.println(F("PUFworks-actuation MCU ready (SolenoidFrameV1)"));
}

void loop() {
  service_failsafe();

  if (!Serial.available()) {
    return;
  }

  String line = Serial.readStringUntil('\n');
  line.trim();
  if (line.length() == 0) {
    return;
  }

  if (line.startsWith(F("SOLENOID:"))) {
    handle_solenoid_line(line.c_str());
    return;
  }

  if (line.equalsIgnoreCase(F("HELLO")) || line.equalsIgnoreCase(F("PING"))) {
    Serial.println(F("PONG"));
  }
}
