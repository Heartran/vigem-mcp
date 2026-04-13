"""
vigem_mcp — MCP server locale per controllo controller virtuali Xbox 360 e DS4 via VigEm.

Espone tool per navigare i menu di WWE 2K26 tramite input controller,
bypassando il problema di Esc con computer use.

Supporta multi-controller (Xbox 360 e DualShock 4), combo, trigger,
hold/release, macro e gestione dinamica dei controller.

Dipendenze: mcp[cli], vgamepad
Avvio:      python vigem_server.py
Transport:  stdio (locale)
"""

import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import dataclass
from importlib.metadata import version
from typing import Any

import vgamepad as vg
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field, ConfigDict


# ---------------------------------------------------------------------------
# Costanti
# ---------------------------------------------------------------------------

# Mappa nome → costante VigEm per i tool che accettano button come stringa (Xbox 360)
BUTTON_MAP: dict[str, int] = {
    "A":     vg.XUSB_BUTTON.XUSB_GAMEPAD_A,
    "B":     vg.XUSB_BUTTON.XUSB_GAMEPAD_B,
    "X":     vg.XUSB_BUTTON.XUSB_GAMEPAD_X,
    "Y":     vg.XUSB_BUTTON.XUSB_GAMEPAD_Y,
    "UP":    vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_UP,
    "DOWN":  vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_DOWN,
    "LEFT":  vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_LEFT,
    "RIGHT": vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_RIGHT,
    "LB":    vg.XUSB_BUTTON.XUSB_GAMEPAD_LEFT_SHOULDER,
    "RB":    vg.XUSB_BUTTON.XUSB_GAMEPAD_RIGHT_SHOULDER,
    "START": vg.XUSB_BUTTON.XUSB_GAMEPAD_START,
    "BACK":  vg.XUSB_BUTTON.XUSB_GAMEPAD_BACK,
    "L3":    vg.XUSB_BUTTON.XUSB_GAMEPAD_LEFT_THUMB,
    "R3":    vg.XUSB_BUTTON.XUSB_GAMEPAD_RIGHT_THUMB,
    "GUIDE": vg.XUSB_BUTTON.XUSB_GAMEPAD_GUIDE,
}

VALID_BUTTONS = list(BUTTON_MAP.keys())

# Mappe DS4
DS4_BUTTON_MAP = {
    "CROSS":    vg.DS4_BUTTONS.DS4_BUTTON_CROSS,
    "CIRCLE":   vg.DS4_BUTTONS.DS4_BUTTON_CIRCLE,
    "SQUARE":   vg.DS4_BUTTONS.DS4_BUTTON_SQUARE,
    "TRIANGLE": vg.DS4_BUTTONS.DS4_BUTTON_TRIANGLE,
    "L1":       vg.DS4_BUTTONS.DS4_BUTTON_SHOULDER_LEFT,
    "R1":       vg.DS4_BUTTONS.DS4_BUTTON_SHOULDER_RIGHT,
    "L2":       vg.DS4_BUTTONS.DS4_BUTTON_TRIGGER_LEFT,
    "R2":       vg.DS4_BUTTONS.DS4_BUTTON_TRIGGER_RIGHT,
    "L3":       vg.DS4_BUTTONS.DS4_BUTTON_THUMB_LEFT,
    "R3":       vg.DS4_BUTTONS.DS4_BUTTON_THUMB_RIGHT,
    "SHARE":    vg.DS4_BUTTONS.DS4_BUTTON_SHARE,
    "OPTIONS":  vg.DS4_BUTTONS.DS4_BUTTON_OPTIONS,
}

DS4_DPAD_MAP = {
    "UP":         vg.DS4_DPAD_DIRECTIONS.DS4_BUTTON_DPAD_NORTH,
    "DOWN":       vg.DS4_DPAD_DIRECTIONS.DS4_BUTTON_DPAD_SOUTH,
    "LEFT":       vg.DS4_DPAD_DIRECTIONS.DS4_BUTTON_DPAD_WEST,
    "RIGHT":      vg.DS4_DPAD_DIRECTIONS.DS4_BUTTON_DPAD_EAST,
    "UP_RIGHT":   vg.DS4_DPAD_DIRECTIONS.DS4_BUTTON_DPAD_NORTHEAST,
    "UP_LEFT":    vg.DS4_DPAD_DIRECTIONS.DS4_BUTTON_DPAD_NORTHWEST,
    "DOWN_RIGHT": vg.DS4_DPAD_DIRECTIONS.DS4_BUTTON_DPAD_SOUTHEAST,
    "DOWN_LEFT":  vg.DS4_DPAD_DIRECTIONS.DS4_BUTTON_DPAD_SOUTHWEST,
    "NONE":       vg.DS4_DPAD_DIRECTIONS.DS4_BUTTON_DPAD_NONE,
}

DS4_SPECIAL_MAP = {
    "PS":       vg.DS4_SPECIAL_BUTTONS.DS4_SPECIAL_BUTTON_PS,
    "TOUCHPAD": vg.DS4_SPECIAL_BUTTONS.DS4_SPECIAL_BUTTON_TOUCHPAD,
}

DS4_VALID_BUTTONS = list(DS4_BUTTON_MAP.keys()) + list(DS4_DPAD_MAP.keys()) + list(DS4_SPECIAL_MAP.keys())

DEFAULT_DURATION   = 0.08   # secondi — quanto resta premuto
DEFAULT_POST_DELAY = 0.06   # secondi — pausa dopo il rilascio
DEFAULT_BETWEEN    = 0.12   # secondi — pausa tra press consecutive in press_n

# Limite massimo di esecuzione per evitare timeout di Claude Desktop (~60s).
# Qualsiasi tool che stima una durata superiore abortisce immediatamente con errore.
MAX_TOOL_DURATION = 45.0   # secondi


# ---------------------------------------------------------------------------
# Multi-controller registry
# ---------------------------------------------------------------------------

@dataclass
class ControllerSlot:
    """Slot per un controller virtuale registrato."""
    gamepad: vg.VX360Gamepad | vg.VDS4Gamepad
    controller_type: str  # "xbox360" | "ds4"

