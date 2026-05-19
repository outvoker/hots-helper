"""macOS-only global hotkey backend using Carbon ``RegisterEventHotKey``.

This is the API Alfred / Raycast / Spark / 30 years of Mac
productivity software use to grab a hotkey for their own app. It
does *not* read the system keyboard stream (so it never collides
with the IME composition path that crashes pynput's listener), and
it does *not* require Accessibility permission — macOS routes the
key event straight to your app's Carbon event dispatcher.

The handler runs on the main thread inside the regular Cocoa /
Carbon event loop, which Qt's native event loop already pumps. We
trampoline through ``QTimer.singleShot(0, ...)`` so the user-
facing callback runs on a clean tick instead of from inside Apple's
event-handler frame, which is mildly hostile to long-running work.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import logging
import sys
from ctypes import (
    CFUNCTYPE,
    POINTER,
    Structure,
    byref,
    c_int32,
    c_uint32,
    c_void_p,
)
from typing import Callable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Carbon constants & ctypes glue.
# ---------------------------------------------------------------------------

# Modifier mask bits as defined in Carbon/HIToolbox/Events.h. Note these
# are the *Carbon* values, not the Cocoa NSEventModifierFlags.
_K_CMD     = 1 << 8   # 256
_K_SHIFT   = 1 << 9   # 512
_K_OPTION  = 1 << 11  # 2048
_K_CONTROL = 1 << 12  # 4096

# Mac virtual keycodes for the keys we actually expose. Based on
# ``HIToolbox/Events.h`` (kVK_ANSI_*). Letters first, then function keys.
_VK_TABLE: dict[str, int] = {
    "a": 0x00, "s": 0x01, "d": 0x02, "f": 0x03, "h": 0x04,
    "g": 0x05, "z": 0x06, "x": 0x07, "c": 0x08, "v": 0x09,
    "b": 0x0B, "q": 0x0C, "w": 0x0D, "e": 0x0E, "r": 0x0F,
    "y": 0x10, "t": 0x11, "1": 0x12, "2": 0x13, "3": 0x14,
    "4": 0x15, "6": 0x16, "5": 0x17, "9": 0x19, "7": 0x1A,
    "8": 0x1C, "0": 0x1D, "o": 0x1F, "u": 0x20, "i": 0x22,
    "p": 0x23, "l": 0x25, "j": 0x26, "k": 0x28, "n": 0x2D,
    "m": 0x2E,
    "f1": 0x7A, "f2": 0x78, "f3": 0x63, "f4": 0x76,
    "f5": 0x60, "f6": 0x61, "f7": 0x62, "f8": 0x64,
    "f9": 0x65, "f10": 0x6D, "f11": 0x67, "f12": 0x6F,
    "space": 0x31, "tab": 0x30, "return": 0x24, "enter": 0x4C,
    "esc": 0x35, "escape": 0x35,
}


class _EventHotKeyID(Structure):
    _fields_ = [("signature", c_uint32), ("id", c_uint32)]


class _EventTypeSpec(Structure):
    _fields_ = [("eventClass", c_uint32), ("eventKind", c_uint32)]


# OSStatus = SInt32; Carbon callback signature is
# (EventHandlerCallRef, EventRef, void* userData) -> OSStatus.
_EventHandlerProcPtr = CFUNCTYPE(c_int32, c_void_p, c_void_p, c_void_p)


def _fourcc(s: str) -> int:
    return (ord(s[0]) << 24) | (ord(s[1]) << 16) | (ord(s[2]) << 8) | ord(s[3])


_CARBON = None
if sys.platform == "darwin":
    try:
        _CARBON = ctypes.CDLL(ctypes.util.find_library("Carbon"))
    except Exception as e:
        logger.warning("Carbon framework unavailable: %s", e)
        _CARBON = None
    if _CARBON is not None:
        # We register against GetApplicationEventTarget(), not
        # GetEventDispatcherTarget(). Qt's Cocoa platform plug-in
        # pumps the application target through the NSApp run loop
        # but does *not* propagate events into the dispatcher target,
        # so registering on the dispatcher silently swallows the
        # hotkey events. Caught this with a manual A/B test against
        # both targets.
        _CARBON.GetApplicationEventTarget.restype = c_void_p
        _CARBON.GetApplicationEventTarget.argtypes = []
        _CARBON.RegisterEventHotKey.restype = c_int32
        _CARBON.RegisterEventHotKey.argtypes = [
            c_uint32, c_uint32, _EventHotKeyID, c_void_p, c_uint32,
            POINTER(c_void_p),
        ]
        _CARBON.UnregisterEventHotKey.restype = c_int32
        _CARBON.UnregisterEventHotKey.argtypes = [c_void_p]
        _CARBON.InstallEventHandler.restype = c_int32
        _CARBON.InstallEventHandler.argtypes = [
            c_void_p, _EventHandlerProcPtr, c_uint32,
            POINTER(_EventTypeSpec), c_void_p, POINTER(c_void_p),
        ]
        _CARBON.RemoveEventHandler.restype = c_int32
        _CARBON.RemoveEventHandler.argtypes = [c_void_p]


_K_EVENT_CLASS_KEYBOARD = _fourcc("keyb")
_K_EVENT_HOT_KEY_PRESSED = 5


def is_available() -> bool:
    """``True`` when Carbon is loadable on this platform — i.e. macOS."""
    return _CARBON is not None


# ---------------------------------------------------------------------------
# pynput-style combo string parsing.
# ---------------------------------------------------------------------------


def _parse_combo(combo: str) -> tuple[int, int] | None:
    """Convert ``"<ctrl>+<shift>+t"`` into ``(vk, modifier_mask)``.

    Returns ``None`` if the combo doesn't have a non-modifier key or
    if the key isn't in our virtual-keycode table. The combo format
    matches the pynput conventions the rest of the app already uses.
    """
    parts = [p.strip().lower() for p in combo.split("+") if p.strip()]
    mods = 0
    base: str | None = None
    for raw in parts:
        token = raw.strip("<> ")
        if token in ("ctrl", "control"):
            mods |= _K_CONTROL
        elif token == "shift":
            mods |= _K_SHIFT
        elif token in ("alt", "option", "opt"):
            mods |= _K_OPTION
        elif token in ("cmd", "command", "meta", "win"):
            mods |= _K_CMD
        else:
            base = token
    if base is None:
        return None
    vk = _VK_TABLE.get(base)
    if vk is None:
        return None
    return vk, mods


# ---------------------------------------------------------------------------
# Public registry.
# ---------------------------------------------------------------------------


class CarbonHotkeyManager:
    """Owns a single global hotkey via Carbon.

    Mirrors the surface of :class:`hots_helper.ui.hotkey.HotkeyManager`
    so the main window can swap one for the other on darwin. Only one
    combo per instance — re-registering replaces the previous binding.

    The Carbon event handler is installed lazily on the first
    ``set_hotkey`` call and lives forever (cheap, no thread). Each
    registered combo gets a unique hotkey-id so the dispatcher can
    map the event back to the right ``on_fire`` callback.
    """

    _next_id = 1
    _shared_handler_installed = False
    _shared_handler_ref = c_void_p()
    # Module-level so the C bridge can find the right callback for a
    # given hotkey id. Keyed by id, value = python callable.
    _id_to_callback: dict[int, "Callable[[], None]"] = {}
    # Need to keep refs to anything we hand to Carbon — otherwise
    # ctypes will GC the trampoline and we'll segfault on the next
    # event.
    _trampoline_ref: "_EventHandlerProcPtr | None" = None

    def __init__(self, on_fire: Callable[[], None]) -> None:
        self._on_fire = on_fire
        self._registered_ref: c_void_p | None = None
        self._registered_id: int | None = None
        self._current_combo: str = ""

    # --- Carbon plumbing ---------------------------------------------------

    @classmethod
    def _ensure_handler_installed(cls) -> None:
        if cls._shared_handler_installed or _CARBON is None:
            return

        # Bind GetEventParameter once up front so the trampoline body
        # doesn't rebind argtypes on every event.
        _CARBON.GetEventParameter.argtypes = [
            c_void_p, c_uint32, c_uint32, POINTER(c_uint32),
            c_uint32, POINTER(c_uint32), c_void_p,
        ]
        _CARBON.GetEventParameter.restype = c_int32

        # Plain Python function — wrap with the CFUNCTYPE *outside*
        # so we keep one reference to a single trampoline object on
        # the class. Earlier this lived inside the nested function +
        # got assigned to cls._trampoline_ref, but the assignment
        # races with ctypes GC: the inner CFUNCTYPE wrapper has been
        # observed to be reaped between InstallEventHandler returning
        # and the first real key press, so the OS calls into freed
        # memory and silently does nothing.
        def _impl(call_ref, event_ref, user_data):
            logger.warning("[carbon-hk] trampoline FIRED")
            try:
                hk_id = _EventHotKeyID()
                # Carbon constant lookup gotcha:
                #   kEventParamDirectObject = '----'   (the param NAME)
                #   typeEventHotKeyID       = 'hkid'   (the param TYPE)
                # I had both as 'hkid' originally, which made the call
                # succeed but return an empty struct (id=0), so every
                # press resolved to no callback.
                rc = _CARBON.GetEventParameter(
                    event_ref,
                    _fourcc("----"),    # kEventParamDirectObject
                    _fourcc("hkid"),    # typeEventHotKeyID
                    None,
                    ctypes.sizeof(_EventHotKeyID),
                    None,
                    ctypes.byref(hk_id),
                )
                if rc != 0:
                    logger.warning(
                        "[carbon-hk] GetEventParameter rc=%d", rc
                    )
                hk_int = int(hk_id.id)
            except Exception:
                logger.exception("[carbon-hk] GetEventParameter failed")
                hk_int = -1
            cb = cls._id_to_callback.get(hk_int)
            logger.warning("[carbon-hk] id=%s cb=%s", hk_int, cb)
            if cb is not None:
                try:
                    from PySide6.QtCore import QTimer
                    QTimer.singleShot(0, cb)
                except Exception:
                    try:
                        cb()
                    except Exception:
                        logger.exception("hotkey callback failed")
            return 0

        # Critical: assign *before* InstallEventHandler so the
        # CFUNCTYPE wrapper has a strong reference held on the class
        # the moment the OS could possibly call it back.
        cls._trampoline_ref = _EventHandlerProcPtr(_impl)

        spec = _EventTypeSpec(
            eventClass=_K_EVENT_CLASS_KEYBOARD,
            eventKind=_K_EVENT_HOT_KEY_PRESSED,
        )
        target = _CARBON.GetApplicationEventTarget()
        rc = _CARBON.InstallEventHandler(
            target, cls._trampoline_ref, 1, byref(spec), None,
            byref(cls._shared_handler_ref),
        )
        if rc != 0:
            logger.warning("[carbon-hk] InstallEventHandler returned %d", rc)
            cls._trampoline_ref = None
            return
        logger.warning("[carbon-hk] handler installed, target=%s", target)
        cls._shared_handler_installed = True

    # --- public API --------------------------------------------------------

    @property
    def hotkey(self) -> str:
        return self._current_combo

    def set_hotkey(self, combo: str) -> tuple[bool, str]:
        """Register ``combo`` as a global hotkey. Returns
        ``(ok, message)``; on failure ``message`` is a user-facing
        reason.
        """
        if _CARBON is None:
            return False, "Carbon framework unavailable"
        self.stop()
        if not combo:
            return True, ""

        parsed = _parse_combo(combo)
        if parsed is None:
            return False, f"unrecognised key in {combo!r}"
        vk, mods = parsed
        logger.warning(
            "[carbon-hk] set_hotkey combo=%r → vk=0x%x mods=0x%x",
            combo, vk, mods,
        )

        self._ensure_handler_installed()
        cls = type(self)
        hk_id = cls._next_id
        cls._next_id += 1
        ref = c_void_p()
        rc = _CARBON.RegisterEventHotKey(
            vk,
            mods,
            _EventHotKeyID(signature=_fourcc("HTMe"), id=hk_id),
            _CARBON.GetApplicationEventTarget(),
            0,
            byref(ref),
        )
        logger.warning(
            "[carbon-hk] RegisterEventHotKey rc=%d ref=%s id=%d",
            rc, ref.value, hk_id,
        )
        if rc != 0:
            return False, f"RegisterEventHotKey failed (status {rc})"
        cls._id_to_callback[hk_id] = self._on_fire
        self._registered_ref = ref
        self._registered_id = hk_id
        self._current_combo = combo
        return True, ""

    def stop(self) -> None:
        if _CARBON is None or self._registered_ref is None:
            return
        try:
            _CARBON.UnregisterEventHotKey(self._registered_ref)
        except Exception:
            pass
        if self._registered_id is not None:
            type(self)._id_to_callback.pop(self._registered_id, None)
        self._registered_ref = None
        self._registered_id = None
        self._current_combo = ""
