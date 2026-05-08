# COC Auto Farm Bot

A **thread-safe** automation bot for Clash of Clans farming on LDPlayer emulator. Uses YOLO object detection and EasyOCR to automatically search for bases, read resource values, and initiate attacks.

## Features

- **Automated Farming**: Searches for bases, reads resource quantities (Gold, Elixir, Dark Elixir), and attacks when thresholds are met
- **Thread-Safe GUI**: Tkinter interface communicates with bot worker via queue (no crashes from threading)
- **Graceful Stop**: Press **F12** to stop farming cleanly at any time
- **Retry Logic**: Up to 4 attempts per detection to handle timing issues
- **Defensive Programming**: Safe `.get()` defaults, OCR error handling, focus fallback tricks
- **Logging**: File + console logs to `logs/bot.log`

## Requirements

### Software
- **Python 3.8+**
- **LDPlayer emulator** (window title must contain "LDPlayer")
- **NVIDIA GPU** (recommended for YOLO inference; CPU mode supported)

### Python Dependencies
See `requirements.txt` for full list:
- `ultralytics` (YOLO)
- `easyocr` (OCR)
- `pywinauto` (Window automation)
- `pyautogui` (Mouse/keyboard)
- `keyboard` (F12 hotkey)
- `opencv-python` (Image processing)
- `numpy` (Arrays)

## Installation

### 1. Clone the Repository
```bash
git clone https://github.com/anugrhaswi/Coc-Auto-Farm.git
cd Coc-Auto-Farm
```

### 2. Create Virtual Environment
```bash
python -m venv venv
venv\Scripts\activate  # Windows
# or
source venv/bin/activate  # macOS/Linux
```

### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

### 4. Obtain Model
- Place your `best.pt` YOLO model at the root: `cocbot/best.pt`
- Or update `config.py` with the correct path

## Usage

### Start the Bot
```bash
python main.py
```

This launches a Tkinter GUI with:
- **Thresholds**: Minimum resources to look for (e.g., 200k Gold, 200k Elixir)
- **Goals**: Total resources to farm before stopping (e.g., 5M Gold, 5M Elixir)
- **Start/Stop buttons**: Launch or cancel the farm
- **Log viewer**: Real-time farming activity
- **F12 hotkey**: Emergency stop

### Configuration
Edit `config.py` to adjust:
- `window_title_re`: LDPlayer window regex (default: `".*LDPlayer.*"`)
- `conf`: YOLO confidence threshold (default: 0.5)
- `gpu`: Use GPU for inference (default: True)
- `max_retries`: Retry attempts per detection (default: 4)
- `model_path`: Path to YOLO model (default: `"best.pt"`)

## Architecture

### Modules

| Module | Purpose |
|--------|---------|
| `main.py` | Entry point; wires all components together |
| `config.py` | Configuration dataclass |
| `window_manager.py` | `WindowManager`: LDPlayer connection, focus, screenshot capture |
| `vision.py` | `VisionManager`: YOLO detection, EasyOCR reading |
| `automation.py` | `AutomationController`: Thin wrapper around pyautogui |
| `bot.py` | `CocBot`: Farming brain; runs in worker thread, checks `_stop_event` every sleep |
| `gui.py` | `BotGUI`: Tkinter interface on main thread; drains queue every 150ms |
| `logger.py` | `setup_logging()`: File + console logging |

### Thread Model
- **Main thread**: GUI (Tkinter), queue polling, keyboard hotkey
- **Worker thread**: Farming loop (bot), window automation, vision inference
- **Communication**: Thread-safe `queue.Queue` (no `root.after()` from worker thread)

## Key Design Decisions

### Why Thread-Safe Queue?
The original code crashed with `RuntimeError: main thread is not in main loop` when the worker thread called `root.after()`. The queue-based approach is the official Python solution: worker puts messages, main thread polls and updates GUI.

### Why Retry Loop?
Game timing is unpredictable. Bases load asynchronously, buttons appear on delay. Retrying up to 4x with 0.5–1s waits handles this robustly.

### Why Retry Inside VisionManager Returns Frame-Relative Coords?
`vision.detect()` returns coordinates relative to the screenshot frame (0,0 origin). The bot layer adds `window.win_left` and `window.win_top` to convert to screen coordinates for clicking. This separation keeps vision pure (no window knowledge).

### Why `.get()` Defaults?
If OCR fails or detection misses a resource, using `.get(key, 0)` prevents KeyErrors. Graceful degradation over crashes.

## Known Limitations

- **Windows only**: Uses `pywinauto` (Windows API)
- **Emulator-only**: Designed for LDPlayer; other emulators may have different window titles
- **Single strategy**: Only supports goblin-based farming; dragon/other strategies not yet implemented
- **YOLO model required**: You must provide a trained `best.pt` model with your game's object classes

## Troubleshooting

### "Failed to connect to LDPlayer window"
- Ensure LDPlayer is running
- Window title must contain "LDPlayer" (check in your emulator settings)
- Update `config.window_title_re` if needed

### "Failed to load YOLO model"
- Verify `best.pt` exists at the path in `config.model_path`
- Ensure the model is a valid YOLOv8 `.pt` file

### "Could not find [class_name] after X attempts"
- YOLO may be under-confident; lower `config.conf` (e.g., 0.4)
- Ensure LDPlayer is focused and unobstructed
- Check bot logs in `logs/bot.log`

### High memory usage
- YOLO and EasyOCR are memory-intensive
- Close other applications
- Enable GPU mode for faster inference and lower CPU usage

## Development

### Running Tests
```bash
# No formal test suite yet; run the GUI and test manually
python main.py
```

### Extending
- To add a new farming strategy, subclass `CocBot` or add a new method to `_farm_loop()`
- To add humanization (mouse jitter, random delays), extend `AutomationController`
- To support other emulators, update `window_manager.py` connection logic

## License

MIT License — See `LICENSE` file.

## Author

**Anugrha Bhujel** ([GitHub](https://github.com/anugrhaswi))

---

## Journey

This bot is the result of iterative learning:
1. **Window Management**: Failed with `pygetwindow`, succeeded with `pywinauto` + fallback focus trick
2. **OCR Reliability**: Added safe defaults to handle empty OCR results
3. **Detection Retries**: Built max-4-attempt loop to handle asynchronous game loading
4. **Threading**: Fixed `RuntimeError` by replacing `root.after()` with queue-based messaging
5. **Architecture**: Modularized globals into focused classes for maintainability and testability

See the original `main.ipynb` in the `backup/` folder for the development history.