_controllers: dict[int, ControllerSlot] = {}
_active_id: int = 0
_next_id: int = 0


# ---------------------------------------------------------------------------
# Lifespan — crea Xbox 360 (ID 0) e condivide tra tutti i tool
# ---------------------------------------------------------------------------

# Timeout per l'inizializzazione del gamepad (ms VX360Gamepad() è sincrono/bloccante).
# Claude Desktop va in timeout se il server non risponde entro ~10s all'init MCP.
# Usiamo run_in_executor per non bloccare il loop + asyncio.wait_for per timeout.
INIT_TIMEOUT = 8.0   # secondi — oltre questo, il lifespan fallisce con errore chiaro

@asynccontextmanager
async def lifespan():
    global _next_id, _active_id
    loop = asyncio.get_event_loop()
    try:
        gp = await asyncio.wait_for(
            loop.run_in_executor(None, vg.VX360Gamepad),
            timeout=INIT_TIMEOUT,
        )
    except asyncio.TimeoutError:
        raise RuntimeError(
            f"Timeout inizializzazione ViGEmBus dopo {INIT_TIMEOUT}s. "
            "Verificare che il driver ViGEmBus sia installato e attivo."
        )
    except Exception as e:
        raise RuntimeError(f"Errore inizializzazione ViGEmBus: {e}") from e
    _controllers[0] = ControllerSlot(gamepad=gp, controller_type="xbox360")
    _active_id = 0
    _next_id = 1
    yield
    _controllers.clear()
    _active_id = 0
    _next_id = 0


