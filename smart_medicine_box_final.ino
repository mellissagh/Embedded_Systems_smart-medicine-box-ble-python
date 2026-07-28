/*
  SMART MEDICINE BOX — FINAL HARDWARE CONTROLLER

  Hardware:
    A0  Lid switch (INPUT_PULLUP; LOW = closed, HIGH = open)
    A1  Morning IR sensor
    A2  Afternoon IR sensor
    A3  Evening IR sensor

    D6  Grove buzzer

    D12 LCD RS
    D11 LCD Enable
    D5  LCD D4
    D4  LCD D5
    D3  LCD D6
    D2  LCD D7

    D0/D1 HMSoft BLE UART

  Notes:
  - The IR LEDs are powered continuously from 5V through resistors.
    They do not need Arduino output pins.
  - Python is the source of truth for schedules and expected slot.
  - Arduino reports physical states and executes BLE commands.
  - Disconnect HMSoft from D0/D1 while uploading.
*/

#include <LiquidCrystal.h>

// ============================================================
// Pins
// ============================================================

constexpr uint8_t LID_SWITCH_PIN = A0;

constexpr uint8_t MORNING_SENSOR_PIN = A1;
constexpr uint8_t AFTERNOON_SENSOR_PIN = A2;
constexpr uint8_t EVENING_SENSOR_PIN = A3;

constexpr uint8_t BUZZER_PIN = 6;

// LCD: RS, E, D4, D5, D6, D7
LiquidCrystal lcd(12, 11, 5, 4, 3, 2);

// ============================================================
// Sensor calibration
// ============================================================
//
// High = pill present / beam blocked
// Low  = pill absent  / beam clear
//
// Morning values are reliable from current calibration.
// Afternoon and Evening values are provisional and should be
// adjusted after isolated calibration if necessary.

constexpr int MORNING_PRESENT_THRESHOLD = 250;
constexpr int MORNING_ABSENT_THRESHOLD = 100;

constexpr int AFTERNOON_PRESENT_THRESHOLD = 940;
constexpr int AFTERNOON_ABSENT_THRESHOLD = 900;

constexpr int EVENING_PRESENT_THRESHOLD = 980;
constexpr int EVENING_ABSENT_THRESHOLD = 965;

// Average multiple readings to reduce noise.
constexpr uint8_t SENSOR_SAMPLE_COUNT = 10;

// ============================================================
// Timing
// ============================================================

constexpr unsigned long LID_DEBOUNCE_MS = 40;
constexpr unsigned long SENSOR_UPDATE_INTERVAL_MS = 100;
constexpr unsigned long STATE_REPORT_INTERVAL_MS = 1000;
constexpr unsigned long COMMAND_TIMEOUT_MS = 3000;

// ============================================================
// Compartments
// ============================================================

enum Slot : uint8_t {
  MORNING_SLOT = 0,
  AFTERNOON_SLOT = 1,
  EVENING_SLOT = 2,
  SLOT_COUNT = 3,
  NO_SLOT = 255
};

const char* SLOT_NAMES[SLOT_COUNT] = {
  "MORNING",
  "AFTERNOON",
  "EVENING"
};

uint8_t expectedSlot = NO_SLOT;

// ============================================================
// State
// ============================================================

bool pillPresent[SLOT_COUNT] = {false, false, false};
bool pillAtLidOpen[SLOT_COUNT] = {false, false, false};

int rawValues[SLOT_COUNT] = {0, 0, 0};

bool stableLidOpen = true;
bool lastRawLidOpen = true;
bool openingSessionActive = false;

unsigned long lidRawChangedAt = 0;
unsigned long lastSensorUpdateAt = 0;
unsigned long lastStateReportAt = 0;

// ============================================================
// BLE command buffer
// ============================================================

String commandBuffer;

// ============================================================
// Setup
// ============================================================

void setup() {
  Serial.begin(9600);

  pinMode(LID_SWITCH_PIN, INPUT_PULLUP);
  pinMode(BUZZER_PIN, OUTPUT);
  noTone(BUZZER_PIN);

  lcd.begin(16, 2);
  showLCD("Medicine Box", "Starting...");

  stableLidOpen = digitalRead(LID_SWITCH_PIN) == HIGH;
  lastRawLidOpen = stableLidOpen;
  lidRawChangedAt = millis();

  updatePillSensors();

  sendMessage("SYSTEM|MEDICINE_BOX_READY");
  printCurrentState();

  showLCD("Medicine Box", "BLE ready");
  startupBeep();

  if (stableLidOpen) {
    captureOpeningSnapshot();
    openingSessionActive = true;
    sendMessage("EVENT|LID_ALREADY_OPEN_AT_STARTUP");
  }
}

