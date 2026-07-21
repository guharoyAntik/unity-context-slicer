# Publishing & Distribution Guide

This guide covers how to build, publish, and run **Unity Context Slicer** via PyPI, `uvx`, and `pipx`.

---

## 1. Local Build

To build the source distribution (`.tar.gz`) and wheel (`.whl`):

```bash
pip install build
python -m build
```

The output artifacts will be created in the `dist/` directory.

---

## 2. Running via `uvx` or `pipx` (Zero Install)

Once published to PyPI, users can run the server directly without installing Python dependencies manually:

### Using `uvx` (Fastest)
```bash
uvx unity-context-slicer --project-dir "path/to/UnityProject"
```

### Using `pipx`
```bash
pipx run unity-context-slicer --project-dir "path/to/UnityProject"
```

---

## 3. Publishing to PyPI

### Option A: Manual Upload via Twine
```bash
pip install twine
twine upload dist/*
```

### Option B: Automated via GitHub Actions (Recommended)
This repository includes a pre-configured GitHub Action workflow at `.github/workflows/publish.yml`.

1. Set up **Trusted Publishing** on PyPI for `unity-context-slicer`.
2. Create a GitHub Release.
3. The workflow will automatically build and publish the package to PyPI.
