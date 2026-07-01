#include <EEPROM.h>

// Hardware Pin Configuration
const int C1_LIMIT_SWITCH = A0;    
const int C1_IR_LED = 6;           
const int C1_PHOTOTRANSISTOR = A1; 
const int BUZZER_PIN = 2;          // Port D2

// System Scheduling Variables (Defaults)
int targetHour = 8;  // Default: Morning dose at 08:00 AM
int targetMinute = 0;
int maxComplianceWindowMinutes = 60; 

// EEPROM Memory Allocation Map
int eepromAddressCounter = 0;
const int MAX_EEPROM_SIZE = 1024;

// Operational States
enum SystemState { SECURE, OPEN_PILL_INSIDE, TAKEN, ERROR_ALREADY_TAKEN };
SystemState currentState = SECURE;

// Track if an event change was already written to memory to avoid duplicating logs
bool loggedTakenThisWindow = false;

// Function to log an event directly to local EEPROM storage
void logEventToEEPROM(char eventCode) {
    if (eepromAddressCounter < (MAX_EEPROM_SIZE - 2)) {
        EEPROM.write(eepromAddressCounter, eventCode);
        // Note: In a full deployment, you'd also write a relative time token here
        eepromAddressCounter++;
        EEPROM.write(eepromAddressCounter, '\n'); // Line terminator for sync bursting
        eepromAddressCounter++;
    }
}

// Function to dump all stored EEPROM logs over BLE when requested by PC
void burstEEPROMLogs() {
    Serial.println("SYNC_START");
    for (int i = 0; i < eepromAddressCounter; i++) {
        char logChar = EEPROM.read(i);
        Serial.print(logChar);
    }
    Serial.println("SYNC_END");
    
    // Clear storage counter after successful synchronization dump
    eepromAddressCounter = 0; 
}

void setup() {
    Serial.begin(9600); // Directly tied to HM-11 over UART
    
    pinMode(C1_LIMIT_SWITCH, INPUT_PULLUP);
    pinMode(C1_IR_LED, OUTPUT);
    pinMode(C1_PHOTOTRANSISTOR, INPUT);
    pinMode(BUZZER_PIN, OUTPUT);
    
    digitalWrite(C1_IR_LED, HIGH);
}

void loop() {
    // Check for incoming remote configuration changes or sync commands from Python backend
    if (Serial.available() > 0) {
        String command = Serial.readStringUntil('\n');
        command.trim();
        
        if (command == "REQ_SYNC") {
            burstEEPROMLogs();
        } else if (command.startsWith("SET_TIME:")) {
            // Expected format: "SET_TIME:HH:MM"
            targetHour = command.substring(9, 11).toInt();
            targetMinute = command.substring(12, 14).toInt();
            // Chirp to confirm settings saved
            tone(BUZZER_PIN, 2000, 100);
        }
    }

    // Read physical hardware sensors
    bool lidIsOpen = (digitalRead(C1_LIMIT_SWITCH) == HIGH);
    bool pillIsRemoved = (digitalRead(C1_PHOTOTRANSISTOR) == LOW);

    // Operational State Machine Evaluator
    if (lidIsOpen && pillIsRemoved) {
        if (!loggedTakenThisWindow) {
            Serial.println("C1:TAKEN");
            logEventToEEPROM('T'); // 'T' for Taken
            loggedTakenThisWindow = true;
        }
        currentState = TAKEN;
        tone(BUZZER_PIN, 3000, 80); // Success chirp
    } 
    else if (lidIsOpen && !pillIsRemoved) {
        if (currentState == TAKEN || loggedTakenThisWindow) {
            // Patient already took it but reopened or re-blocked the box illegally
            Serial.println("C1:WARN_DOUBLE_DOSE");
            logEventToEEPROM('D'); // 'D' for Double-Dose risk
            // Play continuous error tone pattern
            tone(BUZZER_PIN, 400); 
        } else {
            Serial.println("C1:LID_OPEN_PILL_INSIDE");
            // Piercing High-Volume Siren Effect
            tone(BUZZER_PIN, 2500); delay(80);
            tone(BUZZER_PIN, 2900); delay(80);
            noTone(BUZZER_PIN);
        }
        currentState = OPEN_PILL_INSIDE;
    }
    else if (!lidIsOpen && !pillIsRemoved) {
        Serial.println("C1:COMPARTMENT_FULL_AND_CLOSED");
        noTone(BUZZER_PIN);
        currentState = SECURE;
    }
    else if (!lidIsOpen && pillIsRemoved) {
        Serial.println("C1:TAKEN_AND_DONE_WAITING_FOR_NEXT_TIME");
        noTone(BUZZER_PIN);
        currentState = SECURE;
    }

    delay(2000); // 2-second cycle telemetry cadence
}