// ============================================================
// Main loop
// ============================================================

void loop() {
  readBLECommands();
  updateLidSwitch();

  const unsigned long now = millis();

  if (now - lastSensorUpdateAt >= SENSOR_UPDATE_INTERVAL_MS) {
    lastSensorUpdateAt = now;
    updatePillSensors();
  }

  if (now - lastStateReportAt >= STATE_REPORT_INTERVAL_MS) {
    lastStateReportAt = now;
    printCurrentState();
  }
}

// ============================================================
// BLE command handling
// ============================================================

void readBLECommands() {
  while (Serial.available() > 0) {
    const char c = static_cast<char>(Serial.read());

    if (c == '\n' || c == '\r') {
      if (commandBuffer.length() > 0) {
        commandBuffer.trim();
        handleCommand(commandBuffer);
        commandBuffer = "";
      }
    } else {
      if (commandBuffer.length() < 120) {
        commandBuffer += c;
      } else {
        commandBuffer = "";
        sendMessage("ERROR|COMMAND_TOO_LONG");
      }
    }
  }
}

void handleCommand(const String& command) {
  if (command == "CMD|STATUS") {
    printCurrentState();
    return;
  }

  if (command == "CMD|BUZZER|DOSE_REMINDER") {
    reminderBeep();
    sendMessage("ACK|BUZZER|DOSE_REMINDER");
    return;
  }

  if (command == "CMD|BUZZER|LATE_REMINDER") {
    lateReminderBeep();
    sendMessage("ACK|BUZZER|LATE_REMINDER");
    return;
  }

  if (command == "CMD|BUZZER|CAREGIVER_REMINDER") {
    caregiverReminderBeep();
    sendMessage("ACK|BUZZER|CAREGIVER_REMINDER");
    return;
  }

  if (command == "CMD|BUZZER|WRONG_PILL") {
    wrongPillBeep();
    sendMessage("ACK|BUZZER|WRONG_PILL");
    return;
  }

  if (command == "CMD|BUZZER|MULTIPLE_PILLS") {
    multiplePillsBeep();
    sendMessage("ACK|BUZZER|MULTIPLE_PILLS");
    return;
  }

  if (command == "CMD|BUZZER|SUCCESS") {
    correctPillBeep();
    sendMessage("ACK|BUZZER|SUCCESS");
    return;
  }

  if (command == "CMD|BUZZER|STOP") {
    noTone(BUZZER_PIN);
    sendMessage("ACK|BUZZER|STOP");
    return;
  }

  if (command.startsWith("CMD|EXPECTED|")) {
    const String slotText = command.substring(13);
    const int8_t slot = slotFromString(slotText);

    if (slot < 0) {
      sendMessage("ERROR|INVALID_EXPECTED_SLOT");
      return;
    }

    expectedSlot = static_cast<uint8_t>(slot);

    Serial.print("ACK|EXPECTED|");
    Serial.println(SLOT_NAMES[expectedSlot]);

    showExpectedSlot();
    return;
  }

  if (command.startsWith("CMD|LCD|")) {
    const String payload = command.substring(8);
    const int separator = payload.indexOf('|');

    String line1;
    String line2;

    if (separator >= 0) {
      line1 = payload.substring(0, separator);
      line2 = payload.substring(separator + 1);
    } else {
      line1 = payload;
      line2 = "";
    }

    showLCD(line1, line2);
    sendMessage("ACK|LCD");
    return;
  }

  if (command == "CMD|LCD|CLEAR") {
    lcd.clear();
    sendMessage("ACK|LCD|CLEAR");
    return;
  }

  sendMessage("ERROR|UNKNOWN_COMMAND");
}

int8_t slotFromString(String value) {
  value.trim();
  value.toUpperCase();

  if (value == "MORNING") {
    return MORNING_SLOT;
  }

  if (value == "AFTERNOON") {
    return AFTERNOON_SLOT;
  }

  if (value == "EVENING") {
    return EVENING_SLOT;
  }

  return -1;
}

// ============================================================
// Lid handling
// ============================================================