mcp = FastMCP("vigem_mcp", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Lock asincrono (lazy-init)
# ---------------------------------------------------------------------------

_lock: asyncio.Lock | None = None


def _get_lock() -> asyncio.Lock:
    """Restituisce il lock globale, creandolo al primo accesso."""
    global _lock
    if _lock is None:
        _lock = asyncio.Lock()
    return _lock


# ---------------------------------------------------------------------------
# Helper interni
# ---------------------------------------------------------------------------

def _error_response(e: Exception) -> str:
    """Formatta un errore come risposta JSON standardizzata."""
    return json.dumps({"ok": False, "error": type(e).__name__, "message": str(e)})


def _check_duration(estimated: float) -> str | None:
    """Restituisce un avviso se la durata stimata supera MAX_TOOL_DURATION, altrimenti None.

    Il tool continua ad eseguire normalmente; l'avviso viene incluso nella
    risposta JSON come campo "warn" affinché l'agente sia consapevole dei
    rischi di timeout di Claude Desktop.

    Args:
        estimated: Durata stimata in secondi.

    Returns:
        Stringa di avviso se estimated > MAX_TOOL_DURATION, altrimenti None.
    """
    if estimated > MAX_TOOL_DURATION:
        return (
            f"Durata stimata {estimated:.1f}s supera {MAX_TOOL_DURATION}s "
            f"(timeout Claude Desktop ~60s). Il tool è in esecuzione ma potrebbe "
            f"non ricevere risposta. Considera di ridurre n, duration, post_delay o delay_between."
        )
    return None


def _get_active() -> ControllerSlot:
    """Restituisce lo slot del controller attivo."""
    if _active_id not in _controllers:
        raise RuntimeError("Nessun controller attivo. Il server è avviato correttamente?")
    return _controllers[_active_id]


def _resolve_button(button: str, controller_type: str | None = None) -> int:
    """Risolve il nome di un bottone nella costante VigEm appropriata."""
    ct = controller_type or _get_active().controller_type
    key = button.upper()
    if ct == "ds4":
        # Per DS4 i bottoni normali vanno in DS4_BUTTON_MAP
        if key in DS4_BUTTON_MAP:
            return DS4_BUTTON_MAP[key]
        # dpad e special sono gestiti da _resolve_button_ds4
        if key in DS4_DPAD_MAP or key in DS4_SPECIAL_MAP:
            raise ValueError(
                f"Tasto '{button}' è dpad/special DS4. Usa _resolve_button_ds4 per questo tipo."
            )
        raise ValueError(
            f"Tasto '{button}' non valido per DS4. Tasti disponibili: {', '.join(DS4_VALID_BUTTONS)}"
        )
    # Xbox 360
    if key not in BUTTON_MAP:
        raise ValueError(
            f"Tasto '{button}' non valido. Tasti disponibili: {', '.join(VALID_BUTTONS)}"
        )
    return BUTTON_MAP[key]


def _resolve_button_ds4(button: str) -> tuple[str, Any]:
    """Risolve un bottone DS4 nel tipo ('button'|'dpad'|'special') e valore."""
    key = button.upper()
    if key in DS4_BUTTON_MAP:
        return ("button", DS4_BUTTON_MAP[key])
    if key in DS4_DPAD_MAP:
        return ("dpad", DS4_DPAD_MAP[key])
    if key in DS4_SPECIAL_MAP:
        return ("special", DS4_SPECIAL_MAP[key])
    raise ValueError(
        f"Tasto DS4 '{button}' non valido. Tasti disponibili: {', '.join(DS4_VALID_BUTTONS)}"
    )


async def _press_button(button_const: int, duration: float, post_delay: float) -> None:
    """Preme e rilascia un bottone Xbox 360 con timing specificati."""
    gp = _get_active().gamepad
    gp.press_button(button=button_const)
    gp.update()
    await asyncio.sleep(duration)
    gp.release_button(button=button_const)
    gp.update()
    await asyncio.sleep(post_delay)


async def _press_button_ds4(gp: vg.VDS4Gamepad, button: str, duration: float, post_delay: float) -> None:
    """Preme e rilascia un bottone DS4 con gestione dpad/special."""
    btn_type, btn_val = _resolve_button_ds4(button)
    if btn_type == "button":
        gp.press_button(button=btn_val)
        gp.update()
        await asyncio.sleep(duration)
        gp.release_button(button=btn_val)
        gp.update()
    elif btn_type == "dpad":
        gp.directional_pad(direction=btn_val)
        gp.update()
        await asyncio.sleep(duration)
        gp.directional_pad(direction=vg.DS4_DPAD_DIRECTIONS.DS4_BUTTON_DPAD_NONE)
        gp.update()
    elif btn_type == "special":
        gp.press_special_button(special_button=btn_val)
        gp.update()
        await asyncio.sleep(duration)
        gp.release_special_button(special_button=btn_val)
        gp.update()
    await asyncio.sleep(post_delay)


async def _press_any_button(button: str, duration: float, post_delay: float) -> None:
    """Preme un bottone sul controller attivo (Xbox 360 o DS4)."""
    slot = _get_active()
    if slot.controller_type == "ds4":
        await _press_button_ds4(slot.gamepad, button, duration, post_delay)
    else:
        button_const = _resolve_button(button, "xbox360")
        await _press_button(button_const, duration, post_delay)


# ---------------------------------------------------------------------------
# Modelli input
# ---------------------------------------------------------------------------

class PressInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    button: str = Field(
        ...,
        description=(
            f"Tasto da premere. Valori validi Xbox: {', '.join(VALID_BUTTONS)}. "
            f"Valori validi DS4: {', '.join(DS4_VALID_BUTTONS)}. "
            "A=conferma, B=indietro/Esc, UP/DOWN/LEFT/RIGHT=D-pad, "
            "LB/RB=pagina su/giù, X=info, Y=opzioni."
        ),
    )
    duration: float = Field(
        default=DEFAULT_DURATION,
        description="Durata pressione in secondi (default 0.08). Aumentare a 0.12-0.15 su schermate lente.",
        ge=0.01,
        le=2.0,
    )
    post_delay: float = Field(
        default=DEFAULT_POST_DELAY,
        description="Pausa dopo il rilascio in secondi (default 0.06). Aumentare a 0.2-0.3 dopo navigazioni con preview 3D.",
        ge=0.0,
        le=5.0,
    )


class PressNInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    button: str = Field(
        ...,
        description=f"Tasto da premere ripetutamente. Valori validi: {', '.join(VALID_BUTTONS)}.",
    )
    n: int = Field(
        ...,
        description="Numero di pressioni consecutive.",
        ge=1,
        le=50,
    )
    duration: float = Field(
        default=DEFAULT_DURATION,
        description="Durata singola pressione in secondi (default 0.08).",
        ge=0.01,
        le=2.0,
    )
    post_delay: float = Field(
        default=DEFAULT_POST_DELAY,
        description="Pausa dopo ogni rilascio in secondi (default 0.06).",
        ge=0.0,
        le=5.0,
    )
    delay_between: float = Field(
        default=DEFAULT_BETWEEN,
        description="Pausa aggiuntiva tra una pressione e la successiva in secondi (default 0.12).",
        ge=0.0,
        le=5.0,
    )


class SequenceInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    buttons: list[str] = Field(
        ...,
        description=(
            f"Lista ordinata di tasti da premere in sequenza. "
            f"Valori validi per ciascun tasto: {', '.join(VALID_BUTTONS)}. "
            "Esempio: ['DOWN', 'DOWN', 'A'] per scendere di 2 voci e confermare."
        ),
        min_length=1,
        max_length=100,
    )
    duration: float = Field(
        default=DEFAULT_DURATION,
        description="Durata pressione per ogni tasto in secondi (default 0.08).",
        ge=0.01,
        le=2.0,
    )
    post_delay: float = Field(
        default=DEFAULT_POST_DELAY,
        description="Pausa dopo il rilascio per ogni tasto in secondi (default 0.06).",
        ge=0.0,
        le=5.0,
    )
    delay_between: float = Field(
        default=DEFAULT_BETWEEN,
        description="Pausa tra un tasto e il successivo in secondi (default 0.12).",
        ge=0.0,
        le=5.0,
    )


class StickInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    stick: str = Field(
        default="left",
        description="Stick da muovere: 'left' o 'right' (default 'left').",
    )
    x: float = Field(
        default=0.0,
        description="Valore asse X dello stick. -1.0 = tutto a sinistra, +1.0 = tutto a destra.",
        ge=-1.0,
        le=1.0,
    )
    y: float = Field(
        default=0.0,
        description="Valore asse Y dello stick. -1.0 = giù, +1.0 = su.",
        ge=-1.0,
        le=1.0,
    )
    duration: float = Field(
        default=0.1,
        description="Durata del movimento in secondi (default 0.1).",
        ge=0.01,
        le=5.0,
    )


class TriggerInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    trigger: str = Field(
        ...,
        description="Trigger da premere: 'LT' (sinistro) o 'RT' (destro).",
    )
    value: float = Field(
        default=1.0,
        description="Intensità del trigger da 0.0 a 1.0 (default 1.0 = premuto al massimo).",
        ge=0.0,
        le=1.0,
    )
    duration: float = Field(
        default=0.1,
        description="Durata pressione in secondi (default 0.1).",
        ge=0.01,
        le=5.0,
    )
    post_delay: float = Field(
        default=DEFAULT_POST_DELAY,
        description="Pausa dopo il rilascio in secondi (default 0.06).",
        ge=0.0,
        le=5.0,
    )


class ComboInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    buttons: list[str] = Field(
        default=[],
        description="Lista di bottoni da premere contemporaneamente.",
    )
    left_stick: list[float] | None = Field(
        default=None,
        description="Posizione stick sinistro [x, y] con valori da -1.0 a 1.0.",
        min_length=2,
        max_length=2,
    )
    right_stick: list[float] | None = Field(
        default=None,
        description="Posizione stick destro [x, y] con valori da -1.0 a 1.0.",
        min_length=2,
        max_length=2,
    )
    left_trigger: float | None = Field(
        default=None,
        description="Intensità trigger sinistro da 0.0 a 1.0.",
        ge=0.0,
        le=1.0,
    )
    right_trigger: float | None = Field(
        default=None,
        description="Intensità trigger destro da 0.0 a 1.0.",
        ge=0.0,
        le=1.0,
    )
    duration: float = Field(
        default=0.1,
        description="Durata della combo in secondi (default 0.1).",
        ge=0.01,
        le=5.0,
    )
    post_delay: float = Field(
        default=DEFAULT_POST_DELAY,
        description="Pausa dopo il rilascio in secondi (default 0.06).",
        ge=0.0,
        le=5.0,
    )


class HoldInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    buttons: list[str] = Field(
        default=[],
        description="Lista di bottoni da tenere premuti.",
    )
    left_stick: list[float] | None = Field(
        default=None,
        description="Posizione stick sinistro [x, y] con valori da -1.0 a 1.0.",
        min_length=2,
        max_length=2,
    )
    right_stick: list[float] | None = Field(
        default=None,
        description="Posizione stick destro [x, y] con valori da -1.0 a 1.0.",
        min_length=2,
        max_length=2,
    )
    left_trigger: float | None = Field(
        default=None,
        description="Intensità trigger sinistro da 0.0 a 1.0.",
        ge=0.0,
        le=1.0,
    )
    right_trigger: float | None = Field(
        default=None,
        description="Intensità trigger destro da 0.0 a 1.0.",
        ge=0.0,
        le=1.0,
    )


class ReleaseInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    buttons: list[str] = Field(
        default=[],
        description="Lista di bottoni da rilasciare. Se vuota, rilascia tutto (reset completo).",
    )
    reset_sticks: bool = Field(
        default=True,
        description="Se True, resetta gli stick analogici a centro (default True).",
    )
    reset_triggers: bool = Field(
        default=True,
        description="Se True, resetta i trigger a 0 (default True).",
    )


class ControllerInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    action: str = Field(
        ...,
        description="Azione: 'create' (nuovo controller), 'destroy' (rimuovi), 'select' (imposta attivo), 'list' (elenco).",
    )
    controller_type: str = Field(
        default="xbox360",
        description="Tipo controller: 'xbox360' o 'ds4' (usato solo per 'create').",
    )
    controller_id: int | None = Field(
        default=None,
        description="ID del controller (usato per 'destroy' e 'select').",
    )


class MacroStep(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    action: str = Field(
        ...,
        description="Azione: 'press', 'combo', 'stick', 'trigger', 'wait', 'hold', 'release', 'reset'.",
    )
    button: str | None = Field(default=None, description="Bottone per azione 'press'.")
    buttons: list[str] | None = Field(default=None, description="Bottoni per azioni 'combo', 'hold', 'release'.")
    x: float | None = Field(default=None, description="Asse X per stick.")
    y: float | None = Field(default=None, description="Asse Y per stick.")
    stick: str | None = Field(default=None, description="Stick: 'left' o 'right'.")
    trigger: str | None = Field(default=None, description="Trigger: 'LT' o 'RT'.")
    value: float | None = Field(default=None, description="Valore trigger (0.0-1.0).")
    duration: float | None = Field(default=None, description="Durata azione in secondi.")
    delay: float | None = Field(default=None, description="Pausa in secondi (per 'wait').")


class MacroInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    steps: list[MacroStep] = Field(
        ...,
        description="Lista di step da eseguire in sequenza.",
        min_length=1,
        max_length=200,
    )
    repeat: int = Field(
        default=1,
        description="Numero di ripetizioni della macro (default 1).",
        ge=1,
        le=50,
    )


# ---------------------------------------------------------------------------
# Helper per applicare input combo/hold sul gamepad attivo
# ---------------------------------------------------------------------------

def _apply_buttons_press(gp: Any, ct: str, buttons: list[str]) -> None:
    """Preme una lista di bottoni sul gamepad (senza update)."""
    for btn in buttons:
        key = btn.upper()
        if ct == "ds4":
            btn_type, btn_val = _resolve_button_ds4(key)
            if btn_type == "button":
                gp.press_button(button=btn_val)
            elif btn_type == "dpad":
                gp.directional_pad(direction=btn_val)
            elif btn_type == "special":
                gp.press_special_button(special_button=btn_val)
        else:
            gp.press_button(button=_resolve_button(key, "xbox360"))


def _apply_buttons_release(gp: Any, ct: str, buttons: list[str]) -> None:
    """Rilascia una lista di bottoni sul gamepad (senza update)."""
    for btn in buttons:
        key = btn.upper()
        if ct == "ds4":
            btn_type, btn_val = _resolve_button_ds4(key)
            if btn_type == "button":
                gp.release_button(button=btn_val)
            elif btn_type == "dpad":
                gp.directional_pad(direction=vg.DS4_DPAD_DIRECTIONS.DS4_BUTTON_DPAD_NONE)
            elif btn_type == "special":
                gp.release_special_button(special_button=btn_val)
        else:
            gp.release_button(button=_resolve_button(key, "xbox360"))


def _apply_sticks(gp: Any, left_stick: list[float] | None, right_stick: list[float] | None) -> None:
    """Imposta le posizioni degli stick (senza update)."""
    if left_stick is not None:
        gp.left_joystick_float(x_value_float=left_stick[0], y_value_float=left_stick[1])
    if right_stick is not None:
        gp.right_joystick_float(x_value_float=right_stick[0], y_value_float=right_stick[1])


def _apply_triggers(gp: Any, left_trigger: float | None, right_trigger: float | None) -> None:
    """Imposta i valori dei trigger (senza update)."""
    if left_trigger is not None:
        gp.left_trigger_float(value_float=left_trigger)
    if right_trigger is not None:
        gp.right_trigger_float(value_float=right_trigger)


def _reset_sticks(gp: Any) -> None:
    """Resetta entrambi gli stick a centro."""
    gp.left_joystick_float(x_value_float=0.0, y_value_float=0.0)
    gp.right_joystick_float(x_value_float=0.0, y_value_float=0.0)


def _reset_triggers(gp: Any) -> None:
    """Resetta entrambi i trigger a 0."""
    gp.left_trigger_float(value_float=0.0)
    gp.right_trigger_float(value_float=0.0)


# ---------------------------------------------------------------------------
# Tool — Controllo base
# ---------------------------------------------------------------------------

@mcp.tool(
    name="vigem_press",
    annotations={
        "title": "Premi un tasto del controller",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def vigem_press(params: PressInput) -> str:
    """Preme e rilascia un singolo tasto del controller virtuale.

    Usare per confermare (A), tornare indietro (B), navigare (UP/DOWN/LEFT/RIGHT),
    cambiare pagina (LB/RB), accedere a info (X) o opzioni (Y).

    Supporta sia Xbox 360 che DS4 in base al controller attivo.

    Args:
        params (PressInput): Parametri con:
            - button (str): Tasto da premere
            - duration (float): Durata pressione in secondi (default 0.08)
            - post_delay (float): Pausa dopo rilascio in secondi (default 0.06)

    Returns:
        str: Conferma JSON con il tasto premuto e i timing usati.
    """
    try:
        async with _get_lock():
            await _press_any_button(params.button, params.duration, params.post_delay)
            return json.dumps({
                "ok": True,
                "button": params.button.upper(),
                "duration": params.duration,
                "post_delay": params.post_delay,
            })
    except Exception as e:
        return _error_response(e)


@mcp.tool(
    name="vigem_press_n",
    annotations={
        "title": "Premi un tasto N volte consecutive",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def vigem_press_n(params: PressNInput) -> str:
    """Preme lo stesso tasto del controller per N volte consecutive.

    Utile per: navigare di N voci (DOWN x3), incrementare/decrementare uno slider
    di N step (RIGHT x5), scorrere N pagine (RB x2).

    Args:
        params (PressNInput): Parametri con:
            - button (str): Tasto da premere
            - n (int): Numero di pressioni (1-50)
            - duration (float): Durata singola pressione (default 0.08)
            - post_delay (float): Pausa dopo ogni rilascio (default 0.06)
            - delay_between (float): Pausa aggiuntiva tra pressioni (default 0.12)

    Returns:
        str: Conferma JSON con riepilogo operazione.
    """
    try:
        estimated = params.n * (params.duration + params.post_delay) + (params.n - 1) * params.delay_between
        warn = _check_duration(estimated)
        async with _get_lock():
            for i in range(params.n):
                await _press_any_button(params.button, params.duration, params.post_delay)
                if i < params.n - 1:
                    await asyncio.sleep(params.delay_between)
            result: dict = {
                "ok": True,
                "button": params.button.upper(),
                "n": params.n,
                "duration": params.duration,
                "post_delay": params.post_delay,
                "delay_between": params.delay_between,
            }
            if warn:
                result["warn"] = warn
            return json.dumps(result)
    except Exception as e:
        return _error_response(e)


@mcp.tool(
    name="vigem_sequence",
    annotations={
        "title": "Esegui una sequenza di tasti",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def vigem_sequence(params: SequenceInput) -> str:
    """Esegue una sequenza ordinata di tasti del controller virtuale.

    Il tool più potente per navigazioni composite: una sola chiamata
    può fare DOWN x2 + A, oppure B + UP + UP + A, ecc.

    Esempio: buttons=['DOWN', 'DOWN', 'A'] scende di 2 voci e conferma.

    Args:
        params (SequenceInput): Parametri con:
            - buttons (list[str]): Lista ordinata di tasti (max 100)
            - duration (float): Durata pressione per ogni tasto (default 0.08)
            - post_delay (float): Pausa dopo rilascio per ogni tasto (default 0.06)
            - delay_between (float): Pausa tra un tasto e il successivo (default 0.12)

    Returns:
        str: Conferma JSON con la sequenza eseguita.
    """
    try:
        n_seq = len(params.buttons)
        estimated = n_seq * (params.duration + params.post_delay) + (n_seq - 1) * params.delay_between
        warn = _check_duration(estimated)
        async with _get_lock():
            names = [btn.upper() for btn in params.buttons]
            for i, btn in enumerate(params.buttons):
                await _press_any_button(btn, params.duration, params.post_delay)
                if i < len(params.buttons) - 1:
                    await asyncio.sleep(params.delay_between)
            result = {
                "ok": True,
                "sequence": names,
                "count": len(names),
                "duration": params.duration,
                "post_delay": params.post_delay,
                "delay_between": params.delay_between,
            }
            if warn:
                result["warn"] = warn
            return json.dumps(result)
    except Exception as e:
        return _error_response(e)


# ---------------------------------------------------------------------------
# Tool — Stick analogico
# ---------------------------------------------------------------------------

@mcp.tool(
    name="vigem_stick",
    annotations={
        "title": "Muovi uno stick analogico",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def vigem_stick(params: StickInput) -> str:
    """Muove uno stick analogico del controller virtuale.

    Supporta stick sinistro e destro. Utile per schermate che rispondono
    meglio all'analogico che al D-pad (es. color picker, selezioni continue).
    Dopo la durata specificata, lo stick torna automaticamente a centro (0, 0).

    Args:
        params (StickInput): Parametri con:
            - stick (str): 'left' o 'right' (default 'left')
            - x (float): Asse X da -1.0 (sinistra) a +1.0 (destra) (default 0.0)
            - y (float): Asse Y da -1.0 (giù) a +1.0 (su) (default 0.0)
            - duration (float): Durata movimento in secondi (default 0.1)

    Returns:
        str: Conferma JSON con i valori usati.
    """
    try:
        async with _get_lock():
            slot = _get_active()
            gp = slot.gamepad
            stick = params.stick.lower()
            if stick == "right":
                gp.right_joystick_float(x_value_float=params.x, y_value_float=params.y)
            else:
                gp.left_joystick_float(x_value_float=params.x, y_value_float=params.y)
            gp.update()
            await asyncio.sleep(params.duration)
            if stick == "right":
                gp.right_joystick_float(x_value_float=0.0, y_value_float=0.0)
            else:
                gp.left_joystick_float(x_value_float=0.0, y_value_float=0.0)
            gp.update()
            await asyncio.sleep(DEFAULT_POST_DELAY)
            return json.dumps({
                "ok": True,
                "stick": stick,
                "x": params.x,
                "y": params.y,
                "duration": params.duration,
            })
    except Exception as e:
        return _error_response(e)


# ---------------------------------------------------------------------------
# Tool — Trigger analogico
# ---------------------------------------------------------------------------

@mcp.tool(
    name="vigem_trigger",
    annotations={
        "title": "Premi un trigger analogico",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def vigem_trigger(params: TriggerInput) -> str:
    """Preme un trigger analogico (LT o RT) con intensità e durata specificati.

    Dopo la durata, il trigger torna automaticamente a 0.

    Args:
        params (TriggerInput): Parametri con:
            - trigger (str): 'LT' (sinistro) o 'RT' (destro)
            - value (float): Intensità da 0.0 a 1.0 (default 1.0)
            - duration (float): Durata pressione in secondi (default 0.1)
            - post_delay (float): Pausa dopo rilascio in secondi (default 0.06)

    Returns:
        str: Conferma JSON con i valori usati.
    """
    try:
        async with _get_lock():
            slot = _get_active()
            gp = slot.gamepad
            trigger = params.trigger.upper()
            if trigger not in ("LT", "RT"):
                raise ValueError("Trigger deve essere 'LT' o 'RT'.")
            if trigger == "LT":
                gp.left_trigger_float(value_float=params.value)
            else:
                gp.right_trigger_float(value_float=params.value)
            gp.update()
            await asyncio.sleep(params.duration)
            if trigger == "LT":
                gp.left_trigger_float(value_float=0.0)
            else:
                gp.right_trigger_float(value_float=0.0)
            gp.update()
            await asyncio.sleep(params.post_delay)
            return json.dumps({
                "ok": True,
                "trigger": trigger,
                "value": params.value,
                "duration": params.duration,
                "post_delay": params.post_delay,
            })
    except Exception as e:
        return _error_response(e)


# ---------------------------------------------------------------------------
# Tool — Combo (input simultanei)
# ---------------------------------------------------------------------------

@mcp.tool(
    name="vigem_combo",
    annotations={
        "title": "Esegui una combinazione di input simultanei",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def vigem_combo(params: ComboInput) -> str:
    """Preme più input contemporaneamente: bottoni, stick e trigger in una sola update.

    Utile per combinazioni come LB+RB, stick+bottone, ecc.
    Dopo la durata, rilascia tutto automaticamente.

    Args:
        params (ComboInput): Parametri con:
            - buttons (list[str]): Bottoni da premere
            - left_stick (list[float] | None): [x, y] stick sinistro
            - right_stick (list[float] | None): [x, y] stick destro
            - left_trigger (float | None): Intensità trigger sinistro
            - right_trigger (float | None): Intensità trigger destro
            - duration (float): Durata combo in secondi (default 0.1)
            - post_delay (float): Pausa dopo rilascio (default 0.06)

    Returns:
        str: Conferma JSON con gli input eseguiti.
    """
    try:
        async with _get_lock():
            slot = _get_active()
            gp = slot.gamepad
            ct = slot.controller_type
            # Premi tutto contemporaneamente
            _apply_buttons_press(gp, ct, params.buttons)
            _apply_sticks(gp, params.left_stick, params.right_stick)
            _apply_triggers(gp, params.left_trigger, params.right_trigger)
            gp.update()
            await asyncio.sleep(params.duration)
            # Rilascia tutto
            _apply_buttons_release(gp, ct, params.buttons)
            _reset_sticks(gp)
            _reset_triggers(gp)
            gp.update()
            await asyncio.sleep(params.post_delay)
            return json.dumps({
                "ok": True,
                "buttons": [b.upper() for b in params.buttons],
                "left_stick": params.left_stick,
                "right_stick": params.right_stick,
                "left_trigger": params.left_trigger,
                "right_trigger": params.right_trigger,
                "duration": params.duration,
                "post_delay": params.post_delay,
            })
    except Exception as e:
        return _error_response(e)


# ---------------------------------------------------------------------------
# Tool — Hold (mantieni premuto senza rilascio)
# ---------------------------------------------------------------------------

@mcp.tool(
    name="vigem_hold",
    annotations={
        "title": "Mantieni input premuti senza rilascio",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def vigem_hold(params: HoldInput) -> str:
    """Attiva input senza rilascio automatico.

    I bottoni/stick/trigger restano attivi fino a una chiamata
    a vigem_release o vigem_reset.

    Args:
        params (HoldInput): Parametri con:
            - buttons (list[str]): Bottoni da tenere premuti
            - left_stick (list[float] | None): [x, y] stick sinistro
            - right_stick (list[float] | None): [x, y] stick destro
            - left_trigger (float | None): Intensità trigger sinistro
            - right_trigger (float | None): Intensità trigger destro

    Returns:
        str: Conferma JSON con gli input attivati.
    """
    try:
        async with _get_lock():
            slot = _get_active()
            gp = slot.gamepad
            ct = slot.controller_type
            _apply_buttons_press(gp, ct, params.buttons)
            _apply_sticks(gp, params.left_stick, params.right_stick)
            _apply_triggers(gp, params.left_trigger, params.right_trigger)
            gp.update()
            return json.dumps({
                "ok": True,
                "held_buttons": [b.upper() for b in params.buttons],
                "left_stick": params.left_stick,
                "right_stick": params.right_stick,
                "left_trigger": params.left_trigger,
                "right_trigger": params.right_trigger,
            })
    except Exception as e:
        return _error_response(e)


# ---------------------------------------------------------------------------
# Tool — Release (rilascia input)
# ---------------------------------------------------------------------------

@mcp.tool(
    name="vigem_release",
    annotations={
        "title": "Rilascia input del controller",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def vigem_release(params: ReleaseInput) -> str:
    """Rilascia i bottoni indicati e/o resetta stick e trigger.

    Se buttons è vuoto, esegue un reset completo del controller.

    Args:
        params (ReleaseInput): Parametri con:
            - buttons (list[str]): Bottoni da rilasciare (vuoto = reset totale)
            - reset_sticks (bool): Resetta stick a centro (default True)
            - reset_triggers (bool): Resetta trigger a 0 (default True)

    Returns:
        str: Conferma JSON dell'operazione.
    """
    try:
        async with _get_lock():
            slot = _get_active()
            gp = slot.gamepad
            ct = slot.controller_type
            if not params.buttons:
                gp.reset()
            else:
                _apply_buttons_release(gp, ct, params.buttons)
            if params.reset_sticks:
                _reset_sticks(gp)
            if params.reset_triggers:
                _reset_triggers(gp)
            gp.update()
            return json.dumps({
                "ok": True,
                "released_buttons": [b.upper() for b in params.buttons] if params.buttons else "all",
                "reset_sticks": params.reset_sticks,
                "reset_triggers": params.reset_triggers,
            })
    except Exception as e:
        return _error_response(e)


# ---------------------------------------------------------------------------
# Tool — Reset completo
# ---------------------------------------------------------------------------

@mcp.tool(
    name="vigem_reset",
    annotations={
        "title": "Reset completo del controller",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def vigem_reset() -> str:
    """Resetta completamente il controller attivo: rilascia tutti i bottoni,
    riporta stick e trigger a zero.

    Returns:
        str: Conferma JSON del reset.
    """
    try:
        async with _get_lock():
            slot = _get_active()
            gp = slot.gamepad
            gp.reset()
            gp.update()
            return json.dumps({"ok": True, "action": "reset"})
    except Exception as e:
        return _error_response(e)


# ---------------------------------------------------------------------------
# Tool — Gestione controller
# ---------------------------------------------------------------------------

@mcp.tool(
    name="vigem_controller",
    annotations={
        "title": "Gestisci controller virtuali",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def vigem_controller(params: ControllerInput) -> str:
    """Gestisce i controller virtuali: crea, distrugge, seleziona o elenca.

    Supporta Xbox 360 e DualShock 4 (DS4).

    Args:
        params (ControllerInput): Parametri con:
            - action (str): 'create', 'destroy', 'select', 'list'
            - controller_type (str): 'xbox360' o 'ds4' (solo per 'create')
            - controller_id (int | None): ID controller (per 'destroy' e 'select')

    Returns:
        str: Conferma JSON con risultato dell'operazione.
    """
    global _active_id, _next_id
    try:
        async with _get_lock():
            action = params.action.lower()

            if action == "create":
                ct = params.controller_type.lower()
                if ct == "ds4":
                    gp = vg.VDS4Gamepad()
                elif ct == "xbox360":
                    gp = vg.VX360Gamepad()
                else:
                    raise ValueError(f"Tipo controller '{params.controller_type}' non valido. Usa 'xbox360' o 'ds4'.")
                cid = _next_id
                _controllers[cid] = ControllerSlot(gamepad=gp, controller_type=ct)
                _next_id += 1
                return json.dumps({"ok": True, "action": "create", "controller_id": cid, "controller_type": ct})

            elif action == "destroy":
                cid = params.controller_id
                if cid is None:
                    raise ValueError("controller_id richiesto per 'destroy'.")
                if cid not in _controllers:
                    raise ValueError(f"Controller {cid} non trovato.")
                del _controllers[cid]
                if _active_id == cid:
                    _active_id = next(iter(_controllers), -1)
                return json.dumps({"ok": True, "action": "destroy", "controller_id": cid})

            elif action == "select":
                cid = params.controller_id
                if cid is None:
                    raise ValueError("controller_id richiesto per 'select'.")
                if cid not in _controllers:
                    raise ValueError(f"Controller {cid} non trovato.")
                _active_id = cid
                return json.dumps({"ok": True, "action": "select", "active_id": cid})

            elif action == "list":
                items = []
                for cid, slot in _controllers.items():
                    items.append({
                        "id": cid,
                        "type": slot.controller_type,
                        "active": cid == _active_id,
                    })
                return json.dumps({"ok": True, "action": "list", "controllers": items})

            else:
                raise ValueError(f"Azione '{params.action}' non valida. Usa 'create', 'destroy', 'select', 'list'.")
    except Exception as e:
        return _error_response(e)


# ---------------------------------------------------------------------------
# Tool — Reinizializza controller
# ---------------------------------------------------------------------------

@mcp.tool(
    name="vigem_reinit",
    annotations={
        "title": "Reinizializza il controller attivo",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def vigem_reinit() -> str:
    """Reinizializza il controller attivo ricreandolo con lo stesso ID e tipo.

    Utile se il controller entra in uno stato inconsistente.

    Returns:
        str: Conferma JSON della reinizializzazione.
    """
    try:
        async with _get_lock():
            if _active_id not in _controllers:
                raise RuntimeError("Nessun controller attivo da reinizializzare.")
            slot = _controllers[_active_id]
            ct = slot.controller_type
            if ct == "ds4":
                gp = vg.VDS4Gamepad()
            else:
                gp = vg.VX360Gamepad()
            _controllers[_active_id] = ControllerSlot(gamepad=gp, controller_type=ct)
            return json.dumps({"ok": True, "action": "reinit", "controller_id": _active_id, "controller_type": ct})
    except Exception as e:
        return _error_response(e)


# ---------------------------------------------------------------------------
# Tool — Macro
# ---------------------------------------------------------------------------

@mcp.tool(
    name="vigem_macro",
    annotations={
        "title": "Esegui una macro di input",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def vigem_macro(params: MacroInput) -> str:
    """Esegue una macro: sequenza di azioni ripetibile.

    Ogni step può essere: press, combo, stick, trigger, wait, hold, release, reset.
    La macro viene ripetuta per il numero di volte specificato.

    Args:
        params (MacroInput): Parametri con:
            - steps (list[MacroStep]): Lista step (min 1, max 200)
            - repeat (int): Ripetizioni (1-50, default 1)

    Returns:
        str: Conferma JSON con riepilogo esecuzione.
    """
    try:
        # Stima conservativa: ogni step vale almeno (duration or 0.1) + DEFAULT_POST_DELAY
        def _step_est(s: MacroStep) -> float:
            a = s.action.lower()
            if a in ("press", "combo", "stick", "trigger"):
                return (s.duration or 0.1) + DEFAULT_POST_DELAY
            if a == "wait":
                return s.delay or 0.1
            return 0.0   # hold, release, reset sono istantanei
        estimated = params.repeat * sum(_step_est(s) for s in params.steps)
        warn = _check_duration(estimated)
        async with _get_lock():
            slot = _get_active()
            gp = slot.gamepad
            ct = slot.controller_type
            total_steps = 0

            for _ in range(params.repeat):
                for step in params.steps:
                    action = step.action.lower()

                    if action == "press":
                        if not step.button:
                            raise ValueError("'press' richiede il campo 'button'.")
                        dur = step.duration or DEFAULT_DURATION
                        if ct == "ds4":
                            await _press_button_ds4(gp, step.button, dur, DEFAULT_POST_DELAY)
                        else:
                            bc = _resolve_button(step.button, "xbox360")
                            gp.press_button(button=bc)
                            gp.update()
                            await asyncio.sleep(dur)
                            gp.release_button(button=bc)
                            gp.update()
                            await asyncio.sleep(DEFAULT_POST_DELAY)

                    elif action == "combo":
                        btns = step.buttons or []
                        _apply_buttons_press(gp, ct, btns)
                        if step.x is not None or step.y is not None:
                            stick_name = (step.stick or "left").lower()
                            sx, sy = step.x or 0.0, step.y or 0.0
                            if stick_name == "right":
                                gp.right_joystick_float(x_value_float=sx, y_value_float=sy)
                            else:
                                gp.left_joystick_float(x_value_float=sx, y_value_float=sy)
                        if step.trigger and step.value is not None:
                            t = step.trigger.upper()
                            if t == "LT":
                                gp.left_trigger_float(value_float=step.value)
                            elif t == "RT":
                                gp.right_trigger_float(value_float=step.value)
                        gp.update()
                        await asyncio.sleep(step.duration or 0.1)
                        _apply_buttons_release(gp, ct, btns)
                        _reset_sticks(gp)
                        _reset_triggers(gp)
                        gp.update()
                        await asyncio.sleep(DEFAULT_POST_DELAY)

                    elif action == "stick":
                        stick_name = (step.stick or "left").lower()
                        sx, sy = step.x or 0.0, step.y or 0.0
                        if stick_name == "right":
                            gp.right_joystick_float(x_value_float=sx, y_value_float=sy)
                        else:
                            gp.left_joystick_float(x_value_float=sx, y_value_float=sy)
                        gp.update()
                        await asyncio.sleep(step.duration or 0.1)
                        if stick_name == "right":
                            gp.right_joystick_float(x_value_float=0.0, y_value_float=0.0)
                        else:
                            gp.left_joystick_float(x_value_float=0.0, y_value_float=0.0)
                        gp.update()
                        await asyncio.sleep(DEFAULT_POST_DELAY)

                    elif action == "trigger":
                        if not step.trigger:
                            raise ValueError("'trigger' richiede il campo 'trigger'.")
                        t = step.trigger.upper()
                        val = step.value if step.value is not None else 1.0
                        if t == "LT":
                            gp.left_trigger_float(value_float=val)
                        elif t == "RT":
                            gp.right_trigger_float(value_float=val)
                        else:
                            raise ValueError("Trigger deve essere 'LT' o 'RT'.")
                        gp.update()
                        await asyncio.sleep(step.duration or 0.1)
                        if t == "LT":
                            gp.left_trigger_float(value_float=0.0)
                        else:
                            gp.right_trigger_float(value_float=0.0)
                        gp.update()
                        await asyncio.sleep(DEFAULT_POST_DELAY)

                    elif action == "wait":
                        await asyncio.sleep(step.delay or 0.1)

                    elif action == "hold":
                        btns = step.buttons or []
                        _apply_buttons_press(gp, ct, btns)
                        if step.x is not None or step.y is not None:
                            stick_name = (step.stick or "left").lower()
                            sx, sy = step.x or 0.0, step.y or 0.0
                            if stick_name == "right":
                                gp.right_joystick_float(x_value_float=sx, y_value_float=sy)
                            else:
                                gp.left_joystick_float(x_value_float=sx, y_value_float=sy)
                        if step.trigger and step.value is not None:
                            t = step.trigger.upper()
                            if t == "LT":
                                gp.left_trigger_float(value_float=step.value)
                            elif t == "RT":
                                gp.right_trigger_float(value_float=step.value)
                        gp.update()

                    elif action == "release":
                        btns = step.buttons or []
                        if not btns:
                            gp.reset()
                        else:
                            _apply_buttons_release(gp, ct, btns)
                        _reset_sticks(gp)
                        _reset_triggers(gp)
                        gp.update()

                    elif action == "reset":
                        gp.reset()
                        gp.update()

                    else:
                        raise ValueError(f"Azione macro '{step.action}' non valida.")

                    total_steps += 1

            result = {
                "ok": True,
                "total_steps": total_steps,
                "repeat": params.repeat,
            }
            if warn:
                result["warn"] = warn
            return json.dumps(result)
    except Exception as e:
        return _error_response(e)


# ---------------------------------------------------------------------------
# Tool — Stato
# ---------------------------------------------------------------------------

@mcp.tool(
    name="vigem_status",
    annotations={
        "title": "Stato del controller virtuale",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def vigem_status() -> str:
    """Restituisce lo stato dei controller virtuali e i tasti disponibili.

    Utile come health check all'inizio di una sessione per verificare
    che il sistema sia inizializzato correttamente.

    Returns:
        str: JSON con stato, controller attivi, tasti disponibili e timing di default.
    """
    try:
        # Versioni dinamiche dai pacchetti installati
        try:
            vgamepad_ver = version("vgamepad")
        except Exception:
            vgamepad_ver = "sconosciuta"
        try:
            mcp_ver = version("mcp")
        except Exception:
            mcp_ver = "sconosciuta"

        controllers_info = []
        for cid, slot in _controllers.items():
            controllers_info.append({
                "id": cid,
                "type": slot.controller_type,
                "active": cid == _active_id,
            })

        return json.dumps({
            "ok": len(_controllers) > 0,
            "active_controller_id": _active_id,
            "controllers": controllers_info,
            "vgamepad_version": vgamepad_ver,
            "mcp_version": mcp_ver,
            "valid_buttons_xbox": VALID_BUTTONS,
            "valid_buttons_ds4": DS4_VALID_BUTTONS,
            "defaults": {
                "duration": DEFAULT_DURATION,
                "post_delay": DEFAULT_POST_DELAY,
                "delay_between": DEFAULT_BETWEEN,
            },
            "note": (
                "Cambio periferica durante text input annulla l'immissione. "
                "Usare pattern: controller → [fermo] → tastiera → [fermo] → controller."
            ),
        })
    except Exception as e:
        return _error_response(e)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run()
