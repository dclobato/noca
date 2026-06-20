#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""QR Code generation service for Arena.

Provides an abstract generator interface and a concrete PIL-based implementation,
plus a convenience ``QRCodeService`` wrapper with TOTP URI support.
"""

import base64
from abc import ABC, abstractmethod
from dataclasses import dataclass
from io import BytesIO
from urllib.parse import quote


@dataclass
class QRCodeConfig:
    """Configuration for QR Code generation.

    Attributes:
        box_size: Size of each QR code box in pixels.
        border: Width of the quiet-zone border in boxes.
        fill_color: Foreground (module) colour.
        back_color: Background colour.

    Raises:
        ValueError: If ``box_size`` < 1 or ``border`` < 0.
    """

    box_size: int = 10
    border: int = 4
    fill_color: str = "black"
    back_color: str = "white"

    def __post_init__(self) -> None:
        if self.box_size < 1:
            raise ValueError("box_size must be >= 1")
        if self.border < 0:
            raise ValueError("border must be >= 0")


class QRCodeError(Exception):
    """Raised when QR Code generation fails."""


class QRCodeGenerator(ABC):
    """Abstract interface for QR Code generators."""

    @abstractmethod
    def generate(self, data: str, config: QRCodeConfig) -> bytes:
        """Generate a QR Code image from the given data.

        Args:
            data: String to encode.
            config: Generation configuration.

        Returns:
            bytes: PNG-encoded QR Code image.

        Raises:
            QRCodeError: If generation fails.
        """

    @abstractmethod
    def get_generator_name(self) -> str:
        """Return the name of this generator implementation.

        Returns:
            str: Generator name.
        """


class QRCodePILGenerator(QRCodeGenerator):
    """Concrete QR Code generator using the ``qrcode`` and ``PIL`` libraries."""

    def generate(self, data: str, config: QRCodeConfig) -> bytes:
        """Generate a PNG QR Code using the qrcode/PIL stack.

        Args:
            data: String to encode.
            config: Generation configuration.

        Returns:
            bytes: PNG-encoded QR Code image.

        Raises:
            QRCodeError: If the qrcode or PIL library is unavailable.
        """
        try:
            from qrcode.image.pil import PilImage
            from qrcode.main import QRCode
        except ImportError as exc:
            raise QRCodeError("qrcode[pil] package is required") from exc

        qr = QRCode(
            version=None,
            box_size=config.box_size,
            border=config.border,
            image_factory=PilImage,
        )
        qr.add_data(data)
        qr.make(fit=True)
        img = qr.make_image(fill_color=config.fill_color, back_color=config.back_color)
        buf = BytesIO()
        img.save(buf)
        return buf.getvalue()

    def get_generator_name(self) -> str:
        """Return the generator name.

        Returns:
            str: "QRCodePILGenerator"
        """
        return "QRCodePILGenerator"


class QRCodeService:
    """High-level QR Code service with TOTP URI support.

    Attributes:
        generator: Underlying QR Code generator implementation.
    """

    def __init__(self, generator: QRCodeGenerator) -> None:
        """Initialise the service with the given generator.

        Args:
            generator: Concrete QR Code generator to use.
        """
        self.generator = generator

    @classmethod
    def create_default(cls) -> QRCodeService:
        """Create a service instance backed by the default PIL generator.

        Returns:
            QRCodeService: Ready-to-use service instance.
        """
        return cls(generator=QRCodePILGenerator())

    def generate_qr_code(
        self,
        data: str,
        config: QRCodeConfig | None = None,
        as_bytes: bool = True,
    ) -> bytes | str:
        """Generate a QR Code from arbitrary data.

        Args:
            data: String to encode.
            config: Optional generation config; defaults to ``QRCodeConfig()``.
            as_bytes: If ``True`` (default) return raw PNG bytes; otherwise
                return a base64-encoded string.

        Returns:
            bytes | str: PNG bytes or base64 string.

        Raises:
            QRCodeError: If ``data`` is empty or generation fails.
        """
        if not data:
            raise QRCodeError("data must not be empty")
        config = config or QRCodeConfig()
        raw = self.generator.generate(data, config)
        return raw if as_bytes else base64.b64encode(raw).decode("utf-8")

    def generate_totp_qrcode(
        self,
        secret: str,
        user: str,
        issuer: str,
        config: QRCodeConfig | None = None,
        as_bytes: bool = True,
    ) -> bytes | str:
        """Generate a QR Code for TOTP setup in authenticator apps.

        Builds an ``otpauth://totp/...`` URI and encodes it as a QR Code.

        Args:
            secret: Base32-encoded TOTP secret.
            user: User identifier (typically the email address).
            issuer: Service or application name shown in the authenticator.
            config: Optional generation config; defaults to ``QRCodeConfig()``.
            as_bytes: If ``True`` (default) return raw PNG bytes; otherwise
                return a base64-encoded string.

        Returns:
            bytes | str: PNG bytes or base64 string.

        Raises:
            QRCodeError: If any required argument is empty.
        """
        if not all([secret, user, issuer]):
            raise QRCodeError("secret, user, and issuer are all required")
        config = config or QRCodeConfig()
        label = quote(f"{issuer}:{user}")
        params = f"secret={secret}&issuer={quote(issuer)}"
        totp_uri = f"otpauth://totp/{label}?{params}"
        return self.generate_qr_code(totp_uri, config, as_bytes=as_bytes)