void updateLidSwitch() {
  const bool rawLidOpen = digitalRead(LID_SWITCH_PIN) == HIGH;

  if (rawLidOpen != lastRawLidOpen) {
    lastRawLidOpen = rawLidOpen;
    lidRawChangedAt = millis();
  }

  if (
      rawLidOpen != stableLidOpen &&
      millis() - lidRawChangedAt >= LID_DEBOUNCE_MS
  ) {
    stableLidOpen = rawLidOpen;

    if (stableLidOpen) {
      onLidOpened();
    } else {
      onLidClosed();
    }
  }
}

void onLidOpened() {
  updatePillSensors();
  captureOpeningSnapshot();

  openingSessionActive = true;

  sendMessage("EVENT|LID_OPEN");
  printCurrentState();

  showLCD("Lid open", "Choose medicine");
}

void onLidClosed() {
  updatePillSensors();

  sendMessage("EVENT|LID_CLOSED");
  printCurrentState();

  if (!openingSessionActive) {
    sendMessage("RESULT|INFO|NO_VALID_OPENING_SESSION");
    showLCD("Lid closed", "Ready");
    return;
  }

  evaluateRemovedPills();
  openingSessionActive = false;
}

void captureOpeningSnapshot() {
  for (uint8_t i = 0; i < SLOT_COUNT; i++) {
    pillAtLidOpen[i] = pillPresent[i];
  }

  Serial.print("SNAPSHOT|");

  for (uint8_t i = 0; i < SLOT_COUNT; i++) {
    Serial.print(SLOT_NAMES[i]);
    Serial.print("=");
    Serial.print(pillAtLidOpen[i] ? "PRESENT" : "ABSENT");

    if (i < SLOT_COUNT - 1) {
      Serial.print("|");
    }
  }

  Serial.println();
}

// ============================================================
// Sensor handling
// ============================================================

int readAverage(uint8_t pin) {
  long total = 0;

  for (uint8_t i = 0; i < SENSOR_SAMPLE_COUNT; i++) {
    total += analogRead(pin);
    delayMicroseconds(500);
  }

  return static_cast<int>(total / SENSOR_SAMPLE_COUNT);
}

void updatePillSensors() {
  rawValues[MORNING_SLOT] = readAverage(MORNING_SENSOR_PIN);
  rawValues[AFTERNOON_SLOT] = readAverage(AFTERNOON_SENSOR_PIN);
  rawValues[EVENING_SLOT] = readAverage(EVENING_SENSOR_PIN);

  updateStateWithHysteresis(
      rawValues[MORNING_SLOT],
      MORNING_PRESENT_THRESHOLD,
      MORNING_ABSENT_THRESHOLD,
      pillPresent[MORNING_SLOT]
  );

  updateStateWithHysteresis(
      rawValues[AFTERNOON_SLOT],
      AFTERNOON_PRESENT_THRESHOLD,
      AFTERNOON_ABSENT_THRESHOLD,
      pillPresent[AFTERNOON_SLOT]
  );

  updateStateWithHysteresis(
      rawValues[EVENING_SLOT],
      EVENING_PRESENT_THRESHOLD,
      EVENING_ABSENT_THRESHOLD,
      pillPresent[EVENING_SLOT]
  );
}

void updateStateWithHysteresis(
    int rawValue,
    int presentThreshold,
    int absentThreshold,
    bool& state
) {
  if (rawValue >= presentThreshold) {
    state = true;
  } else if (rawValue <= absentThreshold) {
    state = false;
  }
}

// ============================================================
// Physical result logic
// ============================================================

