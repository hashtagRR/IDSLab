# Installing IDSLab

Step-by-step guides from the download to the dashboard, one per system. Each takes about ten minutes,
most of it spent downloading libraries (a few hundred MB).

- [Windows](#windows)
- [macOS](#macos)
- [GNU/Linux](#gnulinux)
- [From the source code](#from-the-source-code)
- [Optional extras](#optional-extras)
- [Tests and checks](#tests-and-checks)
- [Troubleshooting on every system](#troubleshooting-on-every-system)

What you need: Python 3.10 or newer (3.12 is the safest choice: every optional library has prebuilt
packages for it), about 1 GB of free disk space, and an internet connection for the install. No
compiler, no administrator rights after Python is installed.

The guides install IDSLab from [PyPI](https://pypi.org/project/idslab/) into a folder of its own;
the source code is only needed to change IDSLab itself ([from the source code](#from-the-source-code)).

---

## Windows

All commands below are typed in **PowerShell**. On Windows the virtual environment's programs live in
`.venv\Scripts` (not `.venv\bin`), and PowerShell runs a program from a path only with a leading `.\`,
so every command starts with `.\.venv\Scripts\`.

### 1. Install Python

1. Go to [python.org/downloads/windows](https://www.python.org/downloads/windows/) and download the
   **Windows installer (64-bit)** of Python 3.12.
2. Run it. Keep the default options; they include the **py launcher** that the commands below use.
   Ticking "Add python.exe to PATH" is optional.
3. Open PowerShell (Start menu, type `PowerShell`, Enter) and check:

   ```powershell
   py --list
   ```

   You should see a line with `3.12`. If PowerShell says `py` is not recognized, run the installer
   again, choose **Modify**, and tick **py launcher**.

### 2. Make a folder for IDSLab

In the same PowerShell window:

```powershell
mkdir $HOME\idslab
cd $HOME\idslab
```

This is the **IDSLab folder**: the program, the example and your results live here. Every command
below runs in it. (Any other plain local folder works too; avoid OneDrive and network folders.)

### 3. Create the virtual environment

```powershell
py -3.12 -m venv .venv
dir .venv\Scripts\python.exe
```

The second command must list `python.exe`. If `.venv` was created but has a `bin` folder instead of
`Scripts`, a non-Windows Python (MSYS2, Git Bash, Cygwin) made it: delete the `.venv` folder and run
the `py -3.12` line again.

### 4. Install IDSLab

```powershell
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\pip install idslab
```

The second line downloads IDSLab with scikit-learn, pandas and the other libraries; it takes a few
minutes. Then check:

```powershell
.\.venv\Scripts\idslab --version
```

It prints `idslab 0.3.0` (or a newer number).

### 5. Create the demo folder (do not skip)

The dashboard always works inside one folder, its **workspace**: it lists the configs, data and
results found there and saves new files there. That folder must exist before the dashboard starts.
For a first try, let IDSLab create one called `demo`, with example data and three ready designs:

```powershell
.\.venv\Scripts\idslab example demo
```

It prints `wrote demo\data\train.parquet, demo\data\test.parquet and r1_router_gate.yaml, ...` and
creates this inside the IDSLab folder:

```text
demo\
  data\train.parquet, data\test.parquet    synthetic network flows
  r1_router_gate.yaml                      three reference designs
  r1_four_layer.yaml
  r2_anomaly_first.yaml
```

You only do this once. If you skip it, the next step stops with
`workspace folder demo does not exist`.

### 6. Open the dashboard

Still in the IDSLab folder (the one that now contains `demo`):

```powershell
.\.venv\Scripts\idslab dashboard --workspace demo --open
```

- It prints `IDSLab dashboard for ...\demo at http://127.0.0.1:8765/ (Ctrl+C to stop)` and opens that
  address in your browser. If no browser opens, open one and go to **http://127.0.0.1:8765/**.
- **Keep the PowerShell window open** while you use the dashboard. Pressing **Ctrl+C** in it, or
  closing it, stops the dashboard and any run it started.
- The page lists the three designs. For a first run, click **Continue setup** on `r1-router-gate`,
  then **Run** on the Review & run step. Each of its three seeds takes about a minute; the Jobs page
  shows the progress, and **View results** opens the result when it ends.
- `demo` is a path relative to the folder PowerShell is in. For your own data, make a folder, copy
  your Parquet or CSV files into it, and start the dashboard with that folder, for example
  `--workspace C:\data\my-ids`. The [getting started guide](docs/getting-started.md#your-own-data)
  explains the rest.
- It only listens on this computer, so Windows does not ask for firewall access.

### 7. A run from the command line (optional)

The same run without the dashboard, as it would go in a script:

```powershell
.\.venv\Scripts\idslab run demo\r1_router_gate.yaml --out demo\runs
```

Progress lines show what it is doing. When it finishes, open `demo\runs\r1-router-gate\report.html`
(double-click it in File Explorer). The dashboard shows runs from the command line too, and the two
reuse each other's finished seeds.

### 8. Next time

Open PowerShell and go to the IDSLab folder; `demo` from step 5 is still there:

```powershell
cd $HOME\idslab
.\.venv\Scripts\idslab dashboard --workspace demo --open
```

To type plain `idslab` instead of `.\.venv\Scripts\idslab`, activate the environment once per window
with `.\.venv\Scripts\Activate.ps1`. If PowerShell answers "running scripts is disabled on this
system", allow local scripts for your user once with
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, then activate again. In Command Prompt
(`cmd.exe`) use `.venv\Scripts\activate.bat` instead.

### 9. Update or remove

- **Update:** `.\.venv\Scripts\pip install --upgrade idslab` in the IDSLab folder. Your configs,
  data and results stay where they are. A new version runs finished experiments once more before
  their reports can be rebuilt (the [changelog](CHANGELOG.md) says when).
- **Remove:** delete the IDSLab folder. Nothing was installed anywhere else (Python itself stays;
  remove it in Settings, Apps if you want).

### Windows troubleshooting

| What you see | Cause and fix |
|---|---|
| `workspace folder demo does not exist` (older versions: `workspace demo is not a folder`) | Step 5 has not run in this folder, or PowerShell is in another folder. Go to the IDSLab folder (`cd $HOME\idslab`), run `.\.venv\Scripts\idslab example demo`, then start the dashboard again. |
| `The module '.venv' could not be loaded` | The command is missing the leading `.\`, or uses `bin` instead of `Scripts`: type `.\.venv\Scripts\idslab ...` |
| `py : The term 'py' is not recognized` | The py launcher is missing: rerun the python.org installer, **Modify**, tick **py launcher**. |
| `python` opens the Microsoft Store | Windows' store placeholder; use `py -3.12`, or turn the placeholder off in Settings, Apps, Advanced app settings, App execution aliases. |
| `.venv\Scripts` does not exist | Step 3 has not run in this folder, or a non-Windows Python made a `.venv\bin`: delete `.venv` and run `py -3.12 -m venv .venv`. |
| `No matching distribution found for idslab` | The environment's Python is older than 3.10: delete `.venv` and create it again with `py -3.12`. |
| `running scripts is disabled on this system` | Only `Activate.ps1` needs scripts; use the full `.\.venv\Scripts\...` commands, or see step 8. |
| The browser does not open | Go to http://127.0.0.1:8765/ yourself while the PowerShell window still runs the dashboard. |
| The page cannot be reached | The dashboard is not running (window closed or Ctrl+C), or it uses another port: read the address it printed. |
| `[WinError 10048] Only one usage of each socket address` | Port 8765 is taken, often by a dashboard in another window. Close it, or start with `--port 8766` (or `--port 0` for any free port). |
| Windows asks for firewall access | The dashboard was started with `--host 0.0.0.0` or another address. With the default 127.0.0.1 no prompt appears. |
| Everything is slow | Antivirus scanning, or the IDSLab folder inside OneDrive or a network folder. Use a plain local folder such as `C:\idslab`. |
| `pip` tries to build a package from source and fails | Your Python is newer than that library's packages; use Python 3.12. |
| Errors about paths longer than 260 characters | Use a shorter IDSLab folder such as `C:\idslab`, or enable long paths in Windows. |

---

## macOS

All commands below are typed in **Terminal** (Applications, Utilities, Terminal). They work on Apple
silicon and Intel Macs.

### 1. Install Python

Check what you have:

```bash
python3 --version
```

If it prints 3.10 or newer, you can use it. If it is older, or macOS offers to install the "command
line developer tools", install Python 3.12 one of these ways:

- **From python.org:** download the **macOS 64-bit universal2 installer** of Python 3.12 from
  [python.org/downloads/macos](https://www.python.org/downloads/macos/) and run it. Then, in Finder,
  open Applications, Python 3.12 and double-click **Install Certificates.command** once.
- **With Homebrew** (if you use it): `brew install python@3.12`

Check again; the python.org and Homebrew installs both provide `python3.12`:

```bash
python3.12 --version
```

### 2. Make a folder for IDSLab

```bash
mkdir ~/idslab
cd ~/idslab
```

This is the **IDSLab folder**: the program, the example and your results live here, and every command
below runs in it.

### 3. Create the virtual environment

```bash
python3.12 -m venv .venv
```

(Use `python3` instead of `python3.12` if your system Python is already 3.10 or newer.)

### 4. Install IDSLab

```bash
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install idslab
.venv/bin/idslab --version
```

The last line prints `idslab 0.3.0` (or a newer number).

### 5. Create the demo folder (do not skip)

The dashboard always works inside one folder, its **workspace**, which must exist before it starts.
For a first try, let IDSLab create `demo` with example data and three ready designs:

```bash
.venv/bin/idslab example demo
```

It writes `demo/data/train.parquet`, `demo/data/test.parquet` and three designs
(`r1_router_gate.yaml`, `r1_four_layer.yaml`, `r2_anomaly_first.yaml`) inside the IDSLab folder. You
only do this once; without it the next step stops with `workspace folder demo does not exist`.

### 6. Open the dashboard

```bash
.venv/bin/idslab dashboard --workspace demo --open
```

It opens **http://127.0.0.1:8765/** in your default browser. Keep the Terminal window open while you
use it; **Ctrl+C** in Terminal stops it. For a first run, click **Continue setup** on
`r1-router-gate`, then **Run** on the Review & run step (about a minute per seed). For your own data,
make a folder with your Parquet or CSV files and pass it as `--workspace`.

### 7. A run from the command line (optional)

```bash
.venv/bin/idslab run demo/r1_router_gate.yaml --out demo/runs
open demo/runs/r1-router-gate/report.html
```

The same run without the dashboard; `open` shows the report in your browser.

### 8. Next time

```bash
cd ~/idslab
.venv/bin/idslab dashboard --workspace demo --open
```

Or activate the environment once per Terminal window with `source .venv/bin/activate` and type plain
`idslab`.

### 9. Update or remove

- **Update:** `.venv/bin/pip install --upgrade idslab` in the IDSLab folder; your files stay.
- **Remove:** delete the IDSLab folder.

### macOS troubleshooting

| What you see | Cause and fix |
|---|---|
| `xcrun: error: invalid active developer path` | The command line developer tools are missing: `xcode-select --install`. |
| `python3.12: command not found` | Python 3.12 is not installed (step 1), or use `python3` if it is 3.10 or newer. |
| `SSL: CERTIFICATE_VERIFY_FAILED` during the install | Run **Install Certificates.command** in Applications, Python 3.12. |
| `Library not loaded: @rpath/libomp.dylib` with XGBoost or LightGBM | They need OpenMP: `brew install libomp`. |
| `Address already in use` for the dashboard | Another program uses port 8765: start with `--port 0` and open the address it prints. |
| `zsh: no matches found: idslab[xgboost]` | Quote extras in zsh: `pip install "idslab[xgboost]"`. |

---

## GNU/Linux

All commands below are typed in a terminal.

### 1. Install Python and the tools

Check the Python version:

```bash
python3 --version
```

It must be 3.10 or newer. Install Python's virtual environment support with your distribution's
package manager:

| Distribution | Command |
|---|---|
| Debian, Ubuntu, Linux Mint | `sudo apt update && sudo apt install python3 python3-venv python3-pip` |
| Fedora | `sudo dnf install python3 python3-pip` |
| Arch, Manjaro | `sudo pacman -S python python-pip` |
| openSUSE | `sudo zypper install python3 python3-pip` |

If your distribution's Python is older than 3.10 (for example Ubuntu 20.04), install a newer one with
[pyenv](https://github.com/pyenv/pyenv) or, on Ubuntu, the `deadsnakes` archive, and use that
`python3.12` in step 3.

### 2. Make a folder for IDSLab

```bash
mkdir ~/idslab
cd ~/idslab
```

This is the **IDSLab folder**; every command below runs in it.

### 3. Create the virtual environment

```bash
python3 -m venv .venv
```

### 4. Install IDSLab

```bash
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install idslab
.venv/bin/idslab --version
```

### 5. Create the demo folder (do not skip)

The dashboard always works inside one folder, its **workspace**, which must exist before it starts.
For a first try, let IDSLab create `demo` with example data and three ready designs:

```bash
.venv/bin/idslab example demo
```

It writes `demo/data/train.parquet`, `demo/data/test.parquet` and three designs
(`r1_router_gate.yaml`, `r1_four_layer.yaml`, `r2_anomaly_first.yaml`) inside the IDSLab folder. You
only do this once; without it the next step stops with `workspace folder demo does not exist`.

### 6. Open the dashboard

```bash
.venv/bin/idslab dashboard --workspace demo --open
```

It opens **http://127.0.0.1:8765/** in your browser. Keep the terminal open; **Ctrl+C** stops it. For
a first run, click **Continue setup** on `r1-router-gate`, then **Run** on the Review & run step
(about a minute per seed). For your own data, make a folder with your Parquet or CSV files and pass it
as `--workspace`.

**On a server without a desktop:** leave out `--open`, and from your own computer forward the port over
SSH, then open http://127.0.0.1:8765/ in your local browser:

```bash
ssh -L 8765:127.0.0.1:8765 you@server
```

Do not start the dashboard with `--host 0.0.0.0` on a shared network: it runs commands and has no
login.

### 7. A run from the command line (optional)

```bash
.venv/bin/idslab run demo/r1_router_gate.yaml --out demo/runs
xdg-open demo/runs/r1-router-gate/report.html
```

The same run without the dashboard; `xdg-open` shows the report in your browser.

### 8. Next time

```bash
cd ~/idslab
.venv/bin/idslab dashboard --workspace demo --open
```

Or `source .venv/bin/activate` once per terminal and type plain `idslab`.

### 9. Update or remove

- **Update:** `.venv/bin/pip install --upgrade idslab` in the IDSLab folder; your files stay.
- **Remove:** delete the IDSLab folder.

### GNU/Linux troubleshooting

| What you see | Cause and fix |
|---|---|
| `The virtual environment was not created successfully because ensurepip is not available` | Install the venv package: `sudo apt install python3-venv` (or `python3.12-venv`). |
| `error: externally-managed-environment` | `pip` was run outside the virtual environment; use `.venv/bin/pip`. |
| `xdg-open: not found` or no browser opens | Open the file or http://127.0.0.1:8765/ in a browser yourself. |
| `Address already in use` | Another program uses port 8765: start with `--port 0` and use the address it prints. |
| A run is killed with no message | Out of memory: use `protocol.subsample` or fewer parallel `--jobs`. |

---

## From the source code

Only needed to change IDSLab itself or to run its tests. Clone the repository (or download the ZIP
from the green **Code** button on GitHub and extract it), then install it in place, with the
development tools, inside the project folder (the one with `pyproject.toml`):

```bash
git clone https://github.com/hashtagRR/IDSLab.git
cd IDSLab
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

On Windows, use `py -3.12 -m venv .venv` and `.\.venv\Scripts\pip`. Changes to the source then take
effect without reinstalling. To try the newest unreleased code without a clone,
`pip install "git+https://github.com/hashtagRR/IDSLab.git"` works in any virtual environment.

## Optional extras

| Extra       | Installs          | Use it for                                                    |
|-------------|-------------------|---------------------------------------------------------------|
| `xgboost`   | xgboost           | `xgboost.XGBClassifier` in a stage                            |
| `lightgbm`  | lightgbm          | `lightgbm.LGBMClassifier` in a stage                          |
| `pyod`      | pyod              | PyOD detectors (`pyod.models.*`)                              |
| `imbalance` | imbalanced-learn  | resampling steps such as `imblearn.over_sampling.SMOTE`       |
| `torch`     | torch             | `idslab.torch_models.TorchMLPClassifier`, `TorchAutoencoder`  |
| `dev`       | pytest, ruff, pyright, build, twine | tests and checks                            |

Install them in the IDSLab folder, with the quotes:

| System | Command |
|---|---|
| Windows | `.\.venv\Scripts\pip install "idslab[xgboost,pyod]"` |
| macOS, GNU/Linux | `.venv/bin/pip install "idslab[xgboost,pyod]"` |

(From the source code, use `pip install -e ".[xgboost,pyod]"` in the project folder instead.)

A config that names a class from a missing extra fails when it is loaded, before any data is read,
with the install command in the message; the dashboard's builder lists such models as "needs an
extra". On macOS, XGBoost and LightGBM also need `brew install libomp`.

**PyTorch without an NVIDIA GPU:** on Windows and GNU/Linux the CPU build is much smaller; install it
before the `torch` extra:

```bash
pip install torch --index-url https://download.pytorch.org/whl/cpu
```

(On macOS the regular `pip install torch` is already the right build.)

## Tests and checks

For changes to IDSLab itself, in a copy of the source code ([from the source code](#from-the-source-code)):

```bash
.venv/bin/pip install -e ".[dev,xgboost,pyod,imbalance,torch]"
.venv/bin/pytest                                  # about two minutes
.venv/bin/ruff check src tests examples
.venv/bin/ruff format --check src tests examples
.venv/bin/pyright
```

On Windows replace `.venv/bin/` with `.\.venv\Scripts\`.

## Troubleshooting on every system

| Message | Fix |
|---|---|
| `workspace folder demo does not exist` | the dashboard's `--workspace` folder must exist: run `idslab example demo` first (step 5), or name a folder that holds your data; the path is relative to the folder the terminal is in |
| `cannot import module 'xgboost' (install it with: pip install idslab[xgboost])` | install the named extra |
| `Encode: non-numeric columns not listed in 'categorical'` | add the text columns to the `categorical` list of `idslab.prep.Encode` in that stage's pipeline (the dashboard's Data step does this from your data) |
| `train and test columns differ` | the two files need the same columns; drop extras with `dataset.drop` |
| `row ids are not unique` | point `dataset.id_column` at a unique column, or remove it to use row numbers |
| a run seems idle | progress lines appear as it works; with `--jobs` above 1 the worker processes do not log |
| the CPU stays busy after Stop in the dashboard | Stop ends the job and the worker processes it started; a busy CPU after that is another program |

Paths inside a config always use forward slashes (`data/train.parquet`) on every system, and are
relative to the config file.
