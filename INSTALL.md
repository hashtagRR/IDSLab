# Installing IDSLab

IDSLab needs Python 3.10 or newer and runs on Linux, macOS and Windows.

## From PyPI

```bash
pip install idslab
```

## From a clone

```bash
git clone https://github.com/hashtagRR/IDSLab
cd IDSLab
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/idslab --version
```

On Windows (PowerShell), the virtual environment's programs live in `Scripts`:

```powershell
py -3 -m venv .venv
.venv\Scripts\pip install -e .
.venv\Scripts\idslab --version
```

## Optional extras

| Extra       | Installs          | Use it for                                                    |
|-------------|-------------------|---------------------------------------------------------------|
| `xgboost`   | xgboost           | `xgboost.XGBClassifier` in a stage                            |
| `lightgbm`  | lightgbm          | `lightgbm.LGBMClassifier` in a stage                          |
| `pyod`      | pyod              | PyOD detectors (`pyod.models.*`)                              |
| `imbalance` | imbalanced-learn  | resampling steps such as `imblearn.over_sampling.SMOTE`       |
| `torch`     | torch             | `idslab.torch_models.TorchMLPClassifier`, `TorchAutoencoder`  |
| `dev`       | pytest, ruff, pyright, build, twine | tests and checks                            |

```bash
pip install "idslab[xgboost,pyod]"
```

A config that names a class from a missing extra fails when it is loaded, before any data is read,
with the install command in the message. For PyTorch on a machine without a GPU, the CPU wheel is much
smaller: `pip install torch --index-url https://download.pytorch.org/whl/cpu`.

## Tests and checks

```bash
pip install -e ".[dev,xgboost,pyod,imbalance,torch]"
pytest                                  # about two minutes
ruff check src tests examples
ruff format --check src tests examples
pyright
```

## Troubleshooting

| Message | Fix |
|---|---|
| `cannot import module 'xgboost' (install it with: pip install idslab[xgboost])` | install the named extra |
| `Encode: non-numeric columns not listed in 'categorical'` | add the text columns to the `categorical` list of `idslab.prep.Encode` in that stage's pipeline |
| `train and test columns differ` | the two files need the same columns; drop extras with `dataset.drop` |
| `row ids are not unique` | point `dataset.id_column` at a unique column, or remove it to use row numbers |
| the dashboard says the port is in use | start it with `--port 0` for any free port |
| a run seems idle | progress goes to stderr; with `--jobs` above 1 the workers do not log |
