# lightbox_mcp: MCP server for the MLB-LHM V2.0 lightbox

Wraps the YANDING **MLB-LHM V2.0** scene lightbox (the device set of the vendor software `SW_MLB V2.7`) as an MCP server that agents can call.

| Device | Connection | Implementation |
| --- | --- | --- |
| Top light (surface light): white / infrared 850·940 nm | USB virtual serial port, 9600 8N1, address 1 | The vendor serial protocol, implemented directly in Python (`protocol.py`) |
| Up light / down light (bar lights): white / flicker | USB virtual serial port, 9600 8N1, address 2 / 3 | Same as above |
| Rail (0–1750 mm) + turntable | PLC `192.168.1.1`, through the controller's USB Ethernet adapter | Calls the vendor's `LHMGuideV2.dll` (C++ class interface) |

### The lights have two implementations, chosen by `DeviceVersion` (same rule as SW_MLB)

| `[DeviceInfo] DeviceVersion` in `LHMconfig.ini` | Class used by SW_MLB | Implementation in this project |
| --- | --- | --- |
| `V1` / `V1.1` / `V1.2` (**the lightbox PC uses V1.2**) | `CCXL_V5_Product` | Calls `CCXL_calibrate.dll` (`ccxl.py`). It scans every COM port for a controller that answers the handshake. The three lights share one connection and are told apart by address 1/2/3. Each setting sends `setlightOnOff` first, then `SetDataLevel`. **The controller replies, so a return value of 0 means the controller confirmed it received the command.** The DLL prints the frames it sends to the console |
| `V2` | `LHM_V2_Product` | The LHM serial protocol, implemented in Python (`lights.py` / `protocol.py`). Each light has its own serial port. Commands are send-only: the lights never reply |

The "V2 light protocol" section below describes the V2 serial protocol.

Everything here is based on `New folder/YANDING_SW_MLB_V2.7.rar`:

- The vendor manuals are in `docs/`: `SW_MLB_User_Manual.pdf` in English, and `SW_MLB_User_Manual_zh-CN.pdf`, the Chinese original.
- The device configuration is in `src/lightbox_mcp/LHMconfig.ini`, which is bundled into the exe.
- The DLLs used are in `src/lightbox_mcp/vendor/`.

## Running on the lightbox PC

1. Copy `dist/lightbox_mcp.exe` to the lightbox PC. The exe **has an `LHMconfig.ini` built in**, so the single file is all you need. The configuration is read in this order of priority:
   1. `--lhm-config <path>` or the environment variable `LIGHTBOX_LHM_CONFIG`
   2. An `LHMconfig.ini` in the same folder as the exe. For example, if you put the exe next to `SW_MLB.exe`, it uses the vendor software's copy
   3. The copy built into the exe

   The built-in configuration comes from the original vendor software package with one change: the `[Connect]` section sets all three lights to **COM4**. On the lightbox PC, the controller's USB cable creates two devices:
   - a serial port, COM4, shared by the three lights and told apart by address 1/2/3;
   - an Ethernet adapter, used to reach the rail PLC at `192.168.1.1`.

   When the three lights share one serial port, the program opens it only once. If your COM port is different, put an `LHMconfig.ini` next to the exe, or call `light_connect(port="COMx")`. To change the built-in configuration, edit `src/lightbox_mcp/LHMconfig.ini` and rebuild the exe.
2. Set the IPv4 address of the USB Ethernet adapter to `192.168.1.*`, as described in chapter 2 of the vendor manual (`docs/SW_MLB_User_Manual.pdf`). Without this, the rail PLC cannot be reached.
3. Close `SW_MLB.exe` first: a serial port can only be opened by one program at a time. Then double-click `lightbox_mcp.exe`. On the first run Windows Firewall shows a prompt; allow access on "Private networks".
4. The console prints the address that agents connect to, for example `http://10.68.254.107:8000/mcp`. Add it to Claude Code like this:

   ```bash
   claude mcp add --transport http mlb-lhm http://<lightbox-PC-IP>:8000/mcp
   ```

