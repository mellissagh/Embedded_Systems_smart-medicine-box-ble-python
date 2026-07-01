# Embedded_Systems_smart-medicine-box-ble-python
Python BLE communication code for the Smart Medicine Box embedded system, used to connect, send, and receive data between the hardware device and the application.

# Smart Medicine Box BLE Dashboard

This project contains the Python software for the Smart Medicine Box embedded systems project.

The system connects to the medicine box using Bluetooth Low Energy, receives medicine usage data, displays it in a caregiver dashboard, generates adherence reports, and sends alerts through Telegram.

## Features

- BLE connection to the medicine box hardware
- Real-time dashboard using CustomTkinter
- Medicine adherence logging
- Graphical reports using Matplotlib
- Telegram alerts for missed doses or important events

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