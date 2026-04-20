"""QR code generation for cryptocurrency payment addresses."""

import io
from typing import Optional

import qrcode
import qrcode.image.svg
from qrcode.image.pure import PyPNGImage

from price_engine import COIN_REGISTRY
from logger_setup import get_logger

log = get_logger(__name__)


class QRGenerator:
    """Generate QR code images for payment URIs."""

    def __init__(self, box_size: int = 10, border: int = 4) -> None:
        """Initialise the QR generator.

        Args:
            box_size: Size of each QR module in pixels.
            border: Quiet-zone border width in modules.
        """
        self._box_size = box_size
        self._border = border

    def _build_uri(self, coin: str, address: str, amount: Optional[str] = None) -> str:
        """Construct a BIP-21 / coin-specific payment URI.

        Args:
            coin: Coin ticker (e.g. ``"BTC"``).
            address: Public wallet address.
            amount: Optional crypto amount to embed in the URI.

        Returns:
            Payment URI string.
        """
        meta = COIN_REGISTRY.get(coin.upper(), {})
        prefix = meta.get("qr_prefix", "")
        uri = f"{prefix}{address}"
        if amount:
            uri += f"?amount={amount}"
        return uri

    def generate_png(
        self,
        coin: str,
        address: str,
        amount: Optional[str] = None,
    ) -> bytes:
        """Generate a PNG QR code for the given address.

        Args:
            coin: Coin ticker.
            address: Public wallet address.
            amount: Optional crypto amount.

        Returns:
            PNG image as raw bytes.
        """
        uri = self._build_uri(coin, address, amount)
        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=self._box_size,
            border=self._border,
        )
        qr.add_data(uri)
        qr.make(fit=True)

        img = qr.make_image(fill_color="black", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        log.debug(f"Generated PNG QR for {coin} address: {address}")
        return buf.getvalue()

    def generate_svg(
        self,
        coin: str,
        address: str,
        amount: Optional[str] = None,
    ) -> str:
        """Generate an SVG QR code for the given address.

        Args:
            coin: Coin ticker.
            address: Public wallet address.
            amount: Optional crypto amount.

        Returns:
            SVG XML as a string.
        """
        uri = self._build_uri(coin, address, amount)
        factory = qrcode.image.svg.SvgPathImage
        qr = qrcode.make(uri, image_factory=factory)
        buf = io.BytesIO()
        qr.save(buf)
        log.debug(f"Generated SVG QR for {coin} address: {address}")
        return buf.getvalue().decode("utf-8")
