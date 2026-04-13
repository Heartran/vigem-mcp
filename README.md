# vigem_mcp

Server MCP locale per controllare un gamepad Xbox 360 virtuale via VigEm.
Risolve il problema di `Esc` con computer use: invece di mandare il tasto
da tastiera (che sgancia la sessione), Claude chiama `vigem_press` con `button="B"`.

## Requisiti

- Windows 10/11
- VigEm Bus driver installato (già presente sul sistema, v1.17.333.0)
- Python 3.14
- Pacchetti: `vgamepad`, `mcp[cli]`

## Installazione

```bash
pip install vgamepad "mcp[cli]"
```

Metti `vigem_server.py` in una cartella comoda, es.:

```
C:\Users\Federico\OneDrive\Universe\vigem_mcp\vigem_server.py
```

## Configurazione Claude Desktop

Aggiungi questo blocco al file `claude_desktop_config.json`
(di solito in `%APPDATA%\Claude\claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "vigem": {
      "command": "python",
      "args": ["C:\\Users\\Federico\\OneDrive\\Universe\\vigem_mcp\\vigem_server.py"]
    }
  }
}
```

Riavvia Claude Desktop dopo aver salvato.

## Tool disponibili

| Tool | Cosa fa |
|---|---|
| `vigem_press` | Preme e rilascia un singolo tasto |
| `vigem_press_n` | Preme lo stesso tasto N volte |
| `vigem_sequence` | Esegue una sequenza ordinata di tasti |
| `vigem_stick` | Muove lo stick analogico sinistro |
| `vigem_status` | Health check — verifica che il gamepad sia attivo |

## Tasti validi

`A`, `B`, `X`, `Y`, `UP`, `DOWN`, `LEFT`, `RIGHT`, `LB`, `RB`, `START`, `BACK`

Mappatura in gioco:
- `A` = Conferma / Seleziona
- `B` = Indietro / Esc
- `UP/DOWN/LEFT/RIGHT` = D-pad navigazione / slider
- `LB/RB` = Pagina su / Pagina giù
- `X` = Info
- `Y` = Opzioni

## Esempi d'uso (da Claude)

```
vigem_press(button="B")                          # torna indietro
vigem_press(button="A")                          # conferma
vigem_press_n(button="DOWN", n=3)                # scende di 3 voci
vigem_sequence(buttons=["DOWN","DOWN","A"])       # scende 2 + conferma
vigem_press_n(button="RIGHT", n=5)               # slider +5
vigem_status()                                   # health check
```

## Regola critica: cambio periferica

Il gioco accetta una sola periferica attiva alla volta.
Passare da controller a tastiera (o viceversa) durante l'immissione
annulla l'operazione e torna al menu precedente.

Pattern corretto per text input:
```
vigem_sequence(["DOWN","DOWN"])   ← navigazione con controller
# --- pausa, nessun input VigEm ---
[tastiera: digita il testo + Invio]
# --- pausa, nessun input tastiera ---
vigem_press("A")                  ← riprende con controller
```
