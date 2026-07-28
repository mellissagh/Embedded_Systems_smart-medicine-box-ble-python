# Smart Medicine Box

A smart embedded medicine box consisting of an Arduino-based embedded system and a Python desktop application.

The system monitors medication intake using IR sensors, communicates via Bluetooth Low Energy (HM-11), displays real-time status on an LCD and desktop dashboard, stores medication history, and sends Telegram notifications.

## Project Overview

This repository contains the Python desktop application used to communicate with the embedded hardware.

The Arduino firmware is written in **C++**, while the desktop application is written in **Python**.
Communication between them is performed over **Bluetooth Low Energy (BLE)**.


## Features

✔ Bluetooth Low Energy communication (HM-11)

✔ Real-time medicine status monitoring

✔ Medication adherence tracking

✔ Daily and weekly reports

✔ Telegram notifications

✔ LCD synchronization with hardware

✔ Interactive desktop dashboard

## Technologies Used

- Python
- Bleak
- CustomTkinter
- Matplotlib
- python-telegram-bot
- Bluetooth Low Energy

## Installation

Install the required Python libraries:

```bash
pip install -r requirements.txt
```

## Software

- Arduino Firmware (C/C++)
- Python Desktop Application
- Bluetooth Low Energy (HM-11)
- Telegram Bot API

  
 ## Hardware Components

| Component | Quantity | Purpose |
|-----------|---------:|---------|
| Arduino Uno R3 | 1 | Main controller |
| HM-11 BLE Module | 1 | Wireless communication |
| LCD 16×2 | 1 | Display system status |
| IR LEDs | 3 | Emit infrared light |
| Phototransistors | 3 | Detect medication presence |
| Microswitch | 1 | Detect lid opening/closing |
| Breadboard | 1 | Circuit prototyping |
| Resistors | As needed | Current limiting and pull-up/pull-down |
| Jumper Wires | As needed | Electrical connections |