### Start-up self-check (green / red)

On start-up the exe first connects the devices, then marks the results in color in the console: green `[ OK ]`, red `[FAIL]`, yellow `[SKIP]`.

```
====================== device check ======================
[ OK ] Rail/turntable PLC 192.168.1.1: connected, registers readable
         Rail position      : 812.50 mm
         Rotation position  : 90.00 deg
         Rail ready / busy  : yes / no
         E-stop pressed     : no
[ OK ]  top light: COM4 open (9600 8N1, address 1); lights never reply, ...
[SKIP]   up light: no COM port configured (LHMconfig.ini [Connect])
         Serial ports on this PC: COM4 (0FFE:0001)
[ OK ] STATUS: CONNECTED (rail PLC + lights)
==========================================================
```

- **The rail PLC check is a real connectivity test.** If register values such as the rail position and turntable angle can be read, the connection works.
- **For the lights, only the serial port can be checked.** The lights don't reply, so the check confirms that the port opens, but not that a light is really behind it.
- After a successful self-check the connections stay open. Agents can use them directly, without calling `rail_connect` / `light_connect` again.
- Agents can call `device_check` to run the self-check again.
- `get_realtime` returns the rail position (mm) and the turntable angle (°), matching the "real-time information" area of the vendor software. The DLL cannot read the turntable's current speed; it returns error code 5.
- `--no-self-check` skips the self-check.
- `--plc-ip` changes the PLC address (default `192.168.1.1`). Both the start-up check and `device_check` use it.

Other common options: `--port 8000`, `--lhm-config <LHMconfig.ini>`, `--log-dir <directory>`, `--log-level DEBUG|INFO|WARNING`. If the agent runs on the same PC and launches the exe itself, add `--transport stdio`.

> The HTTP interface has no authentication: anyone who can reach this port can control the devices. Only run it on a trusted lab network.

## Tools

**Lights** (call `light_connect` first)

| Tool | Purpose |
| --- | --- |
| `light_list_ports` | Lists the PC's serial ports. Ports with VID/PID `104C:82CD` are flagged `likely_lhm_light` |
| `light_connect(port?, top_port?, up_port?, down_port?)` | Opens the serial ports. `port` means the three lights share one port. Without arguments it uses the COM ports in the `[Connect]` section of `LHMconfig.ini` (COM4 in the built-in configuration) |
| `light_status` | Shows the connection state, addresses, parameter ranges and the CCT flag. Also shows each light's **last parameters sent** (`last_command`): the lights can't be queried, so these are the values that were set |
| `light_set_white(light, cct, illuminance)` | Mode 1, white: colour temperature 2300–10000 K, illuminance 0–60000 |
| `light_set_infrared(ir_850, ir_940)` | Mode 2, infrared, on the top light |
| `light_set_flicker(light=up/down, frequency, duty, illuminance, cct=5000)` | Mode 3, flicker: 0–1000 Hz |
| `light_set_raw(...)` | Sends exactly what SW_MLB's `SetLightParam` sends, for cases the tools above don't cover |
| `light_off(light=all)` | Turns lights off by setting the illuminance to 0, as SW_MLB does |
| `light_disconnect` | Closes all serial ports |

**Rail and turntable.** The start-up self-check already connects them; you can also call `rail_connect`. The tools are:

- `device_check`, `get_realtime`, `rail_connect`, `rail_disconnect`
- `rail_get_status` (includes the turntable angle), `rail_get_ranges`
- `rail_move_to`: checks the range, the e-stop and the ready signal, and stops the rail if the wait times out
- `rail_stop`, `rail_home`, `rail_reset_alarms`
- `turntable_rotate`, `turntable_swing`, `turntable_stop`

## V2 light protocol (from disassembling the vendor DLLs, verified byte by byte)

Each command is an 8-byte frame. Frames are send-only: the device never replies.

```
55 | address | command | p0 | p1 | p2 | p3 | checksum = ~(sum of the first 7 bytes)
```

