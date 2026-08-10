# BlueNet — Decentralised Bluetooth Mesh Communication Platform

A peer-to-peer communication platform that runs entirely over Bluetooth.
No internet, no servers, no infrastructure. Just devices.

## Features

- **P2P Mesh Network** — nodes relay messages automatically; communicate even when not in direct range
- **Chat** — direct messages and group broadcasts that route through the mesh
- **BlueWeb Browser** — visit lightweight sites (`bt://ADDR/page`) hosted by peers
- **Site Publishing** — host your own pages with text, images and links
- **Offline-first** — page caching, message history (SQLite), store-and-forward routing
- **Windows + Android** — single shared core, platform-specific UI and BT adapters

---

## Architecture

```
┌──────────────────────────────────────────────────────┐
│                   Application Layer                   │
│         Windows (Tkinter)  │  Android (Kivy)          │
├──────────────────────────────────────────────────────┤
│                    Core Layer                         │
│  mesh.py  │  protocol.py  │  content.py  │  store.py  │
├──────────────────────────────────────────────────────┤
│                  Bluetooth Adapter                    │
│   windows/bt_adapter.py   │  android/bt_adapter.py   │
│     (PyBluez / RFCOMM)    │   (jnius / Java API)     │
└──────────────────────────────────────────────────────┘
```

### Mesh Routing Protocol

- **Transport**: Bluetooth Classic RFCOMM (reliable, stream-oriented)
- **Service UUID**: `94f39d29-7d6d-437d-973b-fba39e49d4ef`
- **Addressing**: Bluetooth MAC addresses (e.g. `AA:BB:CC:DD:EE:FF`)
- **Routing**: Distance-vector with flooding fallback; TTL=7 max hops
- **Deduplication**: Packet ID set with 120 s expiry prevents loops
- **Keep-alive**: 30 s ping; peers timed out after 60 s silence

### Packet Format

```json
{
  "ver":     "1.0",
  "type":    "CHAT",
  "id":      "uuid4",
  "src":     "AA:BB:CC:DD:EE:FF",
  "dst":     "11:22:33:44:55:66",
  "ttl":     7,
  "ts":      1700000000.0,
  "hops":    [],
  "payload": { "text": "Hello!" }
}
```

Packets are length-prefixed (4-byte big-endian) for reliable stream framing.

### BlueWeb Site Format

Sites are small JSON documents (`*.bweb`) with sections:

```json
{
  "blueweb": "1.0",
  "title":   "My Site",
  "author":  "AA:BB:CC:DD:EE:FF",
  "updated": 1700000000,
  "sections": [
    { "type": "header",  "text": "Hello!", "level": 1 },
    { "type": "text",    "content": "Welcome to my BlueNet site." },
    { "type": "image",   "data": "<zlib+base64>", "format": "jpeg", "alt": "Photo" },
    { "type": "link",    "text": "Visit peer", "url": "bt://AA:BB:CC:DD:EE:FF/page" },
    { "type": "divider" }
  ]
}
```

Images are resized to max 320×240 and compressed (JPEG q=50 + zlib) before embedding.

---

## Windows Setup

### Prerequisites

- Python 3.11+
- A Bluetooth adapter with Microsoft or Widcomm stack
- PyBluez requires the **Windows Bluetooth Development Kit**

```powershell
# Install dependencies
pip install -r requirements_windows.txt

# If PyBluez fails to install, try the unofficial wheel:
pip install PyBluez-win10
```

### Run

```powershell
python windows/main.py --name "MyNode"
```

---

## Android Setup

### Prerequisites

- Linux host (Ubuntu 20.04+ recommended) with buildozer
- Python 3.10, Java 11, Android SDK/NDK (buildozer downloads these)

```bash
pip install buildozer cython

# First build (downloads SDK ~2 GB, takes 10–20 min)
cd android
buildozer android debug

# Deploy to connected device
buildozer android debug deploy run
```

The APK will appear at `android/bin/bluenet-1.0.0-debug.apk`.

#### Android permissions required

- `BLUETOOTH`, `BLUETOOTH_ADMIN` — Classic Bluetooth
- `BLUETOOTH_CONNECT`, `BLUETOOTH_SCAN` — Android 12+ runtime permissions
- `ACCESS_FINE_LOCATION` — required for Bluetooth discovery on Android

Grant these when prompted on first launch or via Settings → Apps → BlueNet → Permissions.

---

## Testing Without Bluetooth Hardware

Use the included simulator to test mesh routing on a single machine:

```bash
python simulator.py --nodes 4
```

This spawns 4 virtual nodes connected in a chain (A↔B↔C↔D).
Messages from A to D route through B and C automatically.

```
> chat 0 3 Hello from Node-0 to Node-3!
> bcast 1 Broadcast from Node-1
> disconnect 1 2      # break the chain
> connect 0 3         # add direct link
```

---

## Project Structure

```
bluetooth-chat-web/
├── core/
│   ├── protocol.py      # Packet format, serialisation, compression
│   ├── mesh.py          # MeshNode: routing, forwarding, peer lifecycle
│   ├── content.py       # BlueWeb site format, SiteStore (SQLite)
│   └── store.py         # Chat message persistence (SQLite)
├── windows/
│   ├── main.py          # Tkinter Windows application
│   └── bt_adapter.py    # PyBluez RFCOMM adapter
├── android/
│   ├── main.py          # Kivy Android application
│   ├── bt_adapter.py    # Android Java BT API via jnius
│   └── buildozer.spec   # Build configuration
├── sites/
│   └── home.bweb        # Example BlueWeb site
├── simulator.py         # In-process mesh simulator for testing
├── requirements_windows.txt
└── requirements_android.txt
```

---

## Message Types

| Type         | Description                              |
|--------------|------------------------------------------|
| `HELLO`      | Announce presence + routing table (TTL 1)|
| `HELLO_ACK`  | Acknowledge + exchange routes            |
| `PING/PONG`  | Keep-alive probes                        |
| `BYE`        | Graceful disconnect notice               |
| `ROUTE_UPD`  | Broadcast updated routing table          |
| `CHAT`       | Direct message (routed via mesh)         |
| `CHAT_ACK`   | Delivery receipt                         |
| `BROADCAST`  | Flooded message to all nodes             |
| `SITE_REQ`   | Request a BlueWeb page                   |
| `SITE_RES`   | BlueWeb page content response            |

---

## Limitations & Notes

- **Range**: Bluetooth Classic typically 10–100 m per hop; mesh extends this
- **Concurrent connections**: Classic BT supports up to 7 simultaneous piconet connections
- **Bandwidth**: ~1–3 Mbit/s RFCOMM; suitable for text and small images
- **PyBluez on Windows 11**: Use `PyBluez-win10` or `pybluez2` if the standard wheel fails
- **Android 12+**: Runtime BT permissions must be accepted; location permission needed for discovery
- **Buildozer**: Build must be done on Linux or WSL2; Windows buildozer is not supported