void evaluateRemovedPills() {
  bool removed[SLOT_COUNT] = {false, false, false};
  uint8_t removedCount = 0;
  int8_t onlyRemovedSlot = -1;

  for (uint8_t i = 0; i < SLOT_COUNT; i++) {
    removed[i] = pillAtLidOpen[i] && !pillPresent[i];

    if (removed[i]) {
      removedCount++;
      onlyRemovedSlot = static_cast<int8_t>(i);
    }
  }

  if (removedCount == 0) {
    sendMessage("RESULT|WARNING|NO_PILL_TAKEN");
    noPillBeep();
    showLCD("No pill taken", "Try again");
    return;
  }

  if (removedCount > 1) {
    Serial.print("RESULT|WARNING|");
    Serial.print(removedCount);
    Serial.println("_PILLS_TAKEN");

    Serial.print("REMOVED|");
    printRemovedSlots(removed);

    multiplePillsBeep();
    showLCD("Too many pills", "Return extras");
    return;
  }

  if (expectedSlot == NO_SLOT) {
    Serial.print("RESULT|INFO|ONE_PILL_REMOVED|");
    Serial.println(SLOT_NAMES[onlyRemovedSlot]);

    showLCD("Pill removed", SLOT_NAMES[onlyRemovedSlot]);
    return;
  }

  if (onlyRemovedSlot == expectedSlot) {
    Serial.print("RESULT|SUCCESS|CORRECT_PILL_TAKEN|");
    Serial.println(SLOT_NAMES[onlyRemovedSlot]);

    correctPillBeep();
    showLCD("Dose complete", "Thank you");
  } else {
    Serial.print("RESULT|WARNING|WRONG_PILL_TAKEN|");
    Serial.println(SLOT_NAMES[onlyRemovedSlot]);

    Serial.print("EXPECTED|");
    Serial.println(SLOT_NAMES[expectedSlot]);

    wrongPillBeep();

    String line2 = "Return ";
    line2 += SLOT_NAMES[onlyRemovedSlot];
    showLCD("Wrong pill", line2);
  }
}

// ============================================================
// Output helpers
// ============================================================

void sendMessage(const char* message) {
  Serial.println(message);
}

void printRemovedSlots(const bool removed[]) {
  for (uint8_t i = 0; i < SLOT_COUNT; i++) {
    if (removed[i]) {
      Serial.print(SLOT_NAMES[i]);
      Serial.print("|");
    }
  }

  Serial.println();
}

void printCurrentState() {
  Serial.print("STATE|LID=");
  Serial.print(stableLidOpen ? "OPEN" : "CLOSED");

  Serial.print("|MORNING=");
  Serial.print(pillPresent[MORNING_SLOT] ? "PRESENT" : "ABSENT");

  Serial.print("|AFTERNOON=");
  Serial.print(pillPresent[AFTERNOON_SLOT] ? "PRESENT" : "ABSENT");

  Serial.print("|EVENING=");
  Serial.println(pillPresent[EVENING_SLOT] ? "PRESENT" : "ABSENT");
}

void printRawValues() {
  Serial.print("RAW|MORNING=");
  Serial.print(rawValues[MORNING_SLOT]);

  Serial.print("|AFTERNOON=");
  Serial.print(rawValues[AFTERNOON_SLOT]);

  Serial.print("|EVENING=");
  Serial.println(rawValues[EVENING_SLOT]);
}

// ============================================================
// LCD helpers
// ============================================================

void showLCD(String line1, String line2) {
  lcd.clear();

  lcd.setCursor(0, 0);
  printLCDLine(line1);

  lcd.setCursor(0, 1);
  printLCDLine(line2);
}

void printLCDLine(String text) {
  if (text.length() > 16) {
    text = text.substring(0, 16);
  }

  lcd.print(text);

  for (uint8_t i = text.length(); i < 16; i++) {
    lcd.print(' ');
  }
}

void showExpectedSlot() {
  if (expectedSlot == NO_SLOT) {
    showLCD("Medicine Box", "No dose active");
    return;
  }

  String line2 = SLOT_NAMES[expectedSlot];
  showLCD("Take medicine", line2);
}

// ============================================================
// Buzzer patterns
// ============================================================

void playBeep(
    unsigned int frequency,
    unsigned long durationMs,
    unsigned long pauseMs = 0
) {
  tone(BUZZER_PIN, frequency);
  delay(durationMs);
  noTone(BUZZER_PIN);

  if (pauseMs > 0) {
    delay(pauseMs);
  }
}

void startupBeep() {
  playBeep(1800, 100);
}

void correctPillBeep() {
  playBeep(2200, 180);
}

void reminderBeep() {
  for (uint8_t i = 0; i < 2; i++) {
    playBeep(1500, 250, 180);
  }
}

void lateReminderBeep() {
  for (uint8_t i = 0; i < 4; i++) {
    playBeep(1200, 350, 150);
  }
}

void caregiverReminderBeep() {
  for (uint8_t i = 0; i < 3; i++) {
    playBeep(1700, 300, 200);
  }
}

void wrongPillBeep() {
  for (uint8_t i = 0; i < 3; i++) {
    playBeep(900, 180, 120);
  }
}

void noPillBeep() {
  for (uint8_t i = 0; i < 2; i++) {
    playBeep(1400, 120, 150);
  }
}

void multiplePillsBeep() {
  for (uint8_t i = 0; i < 4; i++) {
    playBeep(700, 250, 120);
  }
}