| Command | Meaning |
| --- | --- |
| `01` | Set the mode: p0 = 1 white / 2 infrared / 3 flicker |
| `04` | Illuminance (p0p1) + colour temperature (p2p3) |
| `02` | Illuminance only, for flicker lights without colour temperature (`CCT=0`) |
| `06` | Top light infrared: 850 nm (p0p1) + 940 nm (p2p3) |
| `F9` | Flicker light: frequency (p0p1); with `CCT=0`, p2 = 1 and p3 = duty cycle |

- For each light setting, SW_MLB sends three frames in this order: illuminance/colour temperature, wait 300 ms, infrared or frequency/duty, wait 300 ms, mode. This project does the same.
- `tests/test_protocol_vs_dll.py` redirects the vendor DLLs' output to a file and compares it with the frames that `protocol.py` builds. It covers more than 2,400 parameter combinations, and every frame matches.
- **Note**: when `LHMconfig.ini` has `[FlickerLightParamFlag] CCT=1` (the default), the vendor protocol **does not send the duty cycle**. SW_MLB behaves the same way. If the flicker duty cycle has no effect in practice, this is the reason; confirm with the vendor.

## Logs

Every line is shown in the console and written to `logs/lightbox_mcp.log` next to the exe. The log file rotates daily and is kept for 30 days:

```
[mcp]   agent 10.0.0.5 --> tools/call light_set_white {"light": "top", "cct": 6500, "illuminance": 5000}
[light] top COM24 TX 55 01 04 88 13 64 19 8D  (addr=1 illuminance=5000 cct=6500)
[light] top COM24 TX 55 01 06 00 00 00 00 A3  (addr=1 850nm=0 940nm=0)
[light] top COM24 TX 55 01 01 01 00 00 00 A7  (addr=1 mode=1)
[mcp]   agent 10.0.0.5 <-- tools/call light_set_white OK {...}
```

- `[light]` records the **raw bytes actually sent and received** on the serial port, with their decoded meaning. If a device unexpectedly sends data back, it is logged as `RX`.
- `[rail]` records every call into the rail DLL: function, arguments, return code and output values.
- `--log-level DEBUG` also records the status polling while the rail is moving.

## Project layout

| Module (`src/lightbox_mcp/`) | Responsibility |
| --- | --- |
| `server.py` | The MCP server: the 22 tools, the shared device state, `main()` |
| `cli.py` | Command-line options, which `LHMconfig.ini` to use, start-up banner |
| `middleware.py` | Logs every agent request and reply (the `[mcp]` lines) |
| `config.py` | Reads `LHMconfig.ini`: ports, addresses, ranges, `DeviceVersion` |
| `backend.py` | The interface that both light backends implement |
| `lights.py`, `protocol.py` | V2 lights: the LHM serial protocol |
| `ccxl.py` | V1.x lights: `CCXL_calibrate.dll` |
| `rail.py`, `native.py` | Rail/turntable: the `LHMGuideV2.dll` binding; DLL loading and C++ export lookup |
| `motion.py` | Waiting for rail moves and homing to finish |
| `selfcheck.py` | The start-up self-check and `device_check` |
| `logs.py` | Console/file logging and tracing of DLL calls |
| `errors.py` | Errors reported to the agent |

## Development

```bash
uv sync --group dev
uv run pytest                                # unit tests; the byte-for-byte check against the vendor DLLs needs Windows x64
uv run python tests/smoke_client.py          # end-to-end test (lights use the loop:// loopback port, no hardware; the rail part needs the vendor DLLs)
uv sync --group build
uv run --group build python build_exe.py     # builds dist/lightbox_mcp.exe
```

Formatting and linting (PEP 8, 120 columns; see `pyproject.toml` and `.flake8`):

```bash
uvx black --check src build_exe.py packaging
uvx ruff check src build_exe.py packaging
uvx flake8 src build_exe.py packaging
```

`DMT.dll` is a Debug build and depends on `vcruntime140d.dll` and `ucrtbased.dll`. Both files come from the vendor's software package; they are in `vendor/` and bundled into the exe. Microsoft's license does not allow the debug runtime libraries to be redistributed, so the exe is for internal use only.
