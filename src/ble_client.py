from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime

from bleak import BleakClient, BleakScanner
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.backends.device import BLEDevice


DEVICE_NAME = "HMSoft"
NOTIFY_UUID = "0000ffe1-0000-1000-8000-00805f9b34fb"

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
    """
    Connects to the HMSoft BLE module and emits complete Arduino lines.

    HMSoft may split a long Arduino message across several BLE packets.
    This class collects those packets until it receives a newline.
    """

    def __init__(
        self,
        on_message: MessageCallback | None = None,
        device_name: str = DEVICE_NAME,
        notify_uuid: str = NOTIFY_UUID,
        scan_timeout: float = 15.0,
        reconnect_delay: float = 5.0,
    ) -> None:
        self.device_name = device_name
        self.notify_uuid = notify_uuid
        self.scan_timeout = scan_timeout
        self.reconnect_delay = reconnect_delay
        self.on_message = on_message

        self.status = BLEStatus(device_name=device_name)

        self._client: BleakClient | None = None
        self._receive_buffer = ""
        self._stop_event = asyncio.Event()

    async def find_device(self) -> BLEDevice:
        print(f"Searching for BLE device {self.device_name!r}...")

        device = await BleakScanner.find_device_by_name(
            self.device_name,
            timeout=self.scan_timeout,
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

        self._client = BleakClient(
            device,
            disconnected_callback=self._handle_disconnect,
        )

        await self._client.connect()

        if not self._client.is_connected:
            raise RuntimeError("BLE connection failed.")

        self.status.connected = True
        self.status.last_error = None
        self._receive_buffer = ""

        print("Connected to HMSoft.")

        await self._client.start_notify(
            self.notify_uuid,
            self._notification_handler,
        )

        print("Listening for medicine-box messages...")

        while (
            self._client.is_connected
            and not self._stop_event.is_set()
        ):
            await asyncio.sleep(1)

    async def run_forever(self) -> None:
        """
        Keep reconnecting until stop() is called.
        """

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
                print(
                    f"Retrying BLE connection in "
                    f"{self.reconnect_delay:.0f} seconds..."
                )
                await asyncio.sleep(self.reconnect_delay)

    async def stop(self) -> None:
        self._stop_event.set()

        if self._client is not None:
            try:
                if self._client.is_connected:
                    await self._client.stop_notify(self.notify_uuid)
                    await self._client.disconnect()
            except Exception as exc:
                print(f"Error while disconnecting BLE: {exc}")

        self.status.connected = False

    def _handle_disconnect(self, _client: BleakClient) -> None:
        self.status.connected = False
        print("Medicine box BLE disconnected.")

    def _notification_handler(
        self,
        _characteristic: BleakGATTCharacteristic,
        data: bytearray,
    ) -> None:
        """
        Called by Bleak whenever HMSoft sends a BLE notification.
        """

        packet_text = bytes(data).decode(
            "utf-8",
            errors="ignore",
        )

        self._receive_buffer += packet_text

        while "\n" in self._receive_buffer:
            line, self._receive_buffer = self._receive_buffer.split(
                "\n",
                1,
            )

            complete_message = line.rstrip("\r").strip()

            if complete_message:
                asyncio.create_task(
                    self._handle_complete_message(complete_message)
                )

    async def _handle_complete_message(
        self,
        message: str,
    ) -> None:
        now = datetime.now()

        self.status.last_message = message
        self.status.last_message_at = now

        print(
            f"[{now:%Y-%m-%d %H:%M:%S}] "
            f"{message}"
        )

        if self.on_message is None:
            return

        result = self.on_message(message)

        if asyncio.iscoroutine(result):
            await result


async def test_listener() -> None:
    client = MedicineBoxBLEClient()

    try:
        await client.run_forever()
    finally:
        await client.stop()


if __name__ == "__main__":
    try:
        asyncio.run(test_listener())
    except KeyboardInterrupt:
        print("\nBLE listener stopped.")