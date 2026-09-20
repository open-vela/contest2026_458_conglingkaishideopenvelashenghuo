"""
USB CDC 串口传输层

自动检测板端串口，管理连接/断开/重连。
"""

import asyncio
import serial
import serial.tools.list_ports
import logging
from typing import Optional, Callable, Awaitable
from .protocol import FrameParser, Frame, FrameType, build_ack_frame

logger = logging.getLogger("wristclaw.transport")

# SF32LB52 USB CDC ACM 的 VID/PID (来自 defconfig)
SIFLI_VID = 0x38F4
SIFLI_PID = 0x0001  # CDC ACM 默认 PID


class SerialTransport:
    def __init__(self, port: Optional[str] = None, baudrate: int = 1000000):
        self._port_name = port
        self._baudrate = baudrate
        self._serial: Optional[serial.Serial] = None
        self._parser = FrameParser()
        self._running = False
        self._rx_task: Optional[asyncio.Task] = None
        self._on_frame: Optional[Callable[[Frame], Awaitable[None]]] = None
        self._on_disconnect: Optional[Callable[[], Awaitable[None]]] = None
        self._send_lock = asyncio.Lock()
        self._seq = 0

    @property
    def connected(self) -> bool:
        return self._serial is not None and self._serial.is_open

    @property
    def port_name(self) -> Optional[str]:
        return self._port_name

    def next_seq(self) -> int:
        s = self._seq
        self._seq = (self._seq + 1) & 0xFFFF
        return s

    def set_on_frame(self, callback: Callable[[Frame], Awaitable[None]]):
        self._on_frame = callback

    def set_on_disconnect(self, callback: Callable[[], Awaitable[None]]):
        self._on_disconnect = callback

    def find_device(self) -> Optional[str]:
        """自动检测 SF32LB52 设备串口"""
        ports = serial.tools.list_ports.comports()
        for p in ports:
            logger.debug(f"Found port: {p.device} VID={p.vid} PID={p.pid} desc={p.description}")
            if p.vid == SIFLI_VID:
                return p.device
            # Windows: NuttX CDC ACM 通常出现在 "USB Serial Device" 或 "NuttX CDC"
            if "CDC" in (p.description or "").upper() or "NUTTX" in (p.description or "").upper():
                return p.device
            # WSL: /dev/ttyACM*
            if p.device.startswith("/dev/ttyACM"):
                return p.device
        # 回退：如果没有 VID 匹配，返回第一个可用的 ACM 口
        for p in ports:
            if p.device.startswith("/dev/ttyACM") or "ACM" in p.device:
                return p.device
        return None

    async def connect(self, port: Optional[str] = None) -> bool:
        """连接设备"""
        if self.connected:
            return True

        target_port = port or self._port_name or self.find_device()
        if not target_port:
            logger.warning("No device found")
            return False

        try:
            self._serial = serial.Serial(
                target_port,
                baudrate=self._baudrate,
                timeout=0.1,
                write_timeout=1.0,
            )
            self._port_name = target_port
            self._parser.reset()
            self._running = True
            self._rx_task = asyncio.create_task(self._rx_loop())
            logger.info(f"Connected to {target_port}")
            return True
        except serial.SerialException as e:
            logger.error(f"Failed to connect: {e}")
            self._serial = None
            return False

    async def disconnect(self):
        self._running = False
        if self._rx_task:
            self._rx_task.cancel()
            try:
                await self._rx_task
            except asyncio.CancelledError:
                pass
        if self._serial:
            self._serial.close()
            self._serial = None
        logger.info("Disconnected")

    async def send(self, data: bytes):
        """线程安全发送"""
        if not self.connected:
            raise ConnectionError("Not connected")
        async with self._send_lock:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._serial.write, data)

    async def _rx_loop(self):
        loop = asyncio.get_event_loop()
        while self._running and self.connected:
            try:
                data = await loop.run_in_executor(None, self._serial.read, 4096)
                if data:
                    frames = self._parser.feed(data)
                    for frame in frames:
                        if self._on_frame:
                            try:
                                await self._on_frame(frame)
                            except Exception as e:
                                logger.error(f"Frame handler error: {e}")
                else:
                    # 读超时 (timeout=0.1)，正常继续
                    pass
            except serial.SerialException as e:
                logger.error(f"Serial read error: {e}")
                break
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Unexpected error in rx_loop: {e}")
                await asyncio.sleep(0.1)

        self._running = False
        if self._serial:
            self._serial.close()
            self._serial = None
        if self._on_disconnect:
            await self._on_disconnect()

    async def auto_reconnect(self, interval: float = 3.0):
        """自动重连循环"""
        while True:
            if not self.connected:
                port = self.find_device()
                if port:
                    if await self.connect(port):
                        logger.info(f"Reconnected to {port}")
            await asyncio.sleep(interval)

    def list_ports(self) -> list[dict]:
        """列出所有可用串口"""
        ports = serial.tools.list_ports.comports()
        return [
            {
                "device": p.device,
                "description": p.description,
                "vid": p.vid,
                "pid": p.pid,
                "manufacturer": p.manufacturer,
            }
            for p in ports
        ]
