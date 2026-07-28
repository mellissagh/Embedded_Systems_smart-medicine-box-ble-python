from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime

from bleak import BleakClient, BleakScanner
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.backends.device import BLEDevice

DEVICE_NAME = "HMSoft"
CHARACTERISTIC_UUID = "0000ffe1-0000-1000-8000-00805f9b34fb"
MessageCallback = Callable[[str], Awaitable[None] | None]


@dataclass
class BLEStatus:
    connected: bool = False
    device_name: str = DEVICE_NAME
    device_identifier: str | None = None
    last_message: str | None = None
    last_message_at: datetime | None = None
    last_error: str | None = None


class MedicineBoxBLEClient:
    def __init__(
        self,
        on_message: MessageCallback | None = None,
        device_name: str = DEVICE_NAME,
        characteristic_uuid: str = CHARACTERISTIC_UUID,
        scan_timeout: float = 15.0,
        reconnect_delay: float = 5.0,
    ) -> None:
        self.device_name = device_name
        self.characteristic_uuid = characteristic_uuid
        self.scan_timeout = scan_timeout
        self.reconnect_delay = reconnect_delay
        self.on_message = on_message
        self.status = BLEStatus(device_name=device_name)
        self._client: BleakClient | None = None
        self._receive_buffer = ""
        self._stop_event = asyncio.Event()
        self._write_lock = asyncio.Lock()

    async def find_device(self) -> BLEDevice:
        print(f"Searching for BLE device {self.device_name!r}...")
        device = await BleakScanner.find_device_by_name(
            self.device_name, timeout=self.scan_timeout
        )
        if device is None:
            raise RuntimeError(
                f"BLE device {self.device_name!r} was not found. "
                "Make sure HMSoft is powered and the phone is disconnected."
            )
        self.status.device_identifier = device.address
        print(f"Found {device.name} ({device.address})")
        return device

    async def connect_once(self) -> None:
        device = await self.find_device()
        print("Connecting to medicine box...")
        self._client = BleakClient(device, disconnected_callback=self._handle_disconnect)
        await self._client.connect()
        if not self._client.is_connected:
            raise RuntimeError("BLE connection failed.")
        self.status.connected = True
        self.status.last_error = None
        self._receive_buffer = ""
        print("Connected to HMSoft.")
        await self._client.start_notify(
            self.characteristic_uuid, self._notification_handler
        )
        print("Listening for medicine-box messages...")
        while self._client.is_connected and not self._stop_event.is_set():
            await asyncio.sleep(1)

    async def run_forever(self) -> None:
        self._stop_event.clear()
        while not self._stop_event.is_set():
            try:
                await self.connect_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.status.connected = False
                self.status.last_error = str(exc)
                print(f"BLE error: {exc}")
            if not self._stop_event.is_set():
                print(f"Retrying BLE connection in {self.reconnect_delay:.0f} seconds...")
                await asyncio.sleep(self.reconnect_delay)

    async def send_command(self, command: str) -> bool:
        clean = command.strip()
        if not clean:
            raise ValueError("BLE command cannot be empty.")
        client = self._client
        if client is None or not client.is_connected:
            print(f"[BLE] Command not sent because box is offline: {clean}")
            return False
        payload = (clean + "\n").encode("utf-8")
        async with self._write_lock:
            await client.write_gatt_char(
                self.characteristic_uuid, payload, response=False
            )
        print(f"[BLE → Arduino] {clean}")
        return True

    async def stop(self) -> None:
        self._stop_event.set()
        if self._client is not None:
            try:
                if self._client.is_connected:
                    await self._client.stop_notify(self.characteristic_uuid)
                    await self._client.disconnect()
            except Exception as exc:
                print(f"Error while disconnecting BLE: {exc}")
        self.status.connected = False

    def _handle_disconnect(self, _client: BleakClient) -> None:
        self.status.connected = False
        print("Medicine box BLE disconnected.")

    def _notification_handler(
        self, _characteristic: BleakGATTCharacteristic, data: bytearray
    ) -> None:
        self._receive_buffer += bytes(data).decode("utf-8", errors="ignore")
        while "\n" in self._receive_buffer:
            line, self._receive_buffer = self._receive_buffer.split("\n", 1)
            complete = line.rstrip("\r").strip()
            if complete:
                asyncio.create_task(self._handle_complete_message(complete))

    async def _handle_complete_message(self, message: str) -> None:
        now = datetime.now()
        self.status.last_message = message
        self.status.last_message_at = now
        print(f"[{now:%Y-%m-%d %H:%M:%S}] {message}")
        if self.on_message is None:
            return
        result = self.on_message(message)
        if asyncio.iscoroutine(result):
            await result
